# load airline_policy_graph.json into Neo4j
# usage: python load_neo4j.py   (reads NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD from env)
import json, os, re, sys

GRAPH = os.environ.get("POLICY_GRAPH", "airline_policy_graph.json")


def build(g):
    """Turn the JSON into a list of (cypher, params). No database needed here."""
    ops = []
    add = lambda q, **p: ops.append((q, p))

    # actions
    for a in g["actions"]:
        add("MERGE (a:Action {name:$name}) SET a.tool=$tool, a.read=$read",
            name=a["action"], tool=a["tool"], read=a["read"])
    # transfer_to_human_agents is a target of if_fails, not a graph action
    add("MERGE (:Action {name:'transfer_to_human_agents'})")

    # global rules
    for r in g["global_rules"]:
        add("MERGE (r:GlobalRule {source:$source}) SET r.text=$text, r.actor=$actor, r.check=$check",
            source=r["source"], text=r["condition"], actor=r["actor"], check=r["check"])
        for name in r.get("applies_to", []):
            add("MATCH (r:GlobalRule {source:$s}), (a:Action {name:$a}) MERGE (r)-[:APPLIES_TO]->(a)",
                s=r["source"], a=name)

    # info-only bullets
    for x in g.get("info_only", []):
        add("MERGE (d:Description {source:$s}) SET d.text=$t", s=x["source"], t=x["text"])

    # one action at a time
    for a in g["actions"]:
        act = a["action"]
        for i in a["needs_info"]:
            add("MERGE (n:Info {name:$name}) WITH n MATCH (a:Action {name:$act}) "
                "MERGE (a)-[e:NEEDS_INFO {source:$s}]->(n) SET e.order=$order",
                name=i["info"], act=act, s=i["source"], order=i.get("order"))
        for k, f in enumerate(a.get("facts", [])):
            add("MERGE (f:Fact {id:$id}) SET f.text=$t, f.source=$s WITH f "
                "MATCH (a:Action {name:$act}) MERGE (a)-[:HAS_FACT]->(f)",
                id=f"{act}/fact{k}", t=f["text"], s=f["source"], act=act)
        for t in a.get("tables", []):
            add("MERGE (t:Table {id:$id}) SET t.name=$n, t.source=$s, t.rows=$rows, "
                "t.key=$key, t.extra_bag_price=$p WITH t "
                "MATCH (a:Action {name:$act}) MERGE (a)-[:HAS_TABLE]->(t)",
                id=f"{act}/{t['name']}", n=t["name"], s=t["source"], rows=json.dumps(t["rows"]),
                key=t.get("key"), p=t.get("extra_bag_price"), act=act)

        # conditions; OR / AND become Gate nodes
        def walk(c, cid, parent_label, parent_key, parent_val, rel):
            if "any_of" in c or "all_of" in c:
                kind = "OR" if "any_of" in c else "AND"
                add("MERGE (g:Gate {id:$id}) SET g.kind=$kind, g.amount=$amount, g.if_fails=$fails",
                    id=cid, kind=kind, amount=c.get("amount"), fails=c.get("if_fails"))
                link(parent_label, parent_key, parent_val, "Gate", cid, rel, c.get("if_fails"))
                for j, sub in enumerate(c.get("any_of", []) + c.get("all_of", [])):
                    walk(sub, f"{cid}.{j}", "Gate", "id", cid, "BRANCH")
            else:
                add("MERGE (c:Condition {id:$id}) SET c.text=$t, c.actor=$actor, c.check=$check, "
                    "c.source=$s, c.if_fails=$fails",
                    id=cid, t=c["condition"], actor=c["actor"], check=c["check"],
                    s=c["source"], fails=c.get("if_fails"))
                link(parent_label, parent_key, parent_val, "Condition", cid, rel, c.get("if_fails"))
                if c.get("if_fails") == "transfer_to_human_agents":
                    add("MATCH (c:Condition {id:$id}), (t:Action {name:'transfer_to_human_agents'}) "
                        "MERGE (c)-[:IF_FAILS]->(t)", id=cid)
                if c.get("must_happen_before"):
                    add("MATCH (c:Condition {id:$id}), (t:Action {name:$t}) MERGE (c)-[:MUST_HAPPEN_BEFORE]->(t)",
                        id=cid, t=c["must_happen_before"])

        def link(pl, pk, pv, cl, cid, rel, fails):
            add(f"MATCH (p:{pl} {{{pk}:$pv}}), (c:{cl} {{id:$cid}}) MERGE (p)-[:{rel}]->(c)",
                pv=pv, cid=cid)

        for k, c in enumerate(a["conditions"]):
            walk(c, f"{act}/c{k}", "Action", "name", act, "REQUIRES")
    # tag every node with :PolicyGraph, so this graph never touches other data in the same database
    tag = lambda q: re.sub(r"\((\w*):(Action|GlobalRule|Description|Info|Fact|Table|Gate|Condition)\b",
                           r"(\1:\2:PolicyGraph", q)
    return [(tag(q), p) for q, p in ops]


def main():
    g = json.load(open(GRAPH))
    ops = build(g)
    from neo4j import GraphDatabase
    drv = GraphDatabase.driver(os.environ["NEO4J_URI"],
                               auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
    with drv.session() as s:
        # delete only MY old policy graph (label :PolicyGraph); other data stays
        s.run("MATCH (n:PolicyGraph) DETACH DELETE n")
        for q, p in ops:
            s.run(q, **p)
        n = s.run("MATCH (n:PolicyGraph) RETURN count(n) AS n").single()["n"]
        r = s.run("MATCH (:PolicyGraph)-[r]->(:PolicyGraph) RETURN count(r) AS r").single()["r"]
    drv.close()
    print(f"loaded {GRAPH}: {n} nodes, {r} relationships")


if __name__ == "__main__":
    main()
