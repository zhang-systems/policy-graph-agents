# Runtime validator for tau2-bench telecom (the control arm, like 06G for airline).
# Only the agent-side DATA rules of the telecom policy graph can be checked in code:
#   P1  send_payment_request: the bill status is Overdue
#   P7  send_payment_request: no other bill of the customer is Awaiting Payment
#   S2  resume_line: the customer has no Overdue / Awaiting Payment bill left
#   S3  resume_line: the line's contract end date is not in the past
#   F1  refuel_data: at most 2 GB
# The user-side fixes (airplane mode, APN, ...) happen on the user's phone and the
# dialogue rules (confirm price, reboot after resume, ...) are only in the conversation:
# a validator cannot see them.
# A blocked call is returned to the agent as a REJECTED tool message (error=True,
# execution_status="rejected"), so tau2 skips it when it replays the dialogue for scoring.
# The gold run in the evaluator calls tools directly (make_tool_call), so the
# validator never touches the answer key.
from datetime import date, datetime

from tau2.data_model.message import ToolMessage
from tau2.environment.environment import Environment

TAG = "BLOCKED by policy validator"
CHECKED = {"send_payment_request", "resume_line", "refuel_data"}
_original = Environment.get_response


def _status(x):
    return getattr(x, "value", x)


def violations(tools, name, args):
    """List of (rule, reason) the call breaks; [] if fine or if it cannot be checked."""
    out = []
    try:
        if name == "send_payment_request":
            cust = tools.get_customer_by_id(args["customer_id"])
            bill = tools._get_bill_by_id(args["bill_id"])
            if _status(bill.status) != "Overdue":
                out.append(("P1", f"bill {bill.bill_id} is {_status(bill.status)}, not Overdue"))
            waiting = [b for b in cust.bill_ids
                       if b != args["bill_id"] and _status(tools._get_bill_by_id(b).status) == "Awaiting Payment"]
            if waiting:
                out.append(("P7", f"bill {waiting[0]} is already Awaiting Payment"))
        elif name == "resume_line":
            cust = tools.get_customer_by_id(args["customer_id"])
            unpaid = [b for b in cust.bill_ids
                      if _status(tools._get_bill_by_id(b).status) in ("Overdue", "Awaiting Payment")]
            if unpaid:
                out.append(("S2", f"overdue bill(s) not paid yet: {', '.join(unpaid)}"))
            line = tools._get_line_by_id(args["line_id"])
            end = line.contract_end_date
            if isinstance(end, str):
                end = date.fromisoformat(end[:10])
            if isinstance(end, datetime):
                end = end.date()
            if end is not None and end < tools._get_today():
                out.append(("S3", f"contract end date {end} is in the past"))
        elif name == "refuel_data":
            if float(args.get("gb_amount", 0)) > 2:
                out.append(("F1", f"{args['gb_amount']} GB is more than the 2 GB maximum"))
    except Exception:
        return []          # bad ids etc.: let the tool itself return its normal error
    return out


def get_response(self, tool_call):
    if tool_call.requestor == "assistant" and tool_call.name in CHECKED:
        v = violations(self.tools, tool_call.name, tool_call.arguments or {})
        if v:
            reason = "; ".join(f"[{r}] {why}" for r, why in v)
            return ToolMessage(id=tool_call.id, role="tool", requestor=tool_call.requestor,
                               content=f"{TAG}: {reason}. The call was not executed.",
                               error=True, execution_status="rejected")
    return _original(self, tool_call)


def install():
    Environment.get_response = get_response
