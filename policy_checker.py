# Policy checker for tau-bench airline, built from airline_policy_graph.json.
#
# The graph decides WHICH rules apply to an action and how they combine
# (OR / AND gates). This file only says how to test each "data" rule
# against the database. "dialogue" rules can't be seen here, so they count
# as unknown and never block (a validator can't read the chat).
#
# Evaluation: each rule gives True / False / None (unknown).
#   AND -> False if any False, else None if any None, else True
#   OR  -> True if any True,  else None if any None, else False
# A top-level condition that comes out False is a violation.
import json
import os
from datetime import datetime, timedelta

NOW = datetime(2024, 5, 15, 15, 0, 0)          # wiki.md: "The current time is 2024-05-15 15:00:00 EST"
WRITE_TOOLS = {"book_reservation", "update_reservation_flights", "update_reservation_baggages",
               "update_reservation_passengers", "cancel_reservation", "send_certificate"}
FLOWN = {"flying", "landed"}

_HERE = os.path.dirname(os.path.abspath(__file__))
GRAPH_PATH = os.environ.get("POLICY_GRAPH", os.path.join(_HERE, "airline_policy_graph.json"))


# ---------------------------------------------------------------- helpers
def _status(data, flight_number, date):
    f = data["flights"].get(flight_number)
    if not f or date not in f["dates"]:
        return None
    return f["dates"][date]["status"]


def _segments(flights):
    return {(f["flight_number"], f["date"]) for f in flights}


def _user_reservations(ctx):
    user = ctx["user"]
    if not user:
        return []
    return [ctx["data"]["reservations"][r] for r in user.get("reservations", [])
            if r in ctx["data"]["reservations"]]


def _cabin(ctx, res):
    """Real cabin of a reservation. tau-bench's update_reservation_flights never saves
    the new cabin, so I take it from earlier successful calls in this dialogue."""
    cabin = res["cabin"]
    for h in ctx["history"]:
        if h["name"] == "update_reservation_flights" and h["kwargs"].get("reservation_id") == res["reservation_id"]:
            cabin = h["kwargs"].get("cabin", cabin)
    return cabin


def _endpoints(data, flights):
    """(first origin, last destination, all destinations) of a list of flight segments."""
    legs = [data["flights"].get(f["flight_number"]) for f in flights]
    if not legs or any(l is None for l in legs):
        return None
    return legs[0]["origin"], legs[-1]["destination"], {l["destination"] for l in legs}


# ---------------------------------------------------------------- data rules
# key = rule id in the graph: "<action>/c<k>" plus ".<j>" inside OR/AND gates
# (same ids as load_neo4j.py). Each returns True, False or None.
def r_book_status(ctx):                       # D3
    return all(_status(ctx["data"], f["flight_number"], f["date"]) == "available"
               for f in ctx["kw"].get("flights", []))


def r_book_max5(ctx):                         # B2
    return len(ctx["kw"].get("passengers", [])) <= 5


def r_book_same_flights_cabin(ctx):           # B2 (one cabin and one flight list per call)
    return True


def r_book_payment_limits(ctx):               # B3
    pm = ctx["user"]["payment_methods"] if ctx["user"] else {}
    src = [pm.get(p["payment_id"], {}).get("source") for p in ctx["kw"].get("payment_methods", [])]
    return src.count("certificate") <= 1 and src.count("credit_card") <= 1 and src.count("gift_card") <= 3


def r_book_payment_in_profile(ctx):           # B3
    pm = ctx["user"]["payment_methods"] if ctx["user"] else {}
    return all(p["payment_id"] in pm for p in ctx["kw"].get("payment_methods", []))


def r_flights_not_basic(ctx):                 # M2
    return _cabin(ctx, ctx["res"]) != "basic_economy"


def r_flights_same_od_type(ctx):              # M2
    res, data = ctx["res"], ctx["data"]
    ends = _endpoints(data, ctx["kw"].get("flights", []))
    if ends is None:
        return None
    first_o, last_d, dests = ends
    if res["flight_type"] == "one_way":
        return first_o == res["origin"] and last_d == res["destination"]
    return first_o == res["origin"] and last_d == res["origin"] and res["destination"] in dests


def r_flights_payment_gc_cc(ctx):             # M6
    pm = ctx["user"]["payment_methods"] if ctx["user"] else {}
    return pm.get(ctx["kw"].get("payment_id"), {}).get("source") in ("gift_card", "credit_card")


def r_cabin_flights_same(ctx):                # M3 (checked only when the flights are unchanged)
    return _segments(ctx["kw"].get("flights", [])) == _segments(ctx["res"]["flights"])


def r_cabin_same_all(ctx):                    # M3 (one cabin per call)
    return True


def r_bags_not_lower(ctx):                    # M4
    return ctx["kw"].get("total_baggages", 0) >= ctx["res"]["total_baggages"]


def r_bags_no_insurance(ctx):                 # M4 (this tool can't add insurance)
    return True


def r_pax_same_count(ctx):                    # M5
    return len(ctx["kw"].get("passengers", [])) == len(ctx["res"]["passengers"])


def r_cancel_not_flown(ctx):                  # C3
    return all(_status(ctx["data"], f["flight_number"], f["date"]) not in FLOWN
               for f in ctx["res"]["flights"])


def r_cancel_24h(ctx):                        # C2
    created = datetime.fromisoformat(ctx["res"]["created_at"])
    return NOW - created <= timedelta(hours=24)


def r_cancel_airline_cancelled(ctx):          # C2
    return any(_status(ctx["data"], f["flight_number"], f["date"]) == "cancelled"
               for f in ctx["res"]["flights"])


def r_cancel_business(ctx):                   # C2
    return _cabin(ctx, ctx["res"]) == "business"


def r_cancel_insurance(ctx):                  # C2
    return ctx["res"].get("insurance") == "yes"


# send_certificate has no reservation id, so reservation rules look at the
# user's reservations: True if any one of them fits (the agent may mean it).
def r_cert_member(ctx):                       # R1
    return bool(ctx["user"]) and ctx["user"]["membership"] in ("silver", "gold")


def r_cert_insurance(ctx):                    # R1
    return any(r.get("insurance") == "yes" for r in _user_reservations(ctx))


def r_cert_business(ctx):                     # R1
    return any(_cabin(ctx, r) == "business" for r in _user_reservations(ctx))


def _any_flight_status(ctx, status):
    return any(_status(ctx["data"], f["flight_number"], f["date"]) == status
               for r in _user_reservations(ctx) for f in r["flights"])


def r_cert_fact_cancelled(ctx):               # R1
    return _any_flight_status(ctx, "cancelled")


def r_cert_fact_delayed(ctx):                 # R2
    return _any_flight_status(ctx, "delayed")


def r_cert_change_first(ctx):                 # R2: a change or cancel happened earlier in this dialogue
    return any(a in ("update_reservation_flights", "update_reservation_baggages",
                     "update_reservation_passengers", "cancel_reservation") for a in
               (h["name"] for h in ctx["history"]))


DATA_RULES = {
    "book_reservation/c0": r_book_status,
    "book_reservation/c1": r_book_max5,
    "book_reservation/c2": r_book_same_flights_cabin,
    "book_reservation/c3": r_book_payment_limits,
    "book_reservation/c4": r_book_payment_in_profile,
    "update_reservation_flights/c0": r_flights_not_basic,
    "update_reservation_flights/c1": r_flights_same_od_type,
    "update_reservation_flights/c2": r_flights_payment_gc_cc,
    "upgrade_cabin/c0": r_cabin_flights_same,
    "upgrade_cabin/c1": r_cabin_same_all,
    "update_reservation_baggages/c0": r_bags_not_lower,
    "update_reservation_baggages/c1": r_bags_no_insurance,
    "update_reservation_passengers/c0": r_pax_same_count,
    "cancel_reservation/c0": r_cancel_not_flown,
    "cancel_reservation/c1.0": r_cancel_24h,
    "cancel_reservation/c1.1": r_cancel_airline_cancelled,
    "cancel_reservation/c1.2": r_cancel_business,
    "cancel_reservation/c1.3.0": r_cancel_insurance,
    "send_certificate/c1.0": r_cert_member,
    "send_certificate/c1.1": r_cert_insurance,
    "send_certificate/c1.2": r_cert_business,
    "send_certificate/c2.0.1": r_cert_fact_cancelled,
    "send_certificate/c2.1.1": r_cert_fact_delayed,
    "send_certificate/c2.1.2": r_cert_change_first,
}


# ---------------------------------------------------------------- graph walk
def load_graph(path=GRAPH_PATH):
    g = json.load(open(path))
    actions = {a["action"]: a for a in g["actions"]}
    # every data rule in the graph must have code, and no code without a rule
    ids = set()

    def walk(c, cid):
        if "any_of" in c or "all_of" in c:
            for j, s in enumerate(c.get("any_of", []) + c.get("all_of", [])):
                walk(s, f"{cid}.{j}")
        elif c["check"] == "data":
            ids.add(cid)
    for a in g["actions"]:
        for k, c in enumerate(a["conditions"]):
            walk(c, f"{a['action']}/c{k}")
    missing, extra = ids - set(DATA_RULES), set(DATA_RULES) - ids
    assert not missing and not extra, f"graph and code differ: missing={missing} extra={extra}"
    return actions


def _eval(c, cid, ctx, failed):
    if "any_of" in c or "all_of" in c:
        subs = [_eval(s, f"{cid}.{j}", ctx, failed)
                for j, s in enumerate(c.get("any_of", []) + c.get("all_of", []))]
        if "all_of" in c:
            return False if False in subs else (None if None in subs else True)
        return True if True in subs else (None if None in subs else False)
    if c["check"] == "dialogue":
        return None
    ok = DATA_RULES[cid](ctx)
    if ok is False:
        failed.append((cid, c["source"], c["condition"]))
    return ok


def graph_actions_for(tool, kw, data, history=()):
    """Which graph actions a tool call stands for (one tool can be two graph actions)."""
    if tool != "update_reservation_flights":
        return [tool]
    res = data["reservations"].get(kw.get("reservation_id"))
    if res is None:
        return []
    out = []
    if _segments(kw.get("flights", [])) != _segments(res["flights"]):
        out.append("update_reservation_flights")
    elif kw.get("cabin") != _cabin({"history": history}, res):
        out.append("upgrade_cabin")
    return out


def check(tool, kw, data, history, graph):
    """Violations of data rules for one write tool call, before it runs.
    history = earlier calls in this dialogue that ran without error: [{"name", "kwargs"}]."""
    if tool not in WRITE_TOOLS:
        return []
    res = data["reservations"].get(kw.get("reservation_id")) if "reservation_id" in kw else None
    uid = kw.get("user_id") or (res or {}).get("user_id")
    user = data["users"].get(uid)
    if "reservation_id" in kw and res is None:
        return []                      # the tool itself will say "reservation not found"
    if tool in ("book_reservation", "send_certificate") and user is None:
        return []                      # the tool itself will say "user not found"
    ctx = {"data": data, "kw": kw, "res": res, "user": user, "history": history}
    out = []
    for act in graph_actions_for(tool, kw, data, history):
        for k, cond in enumerate(graph[act]["conditions"]):
            failed = []
            if _eval(cond, f"{act}/c{k}", ctx, failed) is False:
                out.append({"action": act, "rule": f"{act}/c{k}",
                            "sources": sorted({s for _, s, _ in failed}),
                            "failed": [t for _, _, t in failed],
                            "if_fails": cond.get("if_fails", "deny")})
    return out


def message(violations):
    """What the agent sees when the validator blocks a call."""
    lines = ["Error: blocked by the policy validator. The call was NOT executed."]
    for v in violations:
        lines.append(f"- {v['action']} breaks {', '.join(v['sources'])}: " + "; ".join(v["failed"]))
    if any(v["if_fails"] == "transfer_to_human_agents" for v in violations):
        lines.append("Policy: transfer the user to a human agent.")
    else:
        lines.append("Policy: deny this request and tell the user why.")
    return "\n".join(lines)
