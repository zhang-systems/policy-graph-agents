# Turn the policy graph (airline_policy_graph.json) into plain text rules.
# 07 shallow puts the whole text in the system prompt.
# 08 semi-deep puts only the part for one action in front of the agent, right before it acts.
# Same content as the graph: nothing added, nothing dropped.
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
GRAPH_PATH = os.environ.get("POLICY_GRAPH", os.path.join(_HERE, "airline_policy_graph.json"))
ROMAN = ["i", "ii", "iii", "iv", "v", "vi"]
LETTERS = "abcdefgh"


def load(path=GRAPH_PATH):
    return json.load(open(path))


def _where(c):
    # where the agent can see whether the rule holds
    if c.get("check") == "data":
        return "check in the tool results"
    return "check in the conversation" if c.get("actor") == "user" else "your own behaviour"


def _if_fails(c):
    f = c.get("if_fails")
    if f == "transfer_to_human_agents":
        return " If not: transfer the user to a human agent."
    if f == "deny":
        return " If not: deny the request (G4)."
    return ""


def _cond(c, depth=0):
    """Lines for one condition, with OR / AND groups indented."""
    pad = "   " * (depth + 1)
    out = []
    if "any_of" in c or "all_of" in c:
        kind, subs = ("ANY ONE of", c["any_of"]) if "any_of" in c else ("ALL of", c["all_of"])
        extra = f" (amount: {c['amount']})" if "amount" in c else ""
        out.append(f"{kind}:{extra}{_if_fails(c)}")
        marks = LETTERS if depth == 0 else ROMAN
        for j, s in enumerate(subs):
            sub = _cond(s, depth + 1)
            out.append(f"{pad}{marks[j]}. {sub[0]}")
            out += sub[1:]
        return out
    txt = f"{c['condition']} [{c['source']}; {_where(c)}]"
    if "must_happen_before" in c:
        txt += f" (must be done before {c['must_happen_before']})"
    if "amount" in c:
        txt += f" (amount: {c['amount']})"
    return [txt + _if_fails(c)]


def action_text(g, name):
    a = next(x for x in g["actions"] if x["action"] == name)
    lines = [f"### {a['action']} (tool: {a['tool']}; from {', '.join(a['read'])})"]
    if a.get("needs_info"):
        info = "; ".join(f"{i['info']} [{i['source']}]" for i in a["needs_info"])
        lines.append(f"Ask the user first: {info}")
    glob = [r for r in g["global_rules"] if name in r.get("applies_to", [])]
    for r in glob:
        lines.append(f"Before the call: {r['condition']} [{r['source']}]")
    lines.append("Allowed only if ALL of these hold:")
    for k, c in enumerate(a["conditions"], 1):
        sub = _cond(c)
        lines.append(f"  {k}. {sub[0]}")
        lines += ["  " + s for s in sub[1:]]
    for f in a.get("facts", []):
        lines.append(f"Fact: {f['text']} [{f['source']}]")
    for t in a.get("tables", []):
        rows = "; ".join(f"{tier}: " + ", ".join(f"{cab} {n}" for cab, n in r.items())
                         for tier, r in t["rows"].items())
        lines.append(f"Table {t['name']} [{t['source']}] ({t['key']}): {rows}; "
                     f"each extra bag costs ${t['extra_bag_price']}")
    return "\n".join(lines)


def full_text(g):
    lines = ["# Policy checklist (the same rules as the policy above, written as a graph)",
             "", "## Global rules (every conversation)"]
    for r in g["global_rules"]:
        scope = f" Applies to: {', '.join(r['applies_to'])}." if "applies_to" in r else ""
        lines.append(f"- {r['condition']} [{r['source']}].{scope}")
    lines += ["", "## Actions"]
    for a in g["actions"]:
        lines += ["", action_text(g, a["action"])]
    return "\n".join(lines)


if __name__ == "__main__":
    print(full_text(load()))
