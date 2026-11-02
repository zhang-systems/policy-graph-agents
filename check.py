# check my policy graph: missing fields, unused wiki.md bullets, unverified items
import json

g = json.load(open("airline_policy_graph.json"))
ALL = ([f"G{i}" for i in range(1, 6)] + ["D1", "D2", "D3"] + [f"B{i}" for i in range(1, 6)]
       + [f"M{i}" for i in range(1, 7)] + [f"C{i}" for i in range(1, 5)] + [f"R{i}" for i in range(1, 4)])
used, problems, unverified = set(), [], 0

def walk(c, where):
    global unverified
    if "any_of" in c or "all_of" in c:
        for sub in c.get("any_of", []) + c.get("all_of", []):
            walk(sub, where)
        return
    for k in ("condition", "actor", "check", "source"):
        if not c.get(k):
            problems.append(f"{where}: missing '{k}'")
    if c.get("actor") not in ("agent", "user"):
        problems.append(f"{where}: actor must be agent or user")
    if c.get("check") not in ("data", "dialogue"):
        problems.append(f"{where}: check must be data or dialogue")
    used.add(c.get("source"))
    unverified += not c.get("verified")

for r in g["global_rules"]:
    walk(r, r["source"])
for x in g.get("info_only", []):
    used.add(x["source"])
for a in g["actions"]:
    name = a["action"]
    if not a["conditions"]:
        problems.append(f"{name}: no conditions yet")
    for i in a["needs_info"]:
        used.add(i["source"]); unverified += not i.get("verified")
    for c in a["conditions"]:
        walk(c, name)
    for f in a.get("facts", []) + a.get("tables", []):
        used.add(f["source"]); unverified += not f.get("verified")

print(f"actions: {len(g['actions'])}")
print("problems:" if problems else "problems: none")
for p in problems:
    print("  -", p)
missing = [s for s in ALL if s not in used]
print("wiki.md bullets not used:", ", ".join(missing) or "none")
print(f"items still to verify by hand: {unverified}")
