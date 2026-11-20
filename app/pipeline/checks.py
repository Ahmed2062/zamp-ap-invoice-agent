"""Stages 4-8: deterministic, auditable checks.

Each check yields a `Check` with a plain-English message and evidence. A failed check
carries a severity that the decision engine maps to an outcome:
    reject  -> invoice must not be processed (duplicate, blocked vendor)
    hold    -> payment-security hold (possible fraud) - needs AP controller
    return  -> vendor must fix and re-issue the invoice
    review  -> a person must make a judgement call (buyer, stores, procurement, AP)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal

from rapidfuzz import fuzz

from app.utils import gstin as G
from app.utils.money import inr, q2
from app.utils.textnorm import norm_invoice_number, norm_text, parse_date

ACTIVE = ("APPROVED", "NEEDS_REVIEW")
FREE_MAIL = {"gmail.com", "yahoo.com", "yahoo.co.in", "outlook.com", "hotmail.com", "rediffmail.com"}

Q_AP = "AP team"
Q_BUYER = "Buyer (Procurement)"
Q_STORES = "Stores / Receiving"
Q_PROC = "Procurement - vendor management"
Q_SECURITY = "AP Controller - payment security"
Q_CONTROLLER = "Finance Controller"


def D(x) -> Decimal:
    return Decimal(str(x))


def rs(x) -> str:
    return f"₹{inr(x)}"


def fmt_date(d: str | date | None) -> str:
    dt = parse_date(d) if not isinstance(d, date) else d
    return dt.strftime("%d %b %Y") if dt else "-"


def mask(acct: str | None) -> str:
    return f"…{acct[-4:]}" if acct else "-"


@dataclass
class Check:
    id: str
    stage: str
    label: str
    status: str                 # pass | warn | fail | skip
    message: str
    severity: str = "info"      # review | return | reject | hold (for fails)
    queue: str | None = None
    title: str | None = None    # short phrase used in the decision headline
    evidence: dict | None = None
    vendor_fix: str | None = None
    question: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class Ctx:
    """Everything a run knows. Filled in progressively by the stages."""
    run_id: int
    ref: object
    db: object
    today: date
    sha256: str = ""
    fields: dict = field(default_factory=dict)
    vendor: dict | None = None
    po: dict | None = None
    po_inferred: bool = False
    po_candidates: list = field(default_factory=list)
    allocations: list = field(default_factory=list)
    line_matches: list = field(default_factory=list)
    po_position: dict = field(default_factory=dict)
    read_method: str | None = None
    doc_pages: int = 1

    @property
    def policy(self) -> dict:
        return self.ref.policy

    @property
    def company(self) -> dict:
        return self.ref.company


# ======================================================================== validate
def validate_invoice(ctx: Ctx) -> list[Check]:
    f, pol = ctx.fields, ctx.policy
    tol = D(pol["rounding_tolerance"])
    st = "validate"
    out: list[Check] = []
    items = f.get("line_items") or []
    taxes = f.get("taxes") or []
    tax_total = sum((D(t["amount"]) for t in taxes), Decimal("0")) if taxes else None

    # Is this actually a payable tax invoice, in a currency we can pay automatically?
    doc_type = f.get("document_type") or "invoice"
    if doc_type in ("credit note", "debit note"):
        out.append(Check("doc.type", st, "Document is a tax invoice", "fail",
                         f"This is a {doc_type}, not an invoice - it adjusts an earlier invoice and needs manual matching.",
                         "review", Q_AP, f"document is a {doc_type}",
                         question="Which original invoice does this adjust? Book it against that invoice."))
    elif doc_type != "invoice":
        out.append(Check("doc.type", st, "Document is a tax invoice", "fail",
                         f"This is a {doc_type}, not a tax invoice - it cannot be booked or paid.", "return", Q_AP,
                         f"document is a {doc_type}, not a tax invoice",
                         vendor_fix=f"We received a {doc_type}; please send the final GST tax invoice."))
    cur = f.get("currency")
    if cur and cur != "INR":
        out.append(Check("doc.currency", st, "Invoice currency is INR", "fail",
                         f"Invoice is in {cur}. Only INR invoices are processed automatically - foreign-currency "
                         f"payments need treasury (FX rate, remittance docs).", "review", Q_AP, f"invoice in {cur}",
                         question=f"Confirm the {cur} amount, FX treatment and PO currency before booking."))
    if ctx.doc_pages > 3:
        out.append(Check("doc.pages", st, "Whole document read", "fail",
                         f"Only the first 3 of {ctx.doc_pages} pages were read - later pages could change the amount.",
                         "review", Q_AP, "document longer than 3 pages",
                         question="Check the remaining pages for extra lines, charges or terms."))

    # Taxable value: required. Derive it only from corroborating figures on the invoice.
    if f.get("subtotal") is None:
        if items and all(it.get("amount") is not None for it in items):
            derived = sum((D(it["amount"]) for it in items), Decimal("0"))
            f["subtotal"] = float(derived)
            out.append(Check("math.subtotal_derived", st, "Taxable value stated", "warn",
                             f"No labelled taxable value; derived {rs(derived)} from the line amounts."))
        elif f.get("total") is not None and tax_total is not None:
            derived = D(f["total"]) - tax_total
            f["subtotal"] = float(derived)
            out.append(Check("math.subtotal_derived", st, "Taxable value stated", "warn",
                             f"No labelled taxable value; derived {rs(derived)} as total minus GST."))
        else:
            out.append(Check("math.subtotal_missing", st, "Taxable value stated", "fail",
                             "The taxable value (amount before GST) could not be determined from the invoice.", "review",
                             Q_AP, "taxable value unclear", question="What is the taxable value of this invoice?"))
    subtotal = D(f["subtotal"]) if f.get("subtotal") is not None else None
    total = D(f["total"]) if f.get("total") is not None else None

    # Line arithmetic
    priced = [(i, it) for i, it in enumerate(items, 1)
              if it.get("quantity") is not None and it.get("unit_price") is not None and it.get("amount") is not None]
    bad = []
    for i, it in priced:
        exp = q2(D(it["quantity"]) * D(it["unit_price"]))
        if abs(exp - D(it["amount"])) > tol:
            bad.append(f"line {i}: {it['quantity']:g} × {inr(it['unit_price'])} = {inr(exp)}, invoice shows {inr(it['amount'])}")
    if not priced:
        out.append(Check("math.lines", st, "Line amounts = qty × rate", "skip",
                         "No itemised quantities/rates to check (bundled or service invoice)."))
    elif bad:
        out.append(Check("math.lines", st, "Line amounts = qty × rate", "fail", "Line arithmetic is wrong: " + "; ".join(bad),
                         "return", Q_AP, "line amounts don't add up", vendor_fix="Correct the line amounts: " + "; ".join(bad)))
    else:
        out.append(Check("math.lines", st, "Line amounts = qty × rate", "pass",
                         f"All {len(priced)} line amounts equal quantity × rate."))

    # Subtotal = sum of lines
    if items and subtotal is not None and all(it.get("amount") is not None for it in items):
        s = sum((D(it["amount"]) for it in items), Decimal("0"))
        if abs(s - subtotal) > tol:
            out.append(Check("math.subtotal", st, "Lines add up to subtotal", "fail",
                             f"Lines sum to {rs(s)} but the invoice subtotal is {rs(subtotal)}.", "return", Q_AP,
                             "subtotal doesn't match the lines",
                             vendor_fix=f"Subtotal should be {rs(s)} (sum of lines), invoice shows {rs(subtotal)}."))
        else:
            out.append(Check("math.subtotal", st, "Lines add up to subtotal", "pass", f"Lines sum to the subtotal {rs(subtotal)}."))

    # Subtotal + tax = total
    if subtotal is not None and total is not None and tax_total is not None:
        exp = subtotal + tax_total
        if abs(exp - total) > tol:
            out.append(Check("math.total", st, "Subtotal + GST = total", "fail",
                             f"{rs(subtotal)} + GST {rs(tax_total)} = {rs(exp)}, but the invoice total is {rs(total)}.",
                             "return", Q_AP, "total doesn't add up",
                             vendor_fix=f"Invoice total should be {rs(exp)}; it shows {rs(total)}."))
        else:
            out.append(Check("math.total", st, "Subtotal + GST = total", "pass",
                             f"{rs(subtotal)} + GST {rs(tax_total)} = {rs(total)}."))
    elif total is None:
        out.append(Check("math.total", st, "Subtotal + GST = total", "skip", "No invoice total available."))

    # Total in words
    words = f.get("amount_in_words_value")
    if words is not None and total is not None:
        if abs(D(words) - total) > tol:
            out.append(Check("math.words", st, "Total in figures = total in words", "fail",
                             f"Total in figures is {rs(total)} but in words it reads {rs(words)}.", "return", Q_AP,
                             "amount in words contradicts the total",
                             vendor_fix=f"Amount in words ({rs(words)}) does not match the total in figures ({rs(total)})."))
        else:
            out.append(Check("math.words", st, "Total in figures = total in words", "pass",
                             f"'{f.get('amount_in_words')}' = {rs(total)}."))

    # GST amounts and rates
    if taxes and subtotal is not None:
        wrong = []
        nonstd = []
        valid_rates = set(pol["standard_gst_rates"])
        for t in taxes:
            rate = t.get("rate")
            if rate is None:
                continue
            full_rate = rate * 2 if t["type"] in ("CGST", "SGST", "UTGST") else rate
            if full_rate not in valid_rates:
                nonstd.append(f"{t['type']} {rate:g}%")
            exp = q2(subtotal * D(rate) / 100)
            if abs(exp - D(t["amount"])) > tol:
                implied = q2(D(t["amount"]) * 100 / D(rate))
                wrong.append(f"{t['type']} @ {rate:g}% should be {rs(exp)} on taxable value {rs(subtotal)}, invoice charges "
                             f"{rs(t['amount'])} (computed on {rs(implied)})")
        if wrong:
            out.append(Check("tax.amount", st, "GST computed on the taxable value", "fail", "; ".join(wrong), "return", Q_AP,
                             "GST computed on the wrong base", vendor_fix="Recompute GST: " + "; ".join(wrong)))
        else:
            out.append(Check("tax.amount", st, "GST computed on the taxable value", "pass",
                             "Each GST component equals rate × taxable value."))
        if nonstd:
            out.append(Check("tax.rate", st, "Standard GST rate", "warn", f"Non-standard GST rate(s): {', '.join(nonstd)}."))

    # GST type vs place of supply
    vg = f.get("vendor_gstin")
    types = {t["type"] for t in taxes}
    if vg and G.is_valid(vg) and types:
        intra = vg[:2] == ctx.company["state_code"]
        expected = {"CGST", "SGST"} if intra else {"IGST"}
        supply = (f"intra-state ({G.STATE_CODES.get(vg[:2], vg[:2])} → {ctx.company['state']})" if intra else
                  f"inter-state ({G.STATE_CODES.get(vg[:2], vg[:2])} → {ctx.company['state']})")
        if types != expected:
            out.append(Check("tax.type", st, "Correct GST type for place of supply", "fail",
                             f"Supply is {supply}, so {' + '.join(sorted(expected))} applies; invoice charges "
                             f"{' + '.join(sorted(types))}. Input tax credit would be blocked.", "return", Q_AP,
                             "wrong GST type for the place of supply",
                             vendor_fix=f"Re-issue with {' + '.join(sorted(expected))} ({supply})."))
        else:
            out.append(Check("tax.type", st, "Correct GST type for place of supply", "pass",
                             f"Supply is {supply}; {' + '.join(sorted(types))} charged correctly."))

    # GSTINs
    if vg:
        repaired = "OCR repaired" in (f.get("field_sources", {}).get("vendor_gstin", {}).get("method", ""))
        out.append(Check("gst.vendor_gstin", st, "Supplier GSTIN valid", "warn" if repaired else "pass",
                         f"{vg} passes the GSTIN checksum" + (" (after OCR repair)." if repaired else ".")))
    elif f.get("invalid_gstins"):
        bad = f["invalid_gstins"][0]
        out.append(Check("gst.vendor_gstin", st, "Supplier GSTIN valid", "fail",
                         f"The supplier GSTIN printed on the invoice ({bad}) fails the GSTIN checksum - it is mistyped, "
                         f"and input tax credit would be rejected.", "return", Q_AP, "supplier GSTIN is invalid",
                         vendor_fix=f"Your GSTIN on the invoice ({bad}) is not valid - please correct it."))
    bg = f.get("buyer_gstin")
    if not bg:
        out.append(Check("gst.buyer_gstin", st, f"Billed to our GSTIN", "fail",
                         f"Our GSTIN {ctx.company['gstin']} does not appear on the invoice - input tax credit cannot be claimed.",
                         "return", Q_AP, "our GSTIN is missing",
                         vendor_fix=f"Add the buyer GSTIN {ctx.company['gstin']} ({ctx.company['name']})."))
    elif bg != ctx.company["gstin"]:
        out.append(Check("gst.buyer_gstin", st, "Billed to our GSTIN", "fail",
                         f"Invoice is addressed to GSTIN {bg}, not ours ({ctx.company['gstin']}).", "return", Q_AP,
                         "billed to the wrong GSTIN", vendor_fix=f"Bill to {ctx.company['name']}, GSTIN {ctx.company['gstin']}."))
    else:
        out.append(Check("gst.buyer_gstin", st, "Billed to our GSTIN", "pass", f"Addressed to {ctx.company['name']} ({bg})."))

    # Dates
    inv_date = parse_date(f.get("invoice_date"))
    if inv_date and (f.get("field_sources", {}).get("invoice_date", {}).get("method", "")).startswith("first date"):
        out.append(Check("date.unlabelled", st, "Invoice date clearly stated", "fail",
                         f"No labelled invoice date; the first date on the page ({fmt_date(inv_date)}) was used, which "
                         f"could be a PO or delivery date.", "review", Q_AP, "invoice date not clearly stated",
                         question="Confirm the invoice date - it drives GST credit timing and the payment due date."))
    if inv_date:
        if inv_date > ctx.today:
            out.append(Check("date.future", st, "Invoice date not in the future", "fail",
                             f"Invoice is dated {fmt_date(inv_date)}, after today ({fmt_date(ctx.today)}).", "return", Q_AP,
                             "invoice is future-dated", vendor_fix="Correct the invoice date."))
        else:
            age = (ctx.today - inv_date).days
            if age > pol["invoice_max_age_days"]:
                out.append(Check("date.age", st, "Invoice age", "warn",
                                 f"Invoice is {age} days old (policy flags > {pol['invoice_max_age_days']} days)."))
            else:
                out.append(Check("date.age", st, "Invoice date sensible", "pass",
                                 f"Dated {fmt_date(inv_date)} ({age} day{'s' if age != 1 else ''} ago)."))
        due = parse_date(f.get("due_date"))
        if due and due < inv_date:
            out.append(Check("date.due", st, "Due date after invoice date", "warn", f"Due date {fmt_date(due)} is before the invoice date."))
    return out


# ======================================================================== vendor
def _domain(email: str) -> str:
    return email.split("@")[-1].lower()


def verify_vendor(ctx: Ctx) -> list[Check]:
    f, ref = ctx.fields, ctx.ref
    st = "vendor"
    out: list[Check] = []
    v = ref.vendor_by_gstin(f.get("vendor_gstin"))
    how = "GSTIN"
    if not v:
        v, score = ref.vendor_by_name(f.get("vendor_name"))
        how = f"name ({score}% similar)"
    if not v:
        out.append(Check("vendor.known", st, "Vendor in vendor master", "fail",
                         f"'{f.get('vendor_name') or 'Unknown vendor'}' (GSTIN {f.get('vendor_gstin') or 'not found'}) is not in the "
                         f"vendor master. Payments can only go to onboarded vendors.", "review", Q_PROC,
                         "vendor is not onboarded",
                         question="Is this a genuine new supplier? If so, onboard them (KYC, bank verification) before this "
                                  "invoice can be paid; otherwise reject."))
        return out
    ctx.vendor = v
    out.append(Check("vendor.known", st, "Vendor in vendor master", "pass",
                     f"Matched {v['vendor_id']} · {v['name']} by {how}.",
                     evidence={"vendor_id": v["vendor_id"], "matched_by": how}))

    if v["status"] == "blocked":
        out.append(Check("vendor.status", st, "Vendor is active", "fail",
                         f"{v['name']} is BLOCKED: {v.get('block_reason', 'no reason recorded')}", "reject", Q_PROC,
                         "vendor is blocked"))
    elif v["status"] != "approved":
        out.append(Check("vendor.status", st, "Vendor is active", "fail", f"Vendor status is '{v['status']}'.", "review",
                         Q_PROC, f"vendor status is {v['status']}"))
    else:
        out.append(Check("vendor.status", st, "Vendor is active", "pass", "Vendor is approved for payment."))

    ig = f.get("vendor_gstin")
    if ig and ig != v["gstin"]:
        out.append(Check("vendor.gstin", st, "GSTIN matches master", "fail",
                         f"Invoice GSTIN {ig} differs from master GSTIN {v['gstin']}.", "review", Q_PROC,
                         "GSTIN differs from the vendor master",
                         question="Has the vendor registered a new GSTIN (e.g. new state)? Update the master or reject."))
    elif ig:
        out.append(Check("vendor.gstin", st, "GSTIN matches master", "pass", f"{ig} matches the vendor master."))

    bank = f.get("bank") or {}
    mb = v["bank"]
    if bank.get("account_number") or bank.get("ifsc"):
        diffs = []
        if bank.get("account_number") and bank["account_number"] != mb["account_number"]:
            diffs.append(f"account {mask(bank['account_number'])} vs master {mask(mb['account_number'])}")
        if bank.get("ifsc") and bank["ifsc"] != mb["ifsc"]:
            diffs.append(f"IFSC {bank['ifsc']} vs master {mb['ifsc']}")
        if diffs:
            out.append(Check(
                "vendor.bank", st, "Bank details match master", "fail",
                f"Invoice asks for payment to {bank.get('bank_name') or 'a different bank'} ({'; '.join(diffs)}). "
                f"Master has {mb['bank_name']} {mask(mb['account_number'])}. Bank-detail changes on an invoice are the "
                f"most common payment-diversion fraud pattern.", "hold", Q_SECURITY, "bank details differ from the vendor master",
                evidence={"invoice_bank": bank, "master_bank": {k: mb[k] for k in ('bank_name', 'ifsc')} | {"account": mask(mb["account_number"])}},
                question=f"Call {v['name']} on the number in the vendor master ({v.get('phone')}) - not any number on the "
                         f"invoice - to confirm the change. Only then update the master via the vendor-change process."))
        else:
            out.append(Check("vendor.bank", st, "Bank details match master", "pass",
                             f"{mb['bank_name']} {mask(mb['account_number'])} / {mb['ifsc']} matches the vendor master."))
    else:
        out.append(Check("vendor.bank", st, "Bank details match master", "pass",
                         "No bank details on the invoice - payment will use the verified master account."))

    emails = f.get("emails") or []
    master = (v.get("email") or "").lower()
    foreign = []
    for e in emails:
        el = e.lower()
        if el == master:
            continue
        if _domain(el) in FREE_MAIL or _domain(master) in FREE_MAIL or _domain(el) != _domain(master):
            foreign.append(e)
    if foreign:
        out.append(Check("vendor.contact", st, "Contact details match master", "warn",
                         f"Invoice lists {', '.join(foreign)}, which is not the master contact ({v.get('email')}). "
                         f"Replies and remittance advice go only to the master contact."))
    elif emails:
        out.append(Check("vendor.contact", st, "Contact details match master", "pass", f"Contact {emails[0]} matches master."))
    return out


# ======================================================================== duplicates
def check_duplicates(ctx: Ctx) -> list[Check]:
    f, db, pol = ctx.fields, ctx.db, ctx.policy
    st = "duplicate"
    out: list[Check] = []
    found_any = False
    for r in db.runs_with_hash(ctx.sha256, ctx.run_id):
        final = r["final_decision"] or r["decision"]
        if final in ACTIVE:
            found_any = True
            out.append(Check("dup.file", st, "Same file not processed before", "fail",
                             f"This exact file (same SHA-256 fingerprint) was already processed in run #{r['id']} "
                             f"({final.replace('_', ' ').lower()}).", "reject", Q_AP,
                             f"exact duplicate of run #{r['id']}", evidence={"run_id": r["id"]}))
            break
    if not found_any:
        out.append(Check("dup.file", st, "Same file not processed before", "pass", "File fingerprint not seen before."))

    vendor_key = (ctx.vendor or {}).get("vendor_id") or f.get("vendor_gstin")
    inv_norm = norm_invoice_number(f.get("invoice_number"))
    if not vendor_key:
        out.append(Check("dup.number", st, "Invoice number not already booked", "skip", "Vendor unknown - cannot scope the search."))
        return out
    prior = db.runs_for_vendor(vendor_key, ctx.run_id)
    num_hit = None
    resubmission = None
    if inv_norm:
        for p in prior:
            if p["invoice_number_norm"] == inv_norm:
                final = p["final_decision"] or p["decision"]
                if final in ACTIVE:
                    num_hit = p
                    break
                resubmission = p
    if num_hit:
        final = num_hit["final_decision"] or num_hit["decision"]
        same_text = (num_hit["invoice_number"] or "") == (f.get("invoice_number") or "")
        detail = (f"Invoice number '{f.get('invoice_number')}' normalises to {inv_norm}, the same as "
                  f"'{num_hit['invoice_number']}' from run #{num_hit['id']}" if not same_text else
                  f"Invoice {num_hit['invoice_number']} was already received in run #{num_hit['id']}")
        out.append(Check("dup.number", st, "Invoice number not already booked", "fail",
                         f"{detail} ({final.replace('_', ' ').lower()}, {fmt_date(num_hit['invoice_date'])}, "
                         f"{rs(num_hit['total'] or 0)}). Paying it again would be a duplicate payment.", "reject", Q_AP,
                         f"duplicate of {num_hit['invoice_number']} (run #{num_hit['id']})",
                         evidence={"run_id": num_hit["id"], "normalised": inv_norm, "original": num_hit["invoice_number"]}))
    elif inv_norm:
        msg = f"No earlier invoice from this vendor normalises to {inv_norm}."
        if resubmission:
            msg += (f" (Run #{resubmission['id']} had the same number but was "
                    f"{(resubmission['final_decision'] or resubmission['decision']).lower()} - treating this as the corrected resubmission.)")
        out.append(Check("dup.number", st, "Invoice number not already booked", "pass", msg))

    total = f.get("total")
    inv_date = parse_date(f.get("invoice_date"))
    if total is not None and inv_date and not num_hit:
        for p in prior:
            final = p["final_decision"] or p["decision"]
            pd = parse_date(p["invoice_date"])
            if (final in ACTIVE and p["total"] is not None and abs(D(p["total"]) - D(total)) <= 1 and pd
                    and abs((pd - inv_date).days) <= pol["fuzzy_duplicate_window_days"]
                    and p["invoice_number_norm"] != inv_norm):
                out.append(Check("dup.fuzzy", st, "No near-duplicate (same amount & date)", "fail",
                                 f"Same amount ({rs(total)}) and date within {pol['fuzzy_duplicate_window_days']} days as "
                                 f"{p['invoice_number']} (run #{p['id']}), but a different number.", "review", Q_AP,
                                 f"possible duplicate of {p['invoice_number']}",
                                 question="Is this a re-issued copy of the earlier invoice, or a genuinely separate supply?"))
                break
        else:
            out.append(Check("dup.fuzzy", st, "No near-duplicate (same amount & date)", "pass",
                             f"No other invoice of {rs(total)} within ±{pol['fuzzy_duplicate_window_days']} days."))
    return out


# ======================================================================== PO matching
def _consumption(db, po_number: str) -> tuple[dict[int, float], Decimal, list]:
    per_line: dict[int, float] = {}
    value = Decimal("0")
    entries = db.ledger_for_po(po_number)
    for e in entries:
        value += D(e["amount_ex_tax"] or 0)
        for a in e["allocations"]:
            if a.get("po_line"):
                per_line[a["po_line"]] = per_line.get(a["po_line"], 0) + float(a.get("qty") or 0)
    return per_line, value, entries


def _best_po_line(item: dict, po: dict):
    """Best PO line by description; an exact price only strengthens an already-plausible description match."""
    best, best_sim = None, 0
    for ln in po["lines"]:
        sim = fuzz.token_set_ratio(norm_text(item.get("description")), norm_text(ln["description"]))
        if sim >= 50 and item.get("unit_price") and abs(item["unit_price"] - ln["unit_price"]) < 0.01:
            sim = min(100, sim + 10)
        if sim > best_sim:
            best, best_sim = ln, sim
    return best, best_sim


def score_po(ctx: Ctx, po: dict) -> dict:
    f = ctx.fields
    items = [it for it in (f.get("line_items") or []) if it.get("description")]
    subtotal = f.get("subtotal") or (sum(it.get("amount") or 0 for it in items) or None)
    _, consumed, _ = _consumption(ctx.db, po["po_number"])
    remaining = D(po["value_ex_tax"]) - consumed
    s_amount = 0.0
    if subtotal is not None and remaining > 0:
        s_amount = 1.0 if D(subtotal) <= remaining * D("1.02") else max(0.0, float(1 - (D(subtotal) - remaining) / D(subtotal)))
    if items:
        matched, price_scores = 0, []
        for it in items:
            ln, sim = _best_po_line(it, po)
            if ln and sim >= 70:
                matched += 1
                if it.get("unit_price"):
                    dev = abs(it["unit_price"] - ln["unit_price"]) / ln["unit_price"]
                    price_scores.append(max(0.0, 1 - dev / 0.10))
                else:
                    price_scores.append(0.5)
        s_lines = matched / len(items)
        s_price = sum(price_scores) / len(price_scores) if price_scores else 0.0
        score = 0.5 * s_lines + 0.3 * s_price + 0.2 * s_amount
        parts = {"lines": round(s_lines, 2), "price": round(s_price, 2), "amount": round(s_amount, 2)}
    else:
        # Without line items the only evidence is the amount - never enough for an automatic match.
        score = 0.6 * s_amount
        parts = {"amount": round(s_amount, 2), "note": "no line items"}
    return {"po_number": po["po_number"], "score": round(score, 2), "parts": parts,
            "remaining_value": float(remaining), "po_date": po["po_date"]}


def match_po(ctx: Ctx) -> list[Check]:
    f, ref, pol = ctx.fields, ctx.ref, ctx.policy
    st = "po_match"
    out: list[Check] = []
    v = ctx.vendor
    po_no = f.get("po_number")
    if po_no:
        po = ref.po(po_no)
        how = "quoted on the invoice" if f.get("po_reference_type") == "labelled" else "found in the invoice text (unlabelled)"
        if not po:
            out.append(Check("po.exists", st, "PO exists", "fail", f"PO {po_no} ({how}) does not exist in the procurement system.",
                             "review", Q_AP, f"PO {po_no} not found",
                             question="Ask the requester for the correct PO, or confirm this is a non-PO purchase."))
            return out
        if v and po["vendor_id"] != v["vendor_id"]:
            owner = ref.vendor(po["vendor_id"])
            out.append(Check("po.vendor", st, "PO belongs to this vendor", "fail",
                             f"PO {po_no} was raised on {owner['name'] if owner else po['vendor_id']}, not {v['name']}.",
                             "review", Q_BUYER, f"PO {po_no} belongs to another vendor"))
            return out
        if po["status"] != "open":
            out.append(Check("po.status", st, "PO is open", "fail", f"PO {po_no} is {po['status']}.", "review", Q_BUYER,
                             f"PO {po_no} is {po['status']}"))
            return out
        ctx.po = po
        out.append(Check("po.exists", st, "PO exists, is open and belongs to vendor", "pass",
                         f"PO {po_no} ({how}) - raised {fmt_date(po['po_date'])} by {po['requester']}, buyer {po['buyer']}, "
                         f"value {rs(po['value_ex_tax'])} + GST, type: {po['type']}.",
                         evidence={"po_number": po_no}))
        return out

    if not v:
        out.append(Check("po.exists", st, "PO identified", "skip", "No PO number and vendor unknown - cannot infer a PO."))
        return out
    candidates = [score_po(ctx, p) for p in ref.open_pos_for_vendor(v["vendor_id"])]
    candidates.sort(key=lambda c: c["score"], reverse=True)
    ctx.po_candidates = candidates
    if not candidates:
        out.append(Check("po.inferred", st, "PO identified", "fail",
                         f"No PO number on the invoice and {v['name']} has no open POs. Non-PO spend needs budget-owner approval.",
                         "review", Q_BUYER, "no PO and no open PO for this vendor",
                         question="Was this purchased without a PO? Get budget-owner approval or ask the requester to raise a PO."))
        return out
    best = candidates[0]
    second = candidates[1]["score"] if len(candidates) > 1 else 0.0
    margin = best["score"] - second
    table = ", ".join(f"{c['po_number']}: {c['score']:.2f}" for c in candidates[:4])
    if best["score"] >= pol["po_inference_min_score"] and margin >= pol["po_inference_min_margin"]:
        ctx.po = ref.po(best["po_number"])
        ctx.po_inferred = True
        out.append(Check("po.inferred", st, "PO identified", "warn",
                         f"No PO number on the invoice. Inferred PO {best['po_number']} from vendor, items, prices and "
                         f"amount (match score {best['score']:.2f}; next best {second:.2f}). Candidates - {table}.",
                         evidence={"candidates": candidates[:4]}))
    else:
        out.append(Check("po.inferred", st, "PO identified", "fail",
                         f"No PO number on the invoice and no PO is a clear match (best {best['score']:.2f}, margin "
                         f"{margin:.2f}). Candidates - {table}.", "review", Q_AP, "PO could not be determined",
                         evidence={"candidates": candidates[:4]},
                         question="Which PO does this invoice bill? Ask the vendor to quote the PO number on future invoices."))
    return out


# ======================================================================== reconcile
def reconcile(ctx: Ctx) -> list[Check]:
    f, pol, ref = ctx.fields, ctx.policy, ctx.ref
    st = "reconcile"
    po = ctx.po
    if not po:
        return [Check("recon.skip", st, "2/3-way match", "skip", "No PO matched - line reconciliation not possible.")]
    out: list[Check] = []
    tol_pct = D(pol["price_tolerance_pct"])
    consumed_qty, consumed_value, entries = _consumption(ctx.db, po["po_number"])
    received = ref.received_qty(po["po_number"])
    is_goods = po["type"] == "goods"
    items = f.get("line_items") or []
    subtotal = D(f["subtotal"]) if f.get("subtotal") is not None else sum((D(i.get("amount") or 0) for i in items), Decimal("0"))
    po_lines = {ln["line_no"]: ln for ln in po["lines"]}
    allocations, rows = [], []
    price_fail, qty_fail, grn_fail, unmatched = [], [], [], []
    excess_total = Decimal("0")

    itemised = bool(items) and all(it.get("quantity") is not None and it.get("unit_price") is not None for it in items)
    this_invoice_qty: dict[int, Decimal] = {}
    if itemised:
        for it in items:
            ln, sim = _best_po_line(it, po)
            if not ln or sim < 60:
                unmatched.append(it.get("description") or "?")
                continue
            q, p = D(it["quantity"]), D(it["unit_price"])
            pp = D(ln["unit_price"])
            var_pct = (p - pp) / pp * 100 if pp else Decimal("0")
            before = D(consumed_qty.get(ln["line_no"], 0))
            # Several invoice lines can bill the same PO line - accumulate within this invoice too.
            this_invoice_qty[ln["line_no"]] = this_invoice_qty.get(ln["line_no"], Decimal("0")) + q
            after = before + this_invoice_qty[ln["line_no"]]
            rec = D(received.get(ln["line_no"], 0))
            if p > pp:
                excess_total += (p - pp) * q
            row = {"po_line": ln["line_no"], "description": ln["description"], "invoice_description": it.get("description"),
                   "similarity": int(sim), "po_price": float(pp), "invoice_price": float(p),
                   "price_variance_pct": float(round(var_pct, 2)), "ordered": ln["qty"], "billed_before": float(before),
                   "this_invoice": float(q), "billed_after": float(after), "received": float(rec) if is_goods else None,
                   "uom": ln["uom"]}
            rows.append(row)
            allocations.append({"po_line": ln["line_no"], "qty": float(q), "amount": float(q2(q * p))})
            if abs(var_pct) > tol_pct:
                price_fail.append(row)
            if after > D(ln["qty"]):
                qty_fail.append(row)
            if is_goods and pol["goods_require_grn"] and after > rec:
                grn_fail.append(row)
        if unmatched:
            out.append(Check("recon.lines", st, "Every invoice line is on the PO", "fail",
                             f"Not on PO {po['po_number']}: {', '.join(unmatched)}.", "review", Q_BUYER,
                             "items billed that are not on the PO",
                             question="Were these items ordered outside the PO? Amend the PO or ask for a credit note."))
        else:
            out.append(Check("recon.lines", st, "Every invoice line is on the PO", "pass",
                             f"All {len(rows)} invoice lines matched to PO lines "
                             f"({', '.join(str(r['po_line']) for r in rows)})."))
        # Price check
        if price_fail:
            parts = [f"line {r['po_line']} '{r['description']}' billed at {rs(r['invoice_price'])} vs PO {rs(r['po_price'])} "
                     f"({r['price_variance_pct']:+.1f}%)" for r in price_fail]
            precedent = _precedent(ctx, "price")
            q_text = "Was a price revision agreed? If yes, amend the PO; if not, request a credit note for the difference."
            out.append(Check("recon.price", st, f"Unit prices within ±{tol_pct:g}% of PO", "fail",
                             "; ".join(parts) + f". Excess over PO price: {rs(q2(excess_total))} + GST.", "review", Q_BUYER,
                             f"price {price_fail[0]['price_variance_pct']:+.1f}% above PO",
                             evidence={"precedent": precedent} if precedent else None, question=q_text))
        else:
            within = [r for r in rows if r["price_variance_pct"] != 0]
            if within:
                out.append(Check("recon.price", st, f"Unit prices within ±{tol_pct:g}% of PO", "warn",
                                 "Small variance within tolerance: " + "; ".join(
                                     f"line {r['po_line']} {r['price_variance_pct']:+.2f}%" for r in within)))
            elif rows:
                out.append(Check("recon.price", st, f"Unit prices within ±{tol_pct:g}% of PO", "pass",
                                 "Every unit price matches the PO exactly."))
        # Quantity vs ordered (cumulative across invoices)
        if qty_fail:
            parts = [f"line {r['po_line']}: ordered {r['ordered']:,.0f}, already invoiced {r['billed_before']:,.0f}, this invoice "
                     f"{r['this_invoice']:,.0f} → {r['billed_after']:,.0f} ({r['billed_after'] / r['ordered'] * 100:.0f}% of PO)"
                     for r in qty_fail]
            out.append(Check("recon.qty", st, "Cumulative quantity within PO", "fail",
                             "Over-billing across invoices on this PO - " + "; ".join(parts) + ".", "review", Q_BUYER,
                             f"PO {po['po_number']} would be over-billed",
                             question="Was extra quantity ordered (PO amendment pending)? If not, reject this invoice."))
        elif rows:
            out.append(Check("recon.qty", st, "Cumulative quantity within PO", "pass", "; ".join(
                f"line {r['po_line']}: {r['billed_after']:,.0f} of {r['ordered']:,.0f} {r['uom']} invoiced after this"
                for r in rows) + "."))
        # Goods receipt (3-way match)
        if is_goods:
            grns = ref.grns_for_po(po["po_number"])
            if grn_fail:
                parts = [f"line {r['po_line']}: received {r['received']:,.0f}, invoiced {r['billed_after']:,.0f} cumulatively"
                         for r in grn_fail]
                out.append(Check("recon.grn", st, "Billed quantity received (3-way match)", "fail",
                                 "Billing for goods not received - " + "; ".join(parts) + ".", "review", Q_STORES,
                                 "goods billed but not received",
                                 question="Confirm with Stores whether the extra goods arrived without a GRN."))
            else:
                out.append(Check("recon.grn", st, "Billed quantity received (3-way match)", "pass",
                                 f"Goods receipts {', '.join(g['grn_number'] for g in grns) or '-'} cover the invoiced quantities."))
        else:
            out.append(Check("recon.grn", st, "Goods receipt", "skip", "Services PO - 2-way match (no goods receipt required)."))
    elif is_goods:
        # A goods invoice that is not itemised cannot be matched to receipts - never approve it on amount alone.
        allocations.append({"po_line": None, "qty": None, "amount": float(subtotal)})
        out.append(Check("recon.lines", st, "Goods invoice itemised (3-way match)", "fail",
                         f"PO {po['po_number']} is for goods, but the invoice has no quantities/rates per item, so billed "
                         f"quantities cannot be matched to goods received.", "review", Q_AP, "goods invoice is not itemised",
                         question="Get an itemised invoice (or the delivery challan) and match the quantities to the GRNs."))
    else:
        # Bundled services invoice: header-level match against whole periods of the service PO
        unit_sum = sum((D(ln["unit_price"]) for ln in po["lines"]), Decimal("0"))
        k = (subtotal / unit_sum) if unit_sum else Decimal("0")
        k_int = int(k.to_integral_value())
        if unit_sum and k_int >= 1 and abs(k - k_int) < Decimal("0.001"):
            for ln in po["lines"]:
                allocations.append({"po_line": ln["line_no"], "qty": k_int, "amount": float(q2(D(ln["unit_price"]) * k_int))})
                before = D(consumed_qty.get(ln["line_no"], 0))
                rows.append({"po_line": ln["line_no"], "description": ln["description"], "po_price": ln["unit_price"],
                             "invoice_price": ln["unit_price"], "price_variance_pct": 0.0, "ordered": ln["qty"],
                             "billed_before": float(before), "this_invoice": k_int, "billed_after": float(before + k_int),
                             "received": None, "uom": ln["uom"]})
                if before + k_int > D(ln["qty"]):
                    qty_fail.append(rows[-1])
            out.append(Check("recon.lines", st, "Bundled invoice matched to PO", "pass",
                             f"Invoice has a single bundled line of {rs(subtotal)} = exactly {k_int} period(s) of all "
                             f"{len(po['lines'])} PO lines ({' + '.join(rs(ln['unit_price']) for ln in po['lines'])})."))
            if qty_fail:
                out.append(Check("recon.qty", st, "Cumulative quantity within PO", "fail",
                                 f"This invoice would bill more periods than PO {po['po_number']} allows.", "review", Q_BUYER,
                                 "PO periods exhausted"))
            else:
                out.append(Check("recon.qty", st, "Cumulative quantity within PO", "pass",
                                 f"{int(rows[0]['billed_after'])} of {po['lines'][0]['qty']} {po['lines'][0]['uom'].lower()}s billed after this invoice."))
        else:
            allocations.append({"po_line": None, "qty": None, "amount": float(subtotal)})
            out.append(Check("recon.lines", st, "Line-level match", "warn",
                             "Invoice lines are not itemised against the PO - matched at header (amount) level only."))

    # Cumulative value against PO (catches split-invoice over-billing)
    po_value = D(po["value_ex_tax"])
    after_value = consumed_value + subtotal
    limit = po_value * (1 + D(pol["po_overbilling_tolerance_pct"]) / 100)
    util_before = consumed_value / po_value * 100 if po_value else Decimal("0")
    util_after = after_value / po_value * 100 if po_value else Decimal("0")
    prior_desc = ", ".join(f"{e['invoice_number']} {rs(e['amount_ex_tax'])}" for e in entries) or "none"
    ctx.po_position = {"po_value": float(po_value), "billed_before": float(consumed_value), "this_invoice": float(subtotal),
                       "billed_after": float(after_value), "util_before": float(round(util_before, 1)),
                       "util_after": float(round(util_after, 1)),
                       "prior_invoices": [{"run_id": e["run_id"], "invoice_number": e["invoice_number"],
                                           "amount_ex_tax": e["amount_ex_tax"], "invoice_date": e["invoice_date"]} for e in entries]}
    if after_value > limit:
        out.append(Check("recon.value", st, "Cumulative billing within PO value", "fail",
                         f"PO value {rs(po_value)}; already invoiced {rs(consumed_value)} ({prior_desc}); this invoice "
                         f"{rs(subtotal)} → {rs(after_value)} = {util_after:.0f}% of the PO (tolerance "
                         f"{pol['po_overbilling_tolerance_pct']:g}%).", "review", Q_BUYER,
                         f"PO {po['po_number']} billed to {util_after:.0f}%"))
    else:
        out.append(Check("recon.value", st, "Cumulative billing within PO value", "pass",
                         f"PO utilisation {util_before:.0f}% → {util_after:.0f}% after this invoice "
                         f"({rs(after_value)} of {rs(po_value)})."))

    if not price_fail and excess_total > D(pol["amount_tolerance_abs"]):
        out.append(Check("recon.abs", st, f"Total price excess ≤ {rs(pol['amount_tolerance_abs'])}", "fail",
                         f"Lines are each within ±{tol_pct:g}%, but together exceed PO prices by {rs(q2(excess_total))}.",
                         "review", Q_BUYER, f"price excess {rs(q2(excess_total))}"))
    ctx.allocations = allocations
    ctx.line_matches = rows
    return out


def _precedent(ctx: Ctx, kind: str) -> dict | None:
    """Most recent human approval of the same kind of exception for this vendor (learning from reviewers)."""
    import json

    v = ctx.vendor
    if not v:
        return None
    check_id = {"price": "recon.price"}.get(kind, kind)
    for r in ctx.db.review_precedents(v["vendor_id"], ctx.run_id):
        if r["action"] != "approve" or not r.get("result_json"):
            continue
        result = json.loads(r["result_json"])
        hit = next((c for c in result.get("checks", []) if c.get("id") == check_id and c.get("status") == "fail"), None)
        if hit:
            return {"run_id": r["run_id"], "invoice_number": r["invoice_number"], "reviewer": r["reviewer"],
                    "note": r["note"], "at": r["created_at"], "exception": hit.get("title")}
    return None
