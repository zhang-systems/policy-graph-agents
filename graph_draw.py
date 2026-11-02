# shared drawing code for the policy graph (pyvis + Graphviz)
import textwrap

COLORS = {"Action": "#dae8fc", "Condition": "#d5e8d4", "Gate": "#fff2cc", "Info": "#eeeeee",
          "Fact": "#f8cecc", "Table": "#e1d5e7", "GlobalRule": "#ffe6cc", "Description": "#f5f5f5"}
DIALOGUE = "#ffe6cc"   # rule only visible in the chat


def fetch(drv):
    """Read the whole policy graph once: nodes {id: {...}}, edges [(a, b, type)]."""
    nodes, edges = {}, []
    with drv.session() as s:
        for r in s.run("MATCH (n:PolicyGraph) RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS p"):
            label = [l for l in r["labels"] if l != "PolicyGraph"][0]
            nodes[r["id"]] = {"label": label, **r["p"]}
        for r in s.run("MATCH (a:PolicyGraph)-[e]->(b:PolicyGraph) "
                       "RETURN elementId(a) AS a, elementId(b) AS b, type(e) AS t"):
            edges.append((r["a"], r["b"], r["t"]))
    return nodes, edges


def select(nodes, edges, action=None, show_facts=True, show_descriptions=False):
    """Keep one action and everything under it (or all), and drop notes if asked."""
    drop = set()
    if not show_facts:
        drop |= {i for i, n in nodes.items() if n["label"] in ("Fact", "Table")}
    if not show_descriptions:
        drop |= {i for i, n in nodes.items() if n["label"] == "Description"}
    keep = set(nodes) - drop
    if action:
        start = [i for i, n in nodes.items() if n["label"] == "Action" and n.get("name") == action]
        if not start:
            raise ValueError(f"no action named {action!r}")
        down = {}
        for a, b, _ in edges:
            down.setdefault(a, []).append(b)
        reach, todo = set(start), list(start)
        while todo:                         # everything below the action
            for b in down.get(todo.pop(), []):
                if b not in reach:
                    reach.add(b); todo.append(b)
        reach |= {a for a, b, t in edges if t == "APPLIES_TO" and b in start}   # G1 above it
        keep &= reach
    return {i: nodes[i] for i in keep}, [e for e in edges if e[0] in keep and e[1] in keep]


def text_of(n, width=30):
    """Short text for a node: name, OR/AND, or the rule text, plus its source id."""
    t = n.get("name") or n.get("kind") or n.get("text") or n.get("source") or ""
    if n["label"] == "GlobalRule":
        t = f"{n['source']}: {t}"
    elif n.get("source") and n["label"] not in ("Description",):
        t = f"{t} ({n['source']})"
    if n["label"] == "Description":
        t = f"{n['source']}: {t}"
    return textwrap.fill(t, width)


def color_of(n):
    return DIALOGUE if n.get("check") == "dialogue" else COLORS.get(n["label"], "#ffffff")


def to_graphviz(nodes, edges, title="Airline policy graph", legend=True):
    """A clean, layered picture; saves as SVG/PDF, sharp at any zoom.
    Each action gets its own box (cluster). Shared info nodes (user_id, ...) are drawn once
    per action, only in the picture, so dashed lines don't cross the whole page."""
    import graphviz
    g = graphviz.Digraph("policy", graph_attr={
        "rankdir": "LR", "splines": "spline", "nodesep": "0.15", "ranksep": "0.6",
        "fontname": "Helvetica", "label": title, "labelloc": "t", "fontsize": "22",
        "newrank": "true", "compound": "true"})
    g.attr("node", shape="box", style="rounded,filled", fontname="Helvetica", fontsize="10",
           color="#888888", penwidth="0.8", margin="0.12,0.06")
    g.attr("edge", fontname="Helvetica", fontsize="8", color="#999999", arrowsize="0.6")

    # Neo4j ids contain ":", which Graphviz reads as a port; use short safe ids instead
    gid = {i: f"n{k}" for k, i in enumerate(nodes)}
    down = {}
    for a, b, t in edges:
        down.setdefault(a, []).append((b, t))

    def node_attrs(n):
        kw = dict(fillcolor=color_of(n))
        if n["label"] == "Action":
            kw.update(fontsize="13", penwidth="1.6", color="#6c8ebf")
        elif n["label"] == "Gate":
            kw.update(shape="diamond", fontsize="9", margin="0.02")
        elif n["label"] == "Info":
            kw.update(style="rounded,filled,dashed")
        return kw

    def edge_attrs(t):
        if t == "IF_FAILS":
            return dict(label="IF_FAILS", color="#b85450", fontcolor="#b85450", style="dashed")
        if t == "NEEDS_INFO":
            return dict(style="dashed")
        if t == "MUST_HAPPEN_BEFORE":
            # drawn as a link, but it must not pull the layout backwards
            return dict(label="MUST_HAPPEN_BEFORE", color="#0050ef", fontcolor="#0050ef",
                        penwidth="1.4", constraint="false")
        return {}

    drawn = set()
    later = []   # edges that leave a cluster (IF_FAILS, MUST_HAPPEN_BEFORE, APPLIES_TO)
    actions = [i for i, n in nodes.items() if n["label"] == "Action" and i in down]
    for k, act in enumerate(sorted(actions, key=lambda i: nodes[i]["name"])):
        with g.subgraph(name=f"cluster_{k}") as c:
            c.attr(style="rounded", color="#cccccc", label="")
            c.node(gid[act], text_of(nodes[act]), **node_attrs(nodes[act]))
            drawn.add(act)
            todo = [act]
            while todo:
                a = todo.pop()
                for b, t in down.get(a, []):
                    nb = nodes[b]
                    if t in ("IF_FAILS", "MUST_HAPPEN_BEFORE"):
                        later.append((a, b, t))
                        continue
                    if nb["label"] == "Info":      # private copy of shared info for this action
                        cid = f"{gid[b]}_{k}"
                        c.node(cid, text_of(nb), **node_attrs(nb))
                        c.edge(gid[a], cid, **edge_attrs(t))
                        continue
                    if b not in drawn:
                        c.node(gid[b], text_of(nb), **node_attrs(nb))
                        drawn.add(b)
                        todo.append(b)
                    c.edge(gid[a], gid[b], **edge_attrs(t))

    # global rules in their own box on the left
    rules = [i for i, n in nodes.items() if n["label"] == "GlobalRule"]
    if rules:
        with g.subgraph(name="cluster_global") as c:
            c.attr(label="Global rules (G1-G5)", style="rounded,dashed", color="#d79b00",
                   fontsize="12")
            for r in sorted(rules, key=lambda i: nodes[i]["source"]):
                c.node(gid[r], text_of(nodes[r], 34), **node_attrs(nodes[r]))
                drawn.add(r)
    for a, b, t in edges:
        if t == "APPLIES_TO":
            later.append((a, b, t))

    # anything left (e.g. transfer_to_human_agents, descriptions) outside the clusters
    for i, n in nodes.items():
        if i not in drawn and n["label"] != "Info":   # info already drawn inside each action
            g.node(gid[i], text_of(n), **node_attrs(n))
    for a, b, t in later:
        kw = edge_attrs(t)
        if t == "APPLIES_TO":
            kw = dict(color="#d79b00", style="dotted", arrowsize="0.5")
        g.edge(gid[a], gid[b], **kw)

    if legend:
        with g.subgraph(name="cluster_legend") as c:
            c.attr(label="Legend", style="rounded", color="#999999", fontsize="11")
            items = [("Action (tool)", COLORS["Action"], {}),
                     ("rule: data can check it\n(validator can check)", COLORS["Condition"], {}),
                     ("rule: only in the dialogue", DIALOGUE, {}),
                     ("OR / AND", COLORS["Gate"], {"shape": "diamond"}),
                     ("info to ask first", COLORS["Info"], {"style": "rounded,filled,dashed"}),
                     ("note / table", COLORS["Fact"], {})]
            prev = None
            for j, (lab, col, kw) in enumerate(items):
                c.node(f"lg{j}", lab, fillcolor=col, fontsize="9", **kw)
                if prev:
                    c.edge(prev, f"lg{j}", style="invis")
                prev = f"lg{j}"
    return g


def to_pyvis(nodes, edges, style="aura"):
    """Interactive picture. style='aura': dots + 2 colors; 'detailed': boxes with text."""
    from pyvis.network import Network
    aura = style == "aura"
    net = Network(height="800px", width="100%", directed=True, cdn_resources="in_line",
                  bgcolor="#f5f6f6" if aura else "#ffffff")
    for i, n in nodes.items():
        hover = text_of(n, 60)
        if aura:
            # short label only: action name, OR/AND, or the rule id (e.g. C2); long text on hover
            if n["label"] == "Action":
                short, size, fsize = n["name"], 16, 12
            elif n["label"] == "Gate":
                short, size, fsize = n["kind"], 10, 9
            elif n["label"] == "Info":
                short, size, fsize = " ", 8, 9       # a space, not "": pyvis shows the id for ""
            else:
                short, size, fsize = n.get("source") or "", 10, 9
            net.add_node(i, label=short or " ", title=hover, shape="dot", size=size, borderWidth=0,
                         color="#c5ddf7" if n["label"] == "Action" else "#e8a0e8",
                         font={"size": fsize, "color": "#444444"})
        else:
            net.add_node(i, label=text_of(n), title=hover, color=color_of(n), shape="box",
                         font={"size": 16 if n["label"] == "Action" else 12,
                               "bold": n["label"] == "Action"})
    for a, b, t in edges:
        if aura:
            net.add_edge(a, b, title=t, color="#c8c8c8", width=1, length=90,
                         arrows={"to": {"scaleFactor": 0.4}})
        else:
            kw = dict(length=220, font={"align": "middle", "background": "white", "size": 12})
            if t == "IF_FAILS":
                kw.update(length=320, color="#b85450", dashes=True)
            net.add_edge(a, b, label=t, **kw)
    if aura:
        # spread the dots out so labels have room
        net.repulsion(node_distance=130, spring_length=90, central_gravity=0.15)
    else:
        net.repulsion(node_distance=220, spring_length=220)
    return net
