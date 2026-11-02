# read a tau2 results.json: reward, turns, tokens and cost per dialogue
import json

from settings_tau2 import PRICE_IN, PRICE_OUT


def load(path):
    return json.load(open(path))


def rows(res):
    out = []
    for s in res["simulations"]:
        tok = {"agent": [0, 0], "user": [0, 0]}
        turns = {"agent": 0, "user": 0, "agent_tool": 0, "user_tool": 0}
        for m in s.get("messages") or []:
            role = m.get("role")
            who = "agent" if role == "assistant" else ("user" if role == "user" else None)
            if who:
                turns[who] += 1
                if m.get("tool_calls"):
                    turns[f"{who}_tool"] += len(m["tool_calls"])
                u = m.get("usage") or {}
                tok[who][0] += u.get("prompt_tokens") or 0
                tok[who][1] += u.get("completion_tokens") or 0
        cost = sum(i * PRICE_IN + o * PRICE_OUT for i, o in tok.values()) / 1e6
        out.append({"task": s["task_id"], "trial": s.get("trial"),
                    "reward": (s.get("reward_info") or {}).get("reward"),
                    "end": s.get("termination_reason"),
                    "agent msgs": turns["agent"], "user msgs": turns["user"],
                    "agent tool calls": turns["agent_tool"], "user tool calls": turns["user_tool"],
                    "agent in": tok["agent"][0], "agent out": tok["agent"][1],
                    "user in": tok["user"][0], "user out": tok["user"][1],
                    "cost $": round(cost, 4)})
    return out


BLOCK_TAG = "BLOCKED by policy validator"


def blocked(res):
    """Validator blocks per dialogue (0 for arms without the validator)."""
    out = []
    for s in res["simulations"]:
        n = sum(1 for m in s.get("messages") or []
                if m.get("role") == "tool" and BLOCK_TAG in str(m.get("content")))
        out.append({"task": s["task_id"], "trial": s.get("trial"), "blocked": n})
    return out


def passk(df, max_k=8):
    """pass^k for k = 1..max_k, tau-bench formula: mean over tasks of C(c,k)/C(n,k).
    Each task uses its own number of trials n; k only goes up to the smallest n,
    so an unfinished arm can never give a value above 1."""
    from math import comb
    ok = df.assign(ok=df["reward"].fillna(0) >= 1 - 1e-6).groupby("task")["ok"].agg(["sum", "count"])
    n_min = int(ok["count"].min())
    return {k: sum(comb(int(c), k) / comb(int(n), k) for c, n in zip(ok["sum"], ok["count"])) / len(ok)
            for k in range(1, min(max_k, n_min) + 1)}, n_min


def show(df, title="", fmt=None, index=False, bar=None):
    """Display a table with a caption, clean number formats and a light style."""
    from IPython.display import display
    st = df.style
    if fmt:
        st = st.format(fmt)
    if bar:
        st = st.bar(subset=bar, color="#9ecae1", vmin=0, vmax=1)
    if not index:
        st = st.hide(axis="index")
    st = st.set_caption(title).set_table_styles([
        {"selector": "caption", "props": "caption-side: top; font-size: 15px; font-weight: bold; text-align: left; padding: 6px 0;"},
        {"selector": "th", "props": "background-color: #f2f2f2; text-align: center; padding: 4px 10px;"},
        {"selector": "td", "props": "text-align: center; padding: 4px 10px;"},
    ])
    display(st)
