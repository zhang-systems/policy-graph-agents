# Count policy violations in saved tau-bench runs (secondary metric).
# I replay every tool call of a dialogue on a fresh copy of the database,
# and check each write call with policy_checker BEFORE it runs.
# Works for any arm: a call the validator blocked is counted as "blocked",
# a bad call that really ran is counted as "executed".
import json
from collections import Counter

import policy_checker as pc
from tau_bench.envs.airline.data import load_data
from tau_bench.envs.airline.tools import ALL_TOOLS

TOOLS = {t.get_info()["function"]["name"]: t for t in ALL_TOOLS}
BLOCK_TAG = "Error: blocked by the policy validator"

# tasks where tau-bench's answer key itself breaks the written policy
# (found by running the checker on the ground-truth actions, see 06)
LABEL_CONFLICT = [10, 16, 19, 26, 27, 34, 45, 46]


def ran_calls(traj):
    """(name, kwargs, observation) for every tool call that got a tool reply, in order."""
    obs = {m["tool_call_id"]: m["content"] for m in traj if m.get("role") == "tool"}
    out = []
    for m in traj:
        for c in (m.get("tool_calls") or []) if m.get("role") == "assistant" else []:
            if c["id"] in obs:
                try:
                    kw = json.loads(c["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    kw = None
                out.append((c["function"]["name"], kw, obs[c["id"]]))
    return out


def replay(traj, graph):
    """Violations in one dialogue: list of dicts, plus a count of replay mismatches."""
    data, hist, found, mismatch = load_data(), [], [], 0
    for name, kw, obs in ran_calls(traj):
        if name not in TOOLS or not isinstance(kw, dict):
            continue
        blocked = str(obs).startswith(BLOCK_TAG)
        if name in pc.WRITE_TOOLS:
            for v in pc.check(name, kw, data, hist, graph):
                found.append({**v, "tool": name, "status": "blocked" if blocked else "executed"})
        if blocked:
            continue                                  # the validator did not run it, so I don't either
        try:
            out = TOOLS[name].invoke(data=data, **kw)
        except Exception as e:
            out = f"Error: {e}"
        if str(out) != str(obs):
            mismatch += 1                             # replay drifted from the real run
        if name in pc.WRITE_TOOLS and not str(out).startswith("Error"):
            hist.append({"name": name, "kwargs": kw})
    return found, mismatch


def count(tab, graph):
    """tab = passk.table(...) -> per-dialogue rows and totals."""
    rows, by_source, mism = [], Counter(), 0
    for trial, tasks in sorted(tab.items()):
        for t, r in sorted(tasks.items()):
            v, m = replay(r.get("traj") or [], graph)
            mism += m
            for x in v:
                for s in x["sources"]:
                    by_source[(s, x["status"])] += 1
            rows.append({"trial": trial, "task": t, "passed": r["reward"] >= 1 - 1e-6,
                         "executed": sum(x["status"] == "executed" for x in v),
                         "blocked": sum(x["status"] == "blocked" for x in v)})
    return rows, by_source, mism
