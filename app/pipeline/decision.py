"""Stage 9 - decide and act.

The decision is a deterministic function of the checks (no LLM in the loop), so every
outcome can be explained and audited. Precedence:
    reject  >  payment-security hold  >  return to vendor  >  human review  >  approve
"""
from __future__ import annotations

from datetime import timedelta

from app.pipeline.checks import (Q_AP, Q_BUYER, Q_CONTROLLER, Q_PROC, Q_SECURITY, Q_STORES, Check, Ctx, fmt_date, mask,
                                 rs)
from app.utils.textnorm import parse_date

LABELS = {"APPROVED": "Approved for payment", "NEEDS_REVIEW": "Needs review", "RETURNED": "Returned to vendor",
          "REJECTED": "Rejected", "ERROR": "Processing error"}
ASSIGNEES = {Q_SECURITY: "AP Controller", Q_PROC: "Vendor management desk", Q_STORES: "Stores in-charge",
             Q_AP: "AP team", Q_CONTROLLER: "Finance Controller"}


def approval_gate(ctx: Ctx) -> Check:
    total = ctx.fields.get("total")
    limit = ctx.policy["auto_approve_limit"]
    if total is not None and total > limit:
        return Check("gate.limit", "decide", "Within auto-approval limit", "fail",
                     f"Total {rs(total)} exceeds the auto-approval limit of {rs(limit)}.", "review", Q_CONTROLLER,
                     "above the auto-approval limit",
                     question=f"All checks passed - approve payment of {rs(total)}?")
    return Check("gate.limit", "decide", "Within auto-approval limit", "pass" if total is not None else "skip",
                 f"Total {rs(total)} is within the auto-approval limit of {rs(limit)}." if total is not None
                 else "No total available.")


def due_date(ctx: Ctx):
    f = ctx.fields
    due = parse_date(f.get("due_date"))
    if due:
        return due, "as stated on invoice"
    inv = parse_date(f.get("invoice_date"))
    if inv and ctx.vendor:
        days = ctx.vendor.get("payment_terms_days", 30)
        return inv + timedelta(days=days), f"invoice date + {days}-day vendor terms"
    return None, None


def decide(ctx: Ctx, checks: list[Check], read_summary: str) -> dict:
    f, v, po = ctx.fields, ctx.vendor, ctx.po
    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]

    def first(sev):
        return next((c for c in fails if c.severity == sev), None)

    priority = None
    if first("reject"):
        decision, primary = "REJECTED", first("reject")
    elif first("hold"):
        decision, primary, priority = "NEEDS_REVIEW", first("hold"), "High"
    elif first("return"):
        decision, primary = "RETURNED", first("return")
    elif first("review"):
        decision, primary, priority = "NEEDS_REVIEW", first("review"), "Normal"
    else:
        decision, primary = "APPROVED", None

    vendor_name = (v or {}).get("name") or f.get("vendor_name") or "Unknown vendor"
    total = f.get("total")
    due, due_basis = due_date(ctx)
    others = [c for c in fails if c is not primary]
    more = f" (+{len(others)} more)" if others else ""

    if decision == "APPROVED":
        headline = f"Approved for payment · {rs(total)} to {vendor_name}" + (f", due {fmt_date(due)}" if due else "")
        if ctx.po_inferred:
            headline += " · PO inferred"
    elif decision == "REJECTED":
        headline = f"Rejected · {primary.title}{more}"
    elif primary.severity == "hold":
        headline = f"Payment on hold · {primary.title}{more}"
    elif decision == "RETURNED":
        headline = f"Returned to vendor · {primary.title}{more}"
    else:
        headline = f"Needs review · {primary.title}{more}"

    queue = primary.queue if (primary and decision == "NEEDS_REVIEW") else None
    reasons = ([primary] if primary else []) + others
    result = {
        "decision": decision,
        "label": LABELS[decision],
        "headline": headline,
        "priority": priority,
        "queue": queue,
        "reasons": [{"check": c.id, "severity": c.severity, "message": c.message, "queue": c.queue} for c in reasons],
        "notes": [c.message for c in warns],
        "explanation": _explain(ctx, decision, primary, read_summary, due, due_basis),
        "outputs": [],
        "next_actions": [],
    }

    if decision == "APPROVED":
        mb = v["bank"]
        pos = ctx.po_position
        result["outputs"].append({
            "type": "payment", "title": "Payment instruction",
            "payee": v["name"], "vendor_id": v["vendor_id"], "amount": total, "currency": "INR",
            "due_date": due.isoformat() if due else None, "due_basis": due_basis,
            "bank": f"{mb['bank_name']} {mask(mb['account_number'])} · {mb['ifsc']}", "bank_source": "vendor master (verified)",
            "invoice_number": f.get("invoice_number"), "po_number": po["po_number"] if po else None,
            "cost_center": po.get("cost_center") if po else None,
            "allocations": ctx.allocations,
        })
        result["next_actions"] = [
            f"Posted to the AP ledger against PO {po['po_number']}" + (
                f" - utilisation {pos['util_before']:.0f}% → {pos['util_after']:.0f}%" if pos else "") if po else "Posted to the AP ledger",
            f"Payment of {rs(total)} scheduled for {fmt_date(due)} to the master bank account {mask(mb['account_number'])}",
        ]
        if ctx.po_inferred:
            result["next_actions"].append(f"Courtesy note: ask {vendor_name} to quote PO numbers on future invoices")
    elif decision == "NEEDS_REVIEW":
        review_fails = [c for c in fails if c.severity in ("hold", "review")]
        assignee = (po or {}).get("buyer") if queue == Q_BUYER and po else ASSIGNEES.get(queue, "AP team")
        precedent = next((c.evidence.get("precedent") for c in review_fails if c.evidence and c.evidence.get("precedent")), None)
        result["outputs"].append({
            "type": "review_task", "title": f"Review task → {queue}",
            "queue": queue, "assignee": assignee, "priority": priority,
            "questions": [c.question for c in review_fails if c.question],
            "items": [c.message for c in review_fails],
            "precedent": precedent,
            "options": ["Approve for payment", "Return to vendor", "Reject"],
        })
        result["next_actions"] = [f"Routed to {queue} ({assignee}), priority {priority}",
                                  "Payment blocked until a reviewer decides"]
        if primary.severity == "hold":
            result["next_actions"].insert(1, "Do NOT use the bank details on the invoice; verify by call-back to the master phone number")
    elif decision == "RETURNED":
        result["outputs"].append(_vendor_email(ctx, [c for c in fails if c.severity == "return"], vendor_name))
        result["next_actions"] = ["Correction request drafted to the vendor's master contact",
                                  "Invoice not booked; it will be re-processed when the corrected invoice arrives"]
    else:
        note = _rejection_note(ctx, primary, vendor_name)
        if note:
            result["outputs"].append(note)
        result["next_actions"] = ["Invoice not booked and will not be paid",
                                  "Courtesy note drafted to the vendor" if note else "No vendor communication needed"]
    return result


def _explain(ctx: Ctx, decision, primary, read_summary, due, due_basis) -> str:
    f, v, po = ctx.fields, ctx.vendor, ctx.po
    parts = [read_summary]
    if v:
        parts.append(f"Vendor {v['name']} is in the vendor master ({v['status']}).")
    if po:
        how = "inferred from the invoice contents" if ctx.po_inferred else "quoted on the invoice"
        parts.append(f"PO {po['po_number']} was {how}.")
    if decision == "APPROVED":
        parts.append("Arithmetic, GST, vendor, duplicate and PO checks all passed within policy tolerances, so the invoice "
                     f"was approved automatically and scheduled for payment on {fmt_date(due)} ({due_basis}).")
    elif primary:
        verb = {"reject": "Rejected", "hold": "Payment held", "return": "Returned to the vendor",
                "review": "Sent for human review"}[primary.severity]
        parts.append(f"Decision: {verb} - {primary.title}. The details and evidence are listed below.")
    return " ".join(parts)


def _vendor_email(ctx: Ctx, fails: list[Check], vendor_name: str) -> dict:
    f, v, co = ctx.fields, ctx.vendor, ctx.company
    to = (v or {}).get("email") or (f.get("emails") or ["(no verified contact - send via Procurement)"])[0]
    ref = f.get("invoice_number") or f"dated {fmt_date(f.get('invoice_date'))}"
    lines = [f"Dear {vendor_name} team,", "",
             f"Thank you for invoice {ref} for {rs(f.get('total') or 0)}. We are unable to process it in its current form:", ""]
    for i, c in enumerate(fails, 1):
        lines.append(f"  {i}. {c.vendor_fix or c.message}")
    po_no = (ctx.po or {}).get("po_number") or f.get("po_number")
    lines += ["", "Please send a corrected invoice to " + co["ap_email"] + (f", quoting our PO {po_no}." if po_no else "."),
              "The invoice has not been booked, so no credit note is needed for this version.", "",
              "Regards,", "Accounts Payable", co["name"]]
    return {"type": "vendor_email", "title": "Correction request to vendor", "to": to,
            "to_source": "vendor master" if v else "invoice (vendor not in master)",
            "subject": f"Invoice {ref} - correction needed before payment", "body": "\n".join(lines)}


def _rejection_note(ctx: Ctx, primary: Check, vendor_name: str) -> dict | None:
    f, v, co = ctx.fields, ctx.vendor, ctx.company
    if primary.id.startswith("dup.") and v:
        ev = primary.evidence or {}
        orig = ev.get("original") or f.get("invoice_number")
        body = (f"Dear {vendor_name} team,\n\nWe received invoice {f.get('invoice_number')} for {rs(f.get('total') or 0)}. "
                f"This is a copy of invoice {orig}, which is already in our system (reference run #{ev.get('run_id')}) and "
                f"will be paid per the agreed terms. No further action is needed, and please do not re-send it.\n\n"
                f"Regards,\nAccounts Payable\n{co['name']}")
        return {"type": "vendor_email", "title": "Duplicate notice to vendor", "to": v.get("email"), "to_source": "vendor master",
                "subject": f"Invoice {f.get('invoice_number')} - duplicate of {orig}", "body": body}
    if primary.id == "vendor.status" and v:
        body = (f"Dear {vendor_name} team,\n\nWe are unable to process invoice {f.get('invoice_number')} because your vendor "
                f"account with {co['name']} is currently inactive. Please contact our Procurement team to resolve this.\n\n"
                f"Regards,\nAccounts Payable\n{co['name']}")
        return {"type": "vendor_email", "title": "Notice to vendor", "to": v.get("email"), "to_source": "vendor master",
                "subject": f"Invoice {f.get('invoice_number')} - unable to process", "body": body}
    return None
