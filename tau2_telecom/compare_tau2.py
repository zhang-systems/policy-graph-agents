# Compare the tau2-bench telecom arms (baseline, validator, shallow KiL) from saved results only.
# No API calls. Same statistics as the airline notebook 10: pass^k per task (tau-bench formula),
# 95% bootstrap CI over tasks, paired differences over the same tasks.
import random
import re
from collections import Counter
from math import comb

import pandas as pd

ARMS = ["baseline", "validator", "shallow"]
ISSUES = ["mms_issue", "service_issue", "mobile_data_issue"]
BLOCK_TAG = "BLOCKED by policy validator"


# ------------------------------------------------------------------ task id parts
def issue(task_id):
    return task_id.split("]")[0].strip("[")


def persona(task_id):
    m = re.search(r"PERSONA:(\w+)", task_id)
    return m.group(1) if m else "None"


def n_faults(task_id):
    """Number of problems planted on the phone / account, e.g. airplane_mode_on|bad_vpn -> 2."""
    return len(task_id.split("]")[1].split("[")[0].split("|"))


# ------------------------------------------------------------------ one row per dialogue
def auto_category(ri, end):
    """A simple first guess of why a failed dialogue failed (checked by hand later)."""
    if end == "max_steps":
        return "ran out of steps"
    if end == "too_many_errors":
        return "too many tool errors"
    acts = ri.get("action_checks") or []
    if any(a["action"]["requestor"] == "assistant" and not a["action_match"] for a in acts):
        return "agent action missing or wrong"
    if any(a["action"]["requestor"] == "user" and not a["action_match"] for a in acts):
        return "user fix not done"
    return "phone state not reached"


def dialogue_rows(arm, res, replay=None):
    """res = Results from tau2 (pydantic). replay(sim, task) -> dict of executed violations by rule."""
    tasks = {t.id: t for t in res.tasks}
    out = []
    for s in res.simulations:
        ri = s.reward_info.model_dump() if s.reward_info else {}
        end = getattr(s.termination_reason, "value", s.termination_reason)
        acts = ri.get("action_checks") or []
        need_user = [a for a in acts if a["action"]["requestor"] == "user"]
        need_agent = [a for a in acts if a["action"]["requestor"] == "assistant"]
        failed = [e["env_assertion"]["func_name"] for e in (ri.get("env_assertions") or []) if not e["met"]]
        msgs = s.messages or []
        row = {
            "arm": arm, "task": s.task_id, "trial": s.trial,
            "issue": issue(s.task_id), "persona": persona(s.task_id), "faults": n_faults(s.task_id),
            "passed": (ri.get("reward") or 0) >= 1 - 1e-6, "end": end,
            "agent msgs": sum(m.role == "assistant" for m in msgs),
            "agent tool calls": sum(len(m.tool_calls or []) for m in msgs if m.role == "assistant"),
            "user tool calls": sum(len(m.tool_calls or []) for m in msgs if m.role == "user"),
            "user fixes needed": len(need_user), "user fixes done": sum(a["action_match"] for a in need_user),
            "agent actions needed": len(need_agent), "agent actions done": sum(a["action_match"] for a in need_agent),
            "failed checks": ", ".join(failed),
            "blocked": sum(1 for m in msgs if m.role == "tool" and BLOCK_TAG in str(m.content)),
            "blocked rules": ", ".join(r for m in msgs if m.role == "tool" and BLOCK_TAG in str(m.content)
                                       for r in re.findall(r"\[(\w\d)\]", str(m.content))),
        }
        row["category"] = "" if row["passed"] else auto_category(ri, end)
        if replay:
            v = replay(s, tasks[s.task_id])
            row["violations executed"] = sum(v.values())
            row["violated rules"] = ", ".join(f"{r} x{n}" for r, n in sorted(v.items()))
        out.append(row)
    return pd.DataFrame(out)


def make_replay(make_env, violations, checked):
    """Replay each dialogue's tool calls in a fresh environment and count the calls that broke a
    validator rule and were really executed (calls the validator rejected are skipped, as in tau2)."""
    def replay(sim, task):
        env = make_env()
        env.set_state(initialization_data=task.initial_state.initialization_data,
                      initialization_actions=task.initial_state.initialization_actions, message_history=[])
        status = {m.id: m.execution_status for m in sim.messages if m.role == "tool"}
        found = Counter()
        for m in sim.messages:
            for tc in (m.tool_calls or []) if m.role in ("assistant", "user") else []:
                if status.get(tc.id) == "rejected":
                    continue
                if tc.requestor == "assistant" and tc.name in checked:
                    for rule, _ in violations(env.tools, tc.name, tc.arguments or {}):
                        found[rule] += 1
                try:
                    env.get_response(tc)
                except Exception:
                    pass
        return dict(found)
    return replay


# ------------------------------------------------------------------ pass^k and bootstrap
def task_counts(df):
    g = df.groupby("task")["passed"].agg(["sum", "count"])
    return {t: (int(c), int(n)) for t, (c, n) in g.iterrows()}


def passk_tasks(counts, k):
    return {t: comb(c, k) / comb(n, k) for t, (c, n) in counts.items() if n >= k}


def curve(df, max_k=8, reps=2000, seed=0):
    counts = task_counts(df)
    tasks = sorted(counts)
    rng = random.Random(seed)
    samples = [[rng.choice(tasks) for _ in tasks] for _ in range(reps)]
    rows = []
    for k in range(1, max_k + 1):
        per = passk_tasks(counts, k)
        boot = sorted(sum(per[t] for t in s) / len(s) for s in samples)
        rows.append({"k": k, "pass^k": sum(per.values()) / len(per),
                     "ci_low": boot[int(0.025 * reps)], "ci_high": boot[int(0.975 * reps) - 1]})
    return pd.DataFrame(rows)


def paired(df_a, df_b, ks=(1, 2, 4, 8), reps=2000, seed=0):
    """Δ = arm a − arm b over the same tasks, 95% bootstrap CI; * = CI excludes 0."""
    ca, cb = task_counts(df_a), task_counts(df_b)
    tasks = sorted(set(ca) & set(cb))
    rng = random.Random(seed)
    samples = [[rng.choice(tasks) for _ in tasks] for _ in range(reps)]
    out = {}
    for k in ks:
        pa, pb = passk_tasks(ca, k), passk_tasks(cb, k)
        d = {t: pa[t] - pb[t] for t in tasks}
        boot = sorted(sum(d[t] for t in s) / len(s) for s in samples)
        lo, hi = boot[int(0.025 * reps)], boot[int(0.975 * reps) - 1]
        out[k] = (sum(d.values()) / len(d), lo, hi, lo > 0 or hi < 0)
    return out


def fmt_delta(x):
    d, lo, hi, sig = x
    return f"{d:+.3f} [{lo:+.3f}, {hi:+.3f}]" + (" *" if sig else "")


def consistency(df):
    c = df.groupby("task")["passed"].agg(["sum", "count"])
    return {"always pass": int((c["sum"] == c["count"]).sum()), "never pass": int((c["sum"] == 0).sum()),
            "sometimes": int(((c["sum"] > 0) & (c["sum"] < c["count"])).sum())}


# ------------------------------------------------------------------ hand-check export
def pick_failed(df, arm, n=5, seed=0):
    """n failed dialogues of one arm, spread over the three issue types, at most one per task."""
    f = df[(df.arm == arm) & ~df.passed].sample(frac=1, random_state=seed)
    picked, tasks = [], set()
    while len(picked) < n:
        added = False
        for iss in ISSUES:                       # round robin over issue types
            rows = f[(f.issue == iss) & ~f.task.isin(tasks)]
            if len(rows) and len(picked) < n:
                picked.append(rows.iloc[0]); tasks.add(rows.iloc[0].task); added = True
        if not added:
            break
    return pd.DataFrame(picked)


def transcript_md(sim, row, max_tool=300):
    lines = [f"# {row['arm']} | trial {row['trial']} | {row['task']}", "",
             f"- **Result:** {'PASS' if row['passed'] else 'FAIL'} ; ends: {row['end']} ; auto category: {row['category']}",
             f"- **Failed checks:** {row['failed checks'] or '-'}",
             f"- **User fixes done:** {row['user fixes done']} / {row['user fixes needed']} ; "
             f"**agent actions done:** {row['agent actions done']} / {row['agent actions needed']}",
             f"- **Blocked by validator:** {row['blocked']} {('(' + row['blocked rules'] + ')') if row['blocked'] else ''}",
             "", "---", ""]
    for m in sim.messages or []:
        who = {"assistant": "AGENT", "user": "USER", "tool": "TOOL"}.get(m.role, m.role.upper())
        if m.role == "tool":
            txt = str(m.content or "")
            lines.append(f"> **{who}** ({getattr(m, 'requestor', '')}): {txt[:max_tool]}{' …' if len(txt) > max_tool else ''}")
        else:
            if m.content:
                lines.append(f"**{who}:** {m.content}")
            for tc in m.tool_calls or []:
                lines.append(f"**{who} → `{tc.name}`** `{tc.arguments}`")
        lines.append("")
    return "\n".join(lines)
