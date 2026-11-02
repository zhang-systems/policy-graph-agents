# Training data for deep KiL, built from the Gemini dialogues of 05G-08.
#   positive = a dialogue that passed AND broke no data rule (the proposal:
#              "filtered to successful and policy-compliant runs")
#   negative = one write call that broke a data rule, with the dialogue before it
#              (the loss pushes its probability down)
# Every example keeps its task id, so training for fold f can leave out
# the tasks of fold f (they are the test tasks of that fold).
import json
import os
import random
from collections import Counter

import passk
import policy_checker as pc
import violations as V
from settings_deep import SOURCE_DIRS, N_FOLDS, MAX_POS_PER_TASK, SEED
from tau_bench.envs.airline.data import load_data
from tau_bench.envs.airline.tools import ALL_TOOLS
from tau_bench.envs.airline.wiki import WIKI

TOOLS_INFO = [t.get_info() for t in ALL_TOOLS]
N_TASKS = 50


def fold_of(task):
    return task % N_FOLDS


def fold_tasks(f):
    return [t for t in range(N_TASKS) if fold_of(t) == f]


def scan(traj, graph):
    """Indexes of assistant messages whose write call broke a data rule.
    Same replay as violations.replay, but it keeps the message index."""
    obs = {m["tool_call_id"]: m["content"] for m in traj if m.get("role") == "tool"}
    data, hist, bad = load_data(), [], []
    for i, m in enumerate(traj):
        if m.get("role") != "assistant" or not m.get("tool_calls"):
            continue
        c = m["tool_calls"][0]
        if c["id"] not in obs:
            continue
        name = c["function"]["name"]
        try:
            kw = json.loads(c["function"]["arguments"] or "{}")
        except json.JSONDecodeError:
            continue
        if name not in V.TOOLS or not isinstance(kw, dict):
            continue
        blocked = str(obs[c["id"]]).startswith(V.BLOCK_TAG)
        if name in pc.WRITE_TOOLS and pc.check(name, kw, data, hist, graph):
            bad.append(i)
        if blocked:
            continue                      # the validator did not run it
        try:
            out = V.TOOLS[name].invoke(data=data, **kw)
        except Exception as e:
            out = f"Error: {e}"
        if name in pc.WRITE_TOOLS and not str(out).startswith("Error"):
            hist.append({"name": name, "kwargs": kw})
    return bad


def clean(traj):
    """Messages in the plain chat format the model is trained on.
    The system prompt is always the prose wiki: in the deep arm the
    policy graph lives in the weights, not in the prompt (07 added it to the prompt)."""
    out = []
    for m in traj:
        role = m.get("role")
        if role == "system":
            out.append({"role": "system", "content": WIKI})
        elif role == "user":
            out.append({"role": "user", "content": m.get("content") or ""})
        elif role == "assistant":
            msg = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("tool_calls"):
                c = m["tool_calls"][0]
                try:
                    args = json.loads(c["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    return None
                msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                      "function": {"name": c["function"]["name"], "arguments": args}}]
            out.append(msg)
        elif role == "tool":
            out.append({"role": "tool", "tool_call_id": m.get("tool_call_id"),
                        "content": str(m.get("content"))})
    return out


def build(root, out_path):
    """Write every positive and negative example to one .jsonl file; return counts."""
    graph = pc.load_graph()
    rng = random.Random(SEED)
    pos, neg = [], []
    for d in SOURCE_DIRS:
        tab = passk.table(os.path.join(root, d))
        for trial, rows in sorted(tab.items()):
            for t, r in sorted(rows.items()):
                traj = r.get("traj") or []
                if not traj or passk.is_error(r):
                    continue
                bad = scan(traj, graph)
                blocked = any(str(m.get("content", "")).startswith(V.BLOCK_TAG)
                              for m in traj if m.get("role") == "tool")
                if r["reward"] >= 1 - 1e-6 and not bad and not blocked:
                    msgs = clean(traj)
                    if msgs:
                        pos.append({"task": t, "arm": d, "trial": trial, "kind": "pos", "messages": msgs})
                for i in bad:
                    msgs = clean(traj[: i + 1])
                    if msgs and msgs[-1]["role"] == "assistant":
                        neg.append({"task": t, "arm": d, "trial": trial, "kind": "neg", "messages": msgs})
    # cap good dialogues per task
    by_task = {}
    for e in pos:
        by_task.setdefault(e["task"], []).append(e)
    pos = []
    for t, es in sorted(by_task.items()):
        rng.shuffle(es)
        pos += es[:MAX_POS_PER_TASK]
    with open(out_path, "w") as f:
        for e in pos + neg:
            f.write(json.dumps(e) + "\n")
    return pos, neg


def load(path, fold, kinds=("pos", "neg")):
    """Training examples for one fold: every task except the fold's test tasks."""
    test = set(fold_tasks(fold))
    out = []
    for line in open(path):
        e = json.loads(line)
        if e["kind"] in kinds and e["task"] not in test:
            out.append(e)
    return out


HEAD, END = "<|im_start|>assistant\n", "<|im_end|>"


def encode(tok, msgs, last_only, max_len):
    """Token ids + a mask of the tokens the loss uses: the agent's turns
    (only the last one for a negative). None if too long or empty."""
    text = tok.apply_chat_template(msgs, tools=TOOLS_INFO, tokenize=False, enable_thinking=False)
    enc = tok(text, return_offsets_mapping=True, add_special_tokens=False)
    ids, offs = enc["input_ids"], enc["offset_mapping"]
    if len(ids) > max_len:
        return None
    spans, pos = [], 0
    while True:
        s = text.find(HEAD, pos)
        if s < 0:
            break
        e = text.find(END, s)
        if e < 0:
            break
        spans.append((s + len(HEAD), e + len(END)))
        pos = e
    if not spans:
        return None
    if last_only:
        spans = spans[-1:]
    mask = [any(a <= st < b for a, b in spans) for st, _ in offs]
    return (ids, mask) if any(mask[1:]) else None


def stats(path):
    rows = []
    for f in range(N_FOLDS):
        ex = load(path, f)
        c = Counter(e["kind"] for e in ex)
        rows.append({"fold": f, "test tasks": " ".join(map(str, fold_tasks(f))),
                     "train tasks": len({e["task"] for e in ex}),
                     "positives": c["pos"], "negatives": c["neg"]})
    return rows
