# 10: compare every arm and run the evaluation the project proposal asks for.
#   - pass^k for k = 1..8 (curve shape, drop from pass^1 to pass^8)
#   - violations split into three types:
#       (a) illegal tool calls        data rules, found by the 06G checker
#       (b) dialogue-level violations G1 no explicit "yes", G3 reply + tool call in one turn,
#                                     B5 booking without asking about insurance,
#                                     R3 compensation offered before the user asked
#                                     (simple text checks, confirmed by the hand-check)
#       (c) reasoning / argument errors in failed dialogues: missing action, wrong arguments,
#                                     extra action, missing output
#   - failed dialogues split into the tau-bench paper's categories: wrong decision,
#     partially resolve, wrong argument, wrong info
#   - violations by turn position (early / middle / late third of the agent's turns)
#   - violations on dialogues that still PASS (what final-state scoring hides)
#   - 30 failed dialogues exported for a hand-check (agent or simulator?)
import json
import os
import random
import re
from math import comb

import passk
import policy_checker as pc
import violations as V
from tau_bench.envs.airline.data import load_data

N_TASKS, N_TRIALS = 50, 8
CLEAN = [t for t in range(N_TASKS) if t not in V.LABEL_CONFLICT]
GEMINI = [("baseline", "results_gemini_baseline"), ("validator", "results_gemini_validator"),
          ("shallow", "results_gemini_shallow"), ("semi-deep", "results_gemini_semideep")]
QWEN = [("qwen base", "results_deep_qwen_base"), ("qwen sft", "results_deep_qwen_sft"),
        ("qwen deep", "results_deep_qwen_deep"), ("qwen validator", "results_deep_qwen_validator")]
ARMS = GEMINI + QWEN
MODEL_OF = {a: ("gemini" if (a, d) in GEMINI else "qwen") for a, d in ARMS}

G1_TOOLS = {"book_reservation", "update_reservation_flights", "update_reservation_baggages",
            "update_reservation_passengers"}       # G1's 5 actions (upgrade_cabin = update_reservation_flights)
YES = re.compile(r"\b(yes|yeah|yep|sure|ok|okay|confirm\w*|go ahead|proceed|correct|that'?s right|please do|do it)\b", re.I)
ASK_COMP = re.compile(r"compensat|refund|voucher|certificate|reimburs|make up for|credit", re.I)


# ------------------------------------------------------------ loading and pass^k
def full_table(root, d):
    """{trial: {task: row}} with only the complete trials."""
    tab = passk.table(os.path.join(root, d))
    return {t: v for t, v in tab.items() if len(v) == N_TASKS}


def counts(tab):
    return passk.success_counts(tab, range(N_TASKS))


def pass_k(c, k, tasks=None):
    tasks = tasks if tasks is not None else list(range(N_TASKS))
    return sum(comb(c[t], k) / comb(N_TRIALS, k) for t in tasks) / len(tasks)


def delta(c_new, c_old, tasks, k, boot=2000, seed=0):
    est = pass_k(c_new, k, tasks) - pass_k(c_old, k, tasks)
    rng = random.Random(seed)
    s = []
    for _ in range(boot):
        pick = [rng.choice(tasks) for _ in tasks]
        s.append(pass_k(c_new, k, pick) - pass_k(c_old, k, pick))
    s.sort()
    lo, hi = s[int(0.025 * boot)], s[int(0.975 * boot) - 1]
    return est, lo, hi


# ------------------------------------------------------------ one dialogue
def _norm(kw):
    return json.dumps(kw, sort_keys=True)


def analyze(row, graph):
    """Everything 10 needs from one dialogue."""
    traj = row.get("traj") or []
    obs = {m["tool_call_id"]: m["content"] for m in traj if m.get("role") == "tool"}
    assistant_idx = [i for i, m in enumerate(traj) if m.get("role") == "assistant"]
    n_turns = max(len(assistant_idx), 1)
    pos_of = {i: j / n_turns for j, i in enumerate(assistant_idx)}     # 0 = first agent turn, ~1 = last

    data, hist = load_data(), []
    data_viol, executed = [], []
    dlg_viol = []
    asked_insurance = False
    user_asked_comp = False
    last_user = ""
    for i, m in enumerate(traj):
        role = m.get("role")
        if role == "user":
            last_user = m.get("content") or ""
            if ASK_COMP.search(last_user):
                user_asked_comp = True
            continue
        if role != "assistant":
            continue
        text = m.get("content") or ""
        if "insurance" in text.lower():
            asked_insurance = True
        calls = m.get("tool_calls") or []
        if not calls:
            continue
        if text.strip():
            dlg_viol.append({"rule": "G3", "pos": pos_of[i]})
        c = calls[0]
        name = c["function"]["name"]
        try:
            kw = json.loads(c["function"]["arguments"] or "{}")
        except json.JSONDecodeError:
            continue
        if c["id"] not in obs or name not in V.TOOLS or not isinstance(kw, dict):
            continue
        blocked = str(obs[c["id"]]).startswith(V.BLOCK_TAG)
        if name in pc.WRITE_TOOLS:
            for v in pc.check(name, kw, data, hist, graph):
                data_viol.append({"sources": v["sources"], "tool": name, "pos": pos_of[i],
                                  "status": "blocked" if blocked else "executed"})
        if blocked:
            continue
        try:
            out = V.TOOLS[name].invoke(data=data, **kw)
        except Exception as e:
            out = f"Error: {e}"
        if name in pc.WRITE_TOOLS and not str(out).startswith("Error"):
            hist.append({"name": name, "kwargs": kw})
            executed.append((name, _norm(kw)))
            if name in G1_TOOLS and not YES.search(last_user):
                dlg_viol.append({"rule": "G1", "pos": pos_of[i]})
            if name == "book_reservation" and not asked_insurance:
                dlg_viol.append({"rule": "B5", "pos": pos_of[i]})
            if name == "send_certificate" and not user_asked_comp:
                dlg_viol.append({"rule": "R3", "pos": pos_of[i]})

    passed = row["reward"] >= 1 - 1e-6
    labels, category = [], None
    if not passed:
        ri = ((row.get("info") or {}).get("reward_info") or {}).get("info") or {}
        gt = [(a["name"], _norm(a["kwargs"])) for a in ((row.get("info") or {}).get("task") or {}).get("actions", [])
              if a["name"] in pc.WRITE_TOOLS]
        ex_names = {n for n, _ in executed}
        gt_names = {n for n, _ in gt}
        violated = any(v["status"] == "executed" for v in data_viol)
        missing = [n for n, _ in gt if n not in ex_names]
        done = [n for n, _ in gt if n in ex_names]
        wrong_args = any(n in ex_names and (n, k) not in executed for n, k in gt)
        extra = any(n not in gt_names for n, _ in executed)
        if violated: labels.append("rule violation")
        if missing: labels.append("missing action")
        if wrong_args: labels.append("wrong arguments")
        if extra: labels.append("extra action")
        if "r_outputs" in ri: labels.append("missing output")
        # one category per failed dialogue, as in the tau-bench paper (Sec. 5, failure breakdown)
        if violated or extra or (gt and not done):
            category = "wrong decision"          # wrong type of call: broke a rule, did something not asked, or did not act
        elif missing and done:
            category = "partially resolve"       # did some of the requests, not all
        elif wrong_args:
            category = "wrong argument"
        elif "r_outputs" in ri:
            category = "wrong info"              # did not give the user the information asked for
        else:
            category = "other"
    return {"passed": passed, "data_viol": data_viol, "dlg_viol": dlg_viol, "labels": labels,
            "category": category, "n_turns": len(assistant_idx)}


CATEGORIES = ["wrong decision", "partially resolve", "wrong argument", "wrong info", "other"]


def position(p):
    return "early" if p < 1 / 3 else ("middle" if p < 2 / 3 else "late")


# ------------------------------------------------------------ hand-check export
def export_dialogue(path, arm, trial, task, row, res):
    traj = row.get("traj") or []
    task_info = (row.get("info") or {}).get("task") or {}
    lines = [f"# {arm} | trial {trial} | task {task} | reward {row['reward']}",
             f"**Auto category:** {res['category']} | labels: {', '.join(res['labels']) or '-'}",
             f"**Data violations:** {[(v['tool'], v['sources'], v['status']) for v in res['data_viol']]}",
             f"**Dialogue violations:** {[v['rule'] for v in res['dlg_viol']]}",
             "", "## User goal (simulator instruction)", task_info.get("instruction", ""),
             "", "## Answer key (write actions)"]
    for a in task_info.get("actions", []):
        if a["name"] in pc.WRITE_TOOLS:
            lines.append(f"- `{a['name']}` {json.dumps(a['kwargs'])}")
    lines += ["", "## Dialogue"]
    for m in traj[1:]:                                    # skip the long system prompt
        role = m.get("role")
        if role == "tool":
            lines.append(f"**tool →** {str(m.get('content'))[:400]}")
        elif role == "assistant" and m.get("tool_calls"):
            c = m["tool_calls"][0]["function"]
            pre = (m.get("content") or "").strip()
            lines.append(f"**agent:** {pre} `CALL {c['name']}({c['arguments']})`")
        else:
            lines.append(f"**{'user' if role == 'user' else 'agent'}:** {m.get('content')}")
        lines.append("")
    open(path, "w").write("\n".join(lines))
