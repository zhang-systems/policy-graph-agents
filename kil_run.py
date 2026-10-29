# KiL arms on Gemini. Same agent, model and settings as 05G (gemini_run.py runs them).
# KIL_MODE picks how the policy graph reaches the agent (no validator in either arm):
#   "shallow"  (07): the whole graph, as text rules, is added to the system prompt once.
#   "semideep" (08): the graph is used step by step. When the agent is about to call a
#                    write tool, the rules for that one action are retrieved from the graph
#                    and shown to it; it must check each rule against the conversation and
#                    the tool results, then either repeat the call or not call it.
import json
import os
import runpy
import sys
import threading

import litellm
import graph_text as gt

MODE = os.environ["KIL_MODE"]
assert MODE in ("shallow", "semideep"), MODE
G = gt.load()
WRITE_TOOLS = {"book_reservation", "update_reservation_flights", "update_reservation_baggages",
               "update_reservation_passengers", "cancel_reservation", "send_certificate"}
CHECK_LOG = os.environ.get("CHECK_LOG", "kil_checks.jsonl")
_lock = threading.Lock()

if MODE == "shallow":
    # the airline env reads WIKI when it is built, so I extend it before run.main()
    import tau_bench.envs.airline.env as airline_env
    airline_env.WIKI = airline_env.WIKI + "\n\n" + gt.full_text(G)

if MODE == "semideep":
    # one tool can be two graph actions (change flights, or only the cabin)
    ACTIONS = {"update_reservation_flights": ["update_reservation_flights", "upgrade_cabin"]}

    def rules_for(tool):
        names = ACTIONS.get(tool, [tool])
        head = ("whichever of these fits the call" if len(names) > 1 else "")
        return (head + "\n\n" if head else "") + "\n\n".join(gt.action_text(G, n) for n in names)

    def check_prompt(call):
        name = call.function.name
        return (
            f"[Policy check before acting] You are about to call {name} with "
            f"{call.function.arguments}. These are the rules for this action, from the policy graph:\n\n"
            f"{rules_for(name)}\n\n"
            "Go through the rules one by one. For each, say in one short line whether it is met, "
            "not met, or unknown, using only this conversation and the tool results so far. "
            "Then: if every rule is met, make the same tool call again (fix the arguments if needed). "
            "If a rule is not met or unknown, do not call the tool; ask the user or explain the policy.")

    import tau_bench.agents.tool_calling_agent as agent_mod

    def semideep(*args, **kwargs):
        # litellm.completion is looked up at call time, so gemini_run.py's backend
        # and logging (patched later) are used for both calls
        res = litellm.completion(*args, **kwargs)
        msg = res.choices[0].message
        calls = msg.tool_calls or []
        if not calls or calls[0].function.name not in WRITE_TOOLS:
            return res
        first = calls[0]
        kw = dict(kwargs)
        kw["messages"] = list(kwargs["messages"]) + [{"role": "user", "content": check_prompt(first)}]
        res2 = litellm.completion(*args, **kw)
        m2 = res2.choices[0].message
        c2 = (m2.tool_calls or [None])[0]
        row = {"tool": first.function.name,
               "decision": "repeat" if c2 and c2.function.name == first.function.name else
                           ("other_tool" if c2 else "no_call"),
               "args_changed": bool(c2 and c2.function.name == first.function.name
                                    and c2.function.arguments != first.function.arguments)}
        with _lock, open(CHECK_LOG, "a") as f:
            f.write(json.dumps(row) + "\n")
        return res2

    agent_mod.completion = semideep

# same agent, model and backend as 05G
here = os.path.dirname(os.path.abspath(__file__))
runpy.run_path(os.path.join(here, "gemini_run.py"), run_name="__main__")
