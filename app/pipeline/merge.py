"""Stage 3c - maker-checker reconciliation of the two readers.

Rule-based reader = exact on digital text, brittle on unseen layouts.
AI reader         = flexible on any layout and on scans, but can hallucinate.
Agreement raises confidence; disagreements are settled by hard evidence (checksums,
arithmetic, amount in words) - and if evidence cannot settle a money/identity field,
the invoice goes to a person instead of guessing.
"""
from __future__ import annotations

import copy

from rapidfuzz import fuzz

from app.utils import gstin as G
from app.utils.textnorm import norm_company, norm_invoice_number, norm_po_number

FIELDS = ["vendor_name", "vendor_gstin", "buyer_gstin", "invoice_number", "invoice_date", "due_date", "po_number",
          "subtotal", "tax_total", "total", "bank.account_number", "bank.ifsc"]
CRITICAL = {"vendor_gstin", "invoice_number", "invoice_date", "po_number", "subtotal", "total", "bank.account_number",
            "bank.ifsc"}
MONEY = {"subtotal", "tax_total", "total"}


def _get(d: dict, path: str):
    cur = d
    for p in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _set(d: dict, path: str, value) -> None:
    parts = path.split(".")
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = value


def _same(field: str, a, b) -> bool:
    if a is None or b is None:
        return False
    if field in MONEY:
        return abs(float(a) - float(b)) <= 1.0
    if field == "invoice_number":
        return norm_invoice_number(a) == norm_invoice_number(b)
    if field == "po_number":
        return (norm_po_number(a) or a) == (norm_po_number(b) or b)
    if field == "vendor_name":
        return fuzz.token_set_ratio(norm_company(a), norm_company(b)) >= 85
    return str(a).replace(" ", "").upper() == str(b).replace(" ", "").upper()


def _money_evidence(field: str, value, rules: dict, ai: dict) -> int:
    """How many independent facts on the invoice support this money value."""
    score = 0
    words = rules.get("amount_in_words_value")
    subs = [x for x in (rules.get("subtotal"), ai.get("subtotal")) if x is not None]
    taxes = [x for x in (rules.get("tax_total"), ai.get("tax_total")) if x is not None]
    lines_sum = None
    items = rules.get("line_items") or []
    if items and all(i.get("amount") is not None for i in items):
        lines_sum = sum(i["amount"] for i in items)
    if field == "total":
        if words is not None and abs(words - value) <= 1:
            score += 2
        if any(abs(s + t - value) <= 1 for s in subs for t in taxes):
            score += 1
    if field == "subtotal" and lines_sum is not None and abs(lines_sum - value) <= 1:
        score += 2
    return score


def merge(rules: dict, ai: dict | None, doc_method: str) -> tuple[dict, list[dict], list[dict]]:
    """Returns (merged_fields, comparison_rows, unresolved_conflicts)."""
    merged = copy.deepcopy(rules)
    src = rules.get("field_sources", {})
    rows, conflicts = [], []
    for field in FIELDS:
        r = _get(rules, field)
        conf = (src.get(field.split(".")[0]) or {}).get("confidence")
        if ai is None:
            rows.append({"field": field, "rules": r, "ai": None, "value": r,
                         "status": "rules only" if r is not None else "not found", "confidence": conf})
            continue
        a = _get(ai, field)
        row = {"field": field, "rules": r, "ai": a}
        if r is None and a is None:
            row.update(value=None, status="not found (both readers)", confidence=None)
        elif a is None:
            row.update(value=r, status="rules only", confidence=conf)
        elif r is None:
            ok = True
            if field in ("vendor_gstin", "buyer_gstin"):
                ok = G.is_valid(a)
            if ok:
                _set(merged, field, a)
                row.update(value=a, status="AI only", confidence=0.75)
            else:
                row.update(value=None, status="AI value rejected (invalid)", confidence=None)
        elif _same(field, r, a):
            row.update(value=r, status="agree", confidence=max(conf or 0.9, 0.97))
        else:
            winner, why = None, None
            if field in MONEY:
                er, ea = _money_evidence(field, r, rules, ai), _money_evidence(field, a, rules, ai)
                if er != ea:
                    winner, why = (r, "rules") if er > ea else (a, "AI")
                    why = f"{why} value is supported by the invoice's own arithmetic / amount in words"
            elif field in ("vendor_gstin", "buyer_gstin"):
                vr, va = G.is_valid(r), G.is_valid(a)
                if vr != va:
                    winner, why = (r, "rules value passes the GSTIN checksum") if vr else (a, "AI value passes the GSTIN checksum")
            elif field == "vendor_name":
                winner, why = r, "names differ only cosmetically; vendor is matched by GSTIN"
            elif doc_method == "text-layer":
                winner, why = r, "digital PDF: the rule-based reader copies the exact printed characters"
            elif field in ("invoice_date", "due_date"):
                winner, why = a, "scanned document: AI vision preferred over OCR digits for dates"
            if winner is not None:
                _set(merged, field, winner)
                row.update(value=winner, status="resolved", confidence=0.85, reason=why)
            else:
                row.update(value=r, status="conflict", confidence=0.4,
                           reason="no hard evidence favours either reader")
                if field in CRITICAL:
                    conflicts.append({"field": field, "rules": r, "ai": a})
        rows.append(row)

    if ai is not None:
        ri, ai_items = rules.get("line_items") or [], ai.get("line_items") or []
        if not ri and ai_items:
            merged["line_items"] = ai_items
            rows.append({"field": "line_items", "rules": 0, "ai": len(ai_items), "value": len(ai_items), "status": "AI only"})
        elif ri and ai_items:
            sr = round(sum(i.get("amount") or 0 for i in ri), 2)
            sa = round(sum(i.get("amount") or 0 for i in ai_items), 2)
            status = "agree" if len(ri) == len(ai_items) and abs(sr - sa) <= 1 else "rules kept"
            sub = merged.get("subtotal")
            if status != "agree" and sub is not None and abs(sa - sub) <= 1 and abs(sr - sub) > 1:
                merged["line_items"] = ai_items
                status = "AI kept (lines sum to subtotal)"
            rows.append({"field": "line_items", "rules": f"{len(ri)} lines / {sr:,.2f}", "ai": f"{len(ai_items)} lines / {sa:,.2f}",
                         "value": f"{len(merged['line_items'])} lines", "status": status})
        if not rules.get("taxes") and ai.get("taxes"):
            merged["taxes"] = ai["taxes"]
            merged["tax_total"] = ai.get("tax_total")
        merged["emails"] = list(dict.fromkeys((rules.get("emails") or []) + (ai.get("emails") or [])))
    return merged, rows, conflicts
