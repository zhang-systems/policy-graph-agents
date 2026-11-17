# NeSy retrieval, offline: neural seeds (the RAG arm's logged vector hits) + symbolic expansion on the policy graph.
# No tool name is used to pick rules: the graph decides which action the seeds point to.
import json, glob, re
import graph_text as gt, rag_text as rt

def action_rules(G):
    """graph action -> rule ids it uses; action -> tool (graph edge)."""
    rules = {a["action"]: set(rt.needed(gt.action_text(G, a["action"]))) for a in G["actions"]}
    tool = {a["action"]: a["tool"] for a in G["actions"]}
    return rules, tool

def ground(seeds, scores, rules, tool):
    """Symbolic step: score each action by the retrieval scores of its own (non-global) seed rules,
    keep the best action, then follow action -> tool -> sibling actions."""
    s = {}
    for r, sc in zip(seeds, scores):
        if not r or r.startswith("G"): continue
        for a, rs in rules.items():
            if r in rs: s[a] = s.get(a, 0) + sc
    if not s: return set(), []
    best = max(s, key=s.get)
    acts = [a for a in rules if tool[a] == tool[best]]
    return set().union(*(rules[a] for a in acts)), acts

def touched(seeds, rules):
    """Looser variant: union of every action any seed belongs to."""
    acts = [a for a, rs in rules.items() if any(r in rs for r in seeds if r and not r.startswith("G"))]
    return set().union(*(rules[a] for a in acts)) if acts else set(), acts

def evaluate(rows, G):
    rules, tool = action_rules(G)
    out = []
    for x in rows:
        need = set(x["needed"]); seeds = [s for s in x["sources"]]
        vec = {s for s in seeds if s}
        nesy, acts = ground(seeds, x["scores"], rules, tool)
        loose, lacts = touched(seeds, rules)
        out.append({"tool": x["tool"], "need": need, "vector": vec, "nesy": nesy, "nesy_loose": loose,
                    "graph": need, "nesy_right_action": tool.get(acts[0]) == x["tool"] if acts else False})
    return out
