# Turn the telecom policy graph (telecom_policy_graph.json) into plain text rules
# for the shallow arm: the whole text is added to the agent's system prompt,
# after the prose policy. Same content as the graph: nothing added, nothing dropped.
# Same layout as the airline graph_text.py (07), plus the user-side procedures.
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
GRAPH_PATH = os.environ.get("POLICY_GRAPH", os.path.join(_HERE, "telecom_policy_graph.json"))


def load(path=GRAPH_PATH):
    return json.load(open(path))


def _where(c):
    if c.get("check") == "data":
        return "check in the tool results"
    return "check in the conversation" if c.get("actor") == "user" else "your own behaviour"


def _if_fails(c):
    f = c.get("if_fails")
    if f == "transfer_to_human_agents":
        return " If not: transfer the user to a human agent."
    return ""


def action_text(g, name):
    a = next(x for x in g["actions"] if x["action"] == name)
    lines = [f"### {a['action']} (tool: {a['tool']}; from {', '.join(a['read'])})"]
    if a.get("needs_info"):
        lines.append("Ask / find first: " + "; ".join(f"{i['info']} [{i['source']}]" for i in a["needs_info"]))
    lines.append("Allowed only if ALL of these hold:")
    for k, c in enumerate(a["conditions"], 1):
        lines.append(f"  {k}. {c['condition']} [{c['source']}; {_where(c)}]{_if_fails(c)}")
    for f in a.get("facts", []):
        lines.append(f"Fact: {f['text']} [{f['source']}]")
    for f in a.get("after", []):
        lines.append(f"After the call: {f['text']} [{f['source']}]")
    return "\n".join(lines)


def procedure_text(p):
    lines = [f"### Issue: {p['issue']}",
             f"Diagnose with (user tool): {p['diagnose']}"]
    if p.get("prerequisite"):
        lines.append(f"Prerequisite: {p['prerequisite']}")
    lines.append("Check each cause; for every cause that holds, do the fix:")
    for k, s in enumerate(p["steps"], 1):
        then = f", then guide the user to {s['then']}" if s.get("then") else ""
        lines.append(f"  {k}. IF {s['if']} -> {s['fix']}{then} [{s['source']}]")
    return "\n".join(lines)


def full_text(g):
    lines = ["# Policy checklist (the same rules as the policy above, written as a graph)",
             "", "## Global rules (every conversation)"]
    for r in g["global_rules"]:
        lines.append(f"- {r['condition']} [{r['source']}].")
    lines += ["", "## Agent actions"]
    for a in g["actions"]:
        lines += ["", action_text(g, a["action"])]
    lines += ["", "## Troubleshooting procedures (the user does these on their own phone; guide them step by step)",
              "Order: cellular service first, then mobile data, then MMS."]
    for p in g["procedures"]:
        lines += ["", procedure_text(p)]
    return "\n".join(lines)


if __name__ == "__main__":
    t = full_text(load())
    print(t)
    print(f"\n[{len(t.split())} words]")
