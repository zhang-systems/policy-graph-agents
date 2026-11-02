# summarize one tau-bench result file
# API errors are not real failures, so I leave them out of pass^1
# I also show how each task ended, to spot loops
import json, sys, textwrap

rows = sorted(json.load(open(sys.argv[1])), key=lambda r: r["task_id"])
err = [r for r in rows if "error" in (r.get("info") or {})]
ok = [r for r in rows if r not in err]
passed = sum(r["reward"] == 1 for r in ok)

def ended(traj):
    # 30 steps = about 60+ messages: it ran out of steps (a loop)
    if len(traj) >= 60:
        return "STEP LIMIT"
    last = (traj[-1].get("content") or "") if traj else ""
    if "###STOP###" in last:
        return "user stop"
    # the agent ended it, e.g. by handing the user to a human
    return "agent end"

print(f"runs {len(rows)} | API errors {len(err)} | valid {len(ok)} | passed {passed}")
if ok:
    print(f"pass^1 = {passed / len(ok):.2f}")
print()
print(f"{'task':>4}  {'result':6}  {'msgs':>4}  {'tools':>5}  ended")
print("-" * 38)
for r in ok:
    traj = r["traj"]
    tools = sum(len(m.get("tool_calls") or []) for m in traj if m.get("role") == "assistant")
    res = "pass" if r["reward"] == 1 else "fail"
    print(f"{r['task_id']:>4}  {res:6}  {len(traj):>4}  {tools:>5}  {ended(traj)}")
for r in err:
    print(f"\nerror task {r['task_id']}:")
    print(textwrap.fill(r["info"]["error"], 120))
