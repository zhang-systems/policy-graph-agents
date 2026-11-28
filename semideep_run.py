# Semi-deep KiL (08Q): "Before each tool call, the agent retrieves the graph nodes relevant to the
# current dialogue state and must cite which preconditions hold; generation is conditioned on that
# check." The one difference from the validator (06Q): the graph shapes the plan BEFORE the call;
# nothing checks or blocks the call afterward (no policy_checker, no veto).
#
# At EVERY agent step (a step may end in a tool call or a reply, so checking every step is the only
# way to check before each tool call), three calls to the same agent model (Qwen3-8B):
#   1. plan      the agent says what it will do next and which graph action(s) the current dialogue
#                state is about: the customer's request, even when the next step is only a lookup
#                (or "none" when no action is in play yet)
#   2. check     code retrieves those actions' nodes from airline_policy_graph.json (conditions with
#                their OR / AND groups, ask-first items, global rules, required order); the agent
#                must cite each one: holds / does not hold / unknown, with its evidence. A missing
#                item is asked for once more ("must cite").
#   3. act       the normal agent call, with the plan and the check added as the agent's current
#                planning state. Only the latest check is in the context; every check is logged.
# Steps 1-2 are text-only calls (tool_choice "none"), so they never reach the database.
# Prompt = wiki.md as in the baseline; the shallow checklist is NOT added.
# Then qwen_run.py runs as for every Qwen arm (simulator seed per trial, deep_run.py unchanged).
import json
import os
import re
import runpy
import sys
import threading

import litellm
import graph_text as gt

G = gt.load()
ACTIONS = {a["action"]: a for a in G["actions"]}
PLAN_LOG = os.environ.get("PLAN_LOG", "plans.jsonl")
VERDICTS = ("holds", "does not hold", "unknown")
_lock = threading.Lock()
_local = threading.local()                       # one task per worker thread (tau-bench ThreadPool)


# ---------------------------------------------------------------- graph retrieval
def _alt(c):
    """One line for a condition; OR / AND groups list their alternatives inline."""
    if "any_of" in c or "all_of" in c:
        kind, subs = ("ANY ONE of", c["any_of"]) if "any_of" in c else ("ALL of", c["all_of"])
        return f"{kind}: " + "; ".join(f"({chr(97 + j)}) {_alt(s)}" for j, s in enumerate(subs))
    txt = f"{c['condition']} [{c['source']}]"
    if "must_happen_before" in c:
        txt += f" (must be done before {c['must_happen_before']})"
    return txt


def nodes_for(actions):
    """The graph nodes the check must cite, as (id, text), for a list of graph actions (empty =
    no action in play): global rules that apply everywhere or to one of the actions, then per
    action what to know first and its conditions. Condition ids follow policy_checker's rule ids
    ("<action>/c<k>"), so every verdict can later be compared with the code checker."""
    if isinstance(actions, str):
        actions = [] if actions == "none" else [actions]
    items = [(f"G/{r['source']}", f"{r['condition']} [{r['source']}]") for r in G["global_rules"]
             if "applies_to" not in r or set(actions) & set(r["applies_to"])]
    for action in actions:
        a = ACTIONS[action]
        items += [(f"{action}/info{k}", f"{action}: known from the user or the tools: {i['info']} [{i['source']}]")
                  for k, i in enumerate(a.get("needs_info", []))]
        items += [(f"{action}/c{k}", f"{action}: {_alt(c)}") for k, c in enumerate(a["conditions"])]
    return items


# ---------------------------------------------------------------- prompts
def plan_prompt():
    names = ", ".join(ACTIONS)
    return ("[Planning step: write for yourself, the customer does not see this.] "
            "Before you act, state your plan. Answer in exactly two lines:\n"
            f"ACTION: the action(s) from this list that the customer's current request is about, even if "
            f"your next step is only a lookup: {names}. Use commas for more than one; write none if no "
            "action is in play yet\n"
            "PLAN: one sentence: what you will do next and why")


def check_prompt(plan, action, items):
    rules = "\n".join(f"{n}. {t}" for n, (_, t) in enumerate(items, 1))
    where = f"for {action}" if action != "none" else "that apply to every conversation"
    return ("[Policy check: write for yourself, the customer does not see this.] "
            f"Your plan: {plan}\nThese are the policy graph's rules {where}:\n{rules}\n\n"
            "Check every rule against this conversation and the tool results so far. For EACH rule, "
            "write one short line exactly as: <number> | holds / does not hold / unknown | <evidence in "
            "at most 12 words: the tool value or what the customer said; for unknown, what is missing>. "
            "For an ANY ONE group, say which option holds. No table, no heading, nothing else.\n"
            "Example:\n1 | holds | user id mia_li_3668 found by get_user_details\n"
            "2 | unknown | customer has not said yes yet")


def act_note(plan, action, items, verdicts):
    lines = "\n".join(f"{n}. {t} -> {verdicts[i][0]} ({verdicts[i][1]})"
                      for n, (i, t) in enumerate(items, 1))
    return ("[Your planning state for this step: your own plan and policy check, not from the "
            f"customer.]\nPlan: {plan}\nAction in the policy graph: {action}\n"
            + (f"Check:\n{lines}\n" if items else "")
            + "Now make your next step follow this check. If a rule does not hold, do not do the "
              "action: tell the customer it cannot be done and which rule fails. If a rule is "
              "unknown, look it up with a tool or ask the customer first. Write your next message "
              "to the customer or make your next tool call.")


# ---------------------------------------------------------------- parsing
def parse_plan(text):
    """-> (comma-joined graph actions, or "none"; None if a name is not a graph action), plan"""
    text = text or ""
    m = re.search(r"ACTION:\s*([^\n]+)", text)
    names = [x.strip(" .`*'\"") for x in (m.group(1).split(",") if m else [])]
    names = [x for x in names if x]
    p = re.search(r"PLAN:\s*(.+)", text)
    plan = p.group(1).strip() if p else text.strip()[:300]
    if names == ["none"]:
        return "none", plan
    ok = names and all(x in ACTIONS for x in names)
    return (",".join(dict.fromkeys(names)) if ok else None), plan


_VERDICT = re.compile(r"does\s*n[o']?t\s+hold|not\s+hold|not\s+met|unmet|unknown|unclear|holds?|met|"
                      r"\btrue\b|\bfalse\b|\byes\b|\bno\b", re.I)


def _verdict(word):
    w = word.lower()
    if w in ("unknown", "unclear"):
        return "unknown"
    if any(x in w for x in ("not", "n't", "unmet")) or w in ("false", "no"):
        return "does not hold"
    return "holds"


def parse_check(text, n_items):
    """Lenient: '3 | holds | ...', '| 3 | Holds | ... |' (a table), '3. Holds - ...', 'Rule 3: does not
    hold (...)', '**3** | unknown | ...'. The first number on the line is the rule; the first verdict
    word after it is the verdict; the rest is the evidence."""
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"[\s|*#>\-]*(?:rule\s*)?(\d+)\b[\s.):*|\-]*(.*)", line, re.I)
        if not m or not (1 <= int(m.group(1)) <= n_items) or int(m.group(1)) in out:
            continue
        v = _VERDICT.search(m.group(2))
        if not v:
            continue
        ev = m.group(2)[v.end():].strip(" |:-–—*()").strip()
        out[int(m.group(1))] = (_verdict(v.group(0)), ev[:200])
    return out


# ---------------------------------------------------------------- the per-step hook
def _side(kwargs, note, max_tokens):
    """Text-only call to the same agent model; never a tool call, never reaches the database."""
    kw = dict(kwargs)
    kw["messages"] = list(kwargs["messages"]) + [{"role": "user", "content": note}]
    kw["tool_choice"] = "none"
    kw["max_tokens"] = max_tokens
    return (litellm.completion(**kw).choices[0].message.content or "").strip()


def semideep(*args, **kwargs):
    # litellm.completion is looked up at call time, so deep_run.py's routing (agent -> vLLM)
    # and call log are used for all three calls
    step = getattr(_local, "step", 0) + 1
    _local.step = step
    row = {"task_id": getattr(_local, "task", None), "step": step}

    text = _side(kwargs, plan_prompt(), 120)
    action, plan = parse_plan(text)
    if action is None:                           # must name a graph action: ask once more
        text = _side(kwargs, plan_prompt() + "\nThe ACTION line must be one of the names above.", 120)
        action, plan = parse_plan(text)
        row["plan_retry"] = True
    action = action or "none"

    items = nodes_for([] if action == "none" else action.split(","))
    verdicts = {}
    if items:
        raw = _side(kwargs, check_prompt(plan, action, items), 600)
        got = parse_check(raw, len(items))
        row["check_raw"] = raw[:1200]                # Qwen's own words, to audit the parser
        miss = [n for n in range(1, len(items) + 1) if n not in got]
        if miss:                                 # "must cite": ask once for the missing ones
            more = _side(kwargs, check_prompt(plan, action, items)
                         + f"\nYou did not answer rules {', '.join(map(str, miss))}. Answer every rule.", 600)
            got.update({k: v for k, v in parse_check(more, len(items)).items() if k not in got})
            row["check_retry"] = miss
        verdicts = {i: got.get(n, ("unknown", "not cited")) for n, (i, _) in enumerate(items, 1)}

    kw = dict(kwargs)
    kw["messages"] = list(kwargs["messages"]) + [{"role": "user", "content": act_note(plan, action, items, verdicts)}]
    res = litellm.completion(*args, **kw)
    m = res.choices[0].message
    calls = m.tool_calls or []
    row.update(action=action, plan=plan, check={i: v for i, v in verdicts.items()},
               next="tool:" + calls[0].function.name if calls else "reply")
    with _lock, open(PLAN_LOG, "a") as f:
        f.write(json.dumps(row) + "\n")
    return res


# ---------------------------------------------------------------- install, then run as every Qwen arm
def install():
    import tau_bench.agents.tool_calling_agent as agent_mod
    from tau_bench.envs.base import Env
    real_reset = Env.reset

    def reset(self, task_index=None):
        _local.task, _local.step = task_index, 0            # plans.jsonl rows know their task
        return real_reset(self, task_index)

    Env.reset = reset
    agent_mod.completion = semideep


if __name__ == "__main__":
    install()
    here = os.path.dirname(os.path.abspath(__file__))
    sys.argv[0] = os.path.join(here, "qwen_run.py")
    runpy.run_path(os.path.join(here, "qwen_run.py"), run_name="__main__")
