# Vector RAG over the policy TEXT (wiki.md), for the RAG arm (14).
# Same moment and same prompt as semi-deep (08); only the source of the rules changes:
#   semi-deep: the rules of the action, taken from the policy graph (by action name)
#   RAG:       the wiki.md bullets most similar to the pending tool call (by embedding)
# Fair budget: for each tool, RAG gets about as many characters as semi-deep shows for it.
import os
import re

import numpy as np

EMBED_MODEL = os.environ.get("RAG_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")


def chunks(wiki):
    """One chunk per policy bullet (sub-bullets stay with their bullet), with its section title."""
    out, section, cur = [], "Policy", None
    for line in wiki.splitlines():
        if line.startswith("#"):
            if cur: out.append(cur); cur = None
            section = line.lstrip("#").strip()
        elif line.startswith("- "):
            if cur: out.append(cur)
            cur = {"section": section, "text": line[2:].strip(), "bullet": True}
        elif line.startswith("  ") and cur and line.strip():
            cur["text"] += "\n" + line.strip()
        elif not line.strip():
            continue
        else:  # a plain sentence outside a bullet (e.g. the intro)
            if cur: out.append(cur); cur = None
            out.append({"section": section, "text": line.strip()})
    if cur: out.append(cur)
    # bullet ids as in the policy graph: G1-G5, D1-D3, B1-B5, M1-M6, C1-C4, R1-R3
    letter = {"Airline Agent Policy": "G", "Domain Basic": "D", "Book flight": "B",
              "Modify flight": "M", "Cancel flight": "C", "Refund": "R"}
    seen = {}
    for i, c in enumerate(out):
        c["id"] = i
        c["doc"] = f"{c['section']}: {c['text']}"
        c["source"] = None
        if c.pop("bullet", False) and c["section"] in letter:
            L = letter[c["section"]]; seen[L] = seen.get(L, 0) + 1
            c["source"] = f"{L}{seen[L]}"
    return out


# one tool can be two graph actions (change flights, or only the cabin)
GRAPH_ACTIONS = {"update_reservation_flights": ["update_reservation_flights", "upgrade_cabin"]}


def graph_rules(G, tool):
    """The rule text semi-deep shows for this tool (also RAG's character budget)."""
    import graph_text as gt
    names = GRAPH_ACTIONS.get(tool, [tool])
    head = "whichever of these fits the call" if len(names) > 1 else ""
    return (head + "\n\n" if head else "") + "\n\n".join(gt.action_text(G, n) for n in names)


def query_text(tool, arguments):
    # what the agent is about to do, in words: "cancel reservation reason: change_of_plan ..."
    return f"{tool.replace('_', ' ')} {arguments}"


class Retriever:
    def __init__(self, wiki, encode=None):
        self.chunks = chunks(wiki)
        if encode is None:
            from sentence_transformers import SentenceTransformer
            m = SentenceTransformer(EMBED_MODEL, device="cpu")
            encode = lambda xs: m.encode(xs, normalize_embeddings=True)
        self.encode = encode
        self.E = np.asarray(encode([c["doc"] for c in self.chunks]))

    def rank(self, query):
        q = np.asarray(self.encode([query]))[0]
        s = self.E @ q
        return [(self.chunks[i], float(s[i])) for i in np.argsort(-s)]

    def retrieve(self, query, budget_chars):
        """Best chunks in order until they reach the budget (the last one may go a bit over,
        so RAG never gets less text than semi-deep)."""
        picked, used = [], 0
        for c, s in self.rank(query):
            if used >= budget_chars:
                break
            picked.append((c, s)); used += len(c["doc"])
        return picked

    def text(self, query, budget_chars):
        return "\n\n".join(f"- [{c['section']}] {c['text']}" for c, _ in self.retrieve(query, budget_chars))


def needed(rules_text):
    """Bullet ids the policy graph uses for this action (from semi-deep's rule text)."""
    return sorted(set(re.findall(r"\[([GDBMCR]\d)", rules_text)))
