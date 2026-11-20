"""Stage 3a - deterministic field extraction ("rule-based reader").

Works on positioned lines/segments from the reader, so the same logic handles digital
PDFs and OCR output. Every extracted field carries evidence (the text it came from), the
method used, and a confidence score - nothing is a black box.
"""
from __future__ import annotations

import re
from decimal import Decimal

from app.pipeline.reader import DocText, Line, Segment
from app.utils import gstin as G
from app.utils.money import amounts_in, parse_amount, q2, words_to_amount
from app.utils.textnorm import PO_RE, find_dates, norm_po_number

LABELS: dict[str, list[str]] = {
    "invoice_number": [r"invoice\s*(?:no|number|num)\b\.?", r"invoice\s*#", r"inv\.?\s*(?:no|number)\b\.?",
                       r"inv\.?\s*#", r"bill\s*(?:no|number)\b\.?", r"bill\s*#", r"document\s*no\b\.?"],
    "invoice_date": [r"invoice\s*date", r"inv\.?\s*date", r"bill\s*date", r"date\s*of\s*(?:invoice|issue)",
                     r"^dated?\b"],
    "due_date": [r"due\s*date", r"payment\s*due(?:\s*date)?", r"pay\s*by"],
    "po_number": [r"p\.?\s*o\.?\s*(?:no|number|ref|reference)\b\.?", r"p\.?\s*o\.?\s*#", r"purchase\s*order(?:\s*(?:no|number))?\b\.?",
                  r"buyer'?s\s*order\s*(?:no|number)\b\.?", r"customer\s*p\.?\s*o\b\.?",
                  r"your\s*(?:order\s*ref(?:erence)?|p\.?\s*o)\b\.?", r"order\s*ref(?:erence)?\b\.?"],
}
_COMPILED = {k: [re.compile(p, re.I) for p in v] for k, v in LABELS.items()}
_ALL_LABELS = [rx for rxs in _COMPILED.values() for rx in rxs]
_GENERIC_LABEL = re.compile(
    r"^(dated?|delivery note|mode/terms of payment|destination|dispatched through|place of supply|"
    r"billing period|service period|state name|bill to|billed to|buyer|ship to|gstin|pan)\b", re.I)

TITLE_RX = re.compile(r"^(tax\s+)?invoice$|^freight\s+bill$|^bill\s+of\s+supply$|^receipt$|^proforma", re.I)
LEGAL_RX = re.compile(r"\b(pvt|private|ltd|limited|llp|industries|solutions|services|supplies|traders|carriers|"
                      r"electricals|enterprises|corporation|corp|inc|company)\b", re.I)

HEADER_TYPES = [
    ("sl", re.compile(r"^(#|s\.?\s*no\.?|sl\.?(\s*no\.?)?|sr\.?(\s*no\.?)?|no\.?)$", re.I)),
    ("desc", re.compile(r"^(description(\s+of\s+(goods|services))?|particulars|items?(\s+description)?|details|"
                        r"product|service|services)$", re.I)),
    ("hsn", re.compile(r"^(hsn(\s*/\s*sac)?|sac|hsn\s*code|sac\s*code)$", re.I)),
    ("qty", re.compile(r"^(qty|quantity|trips|units|hours|hrs)\.?$", re.I)),
    ("uom", re.compile(r"^(per|uom|unit)$", re.I)),
    ("rate", re.compile(r"^(rate|unit\s*price|price|rate\s*/\s*\w+|rate\s*per\s*\w+|unit\s*rate|unit\s*cost)$", re.I)),
    ("amount", re.compile(r"^(amount(\s*\((inr|rs\.?)\))?|total|value|line\s*total)$", re.I)),
]

STOP_RX = re.compile(r"\b(sub\s*-?\s*total|taxable\s*(value|amount)|cgst|sgst|igst|utgst|grand\s*total|"
                     r"amount\s*in\s*words|net\s*payable|freight\s*total|invoice\s*total|total\s*due|"
                     r"amount\s*due|amount\s*chargeable|rupees)\b|^total\b", re.I)

TOTAL_PATTERNS = [r"grand\s*total", r"total\s*due", r"amount\s*due", r"invoice\s*total", r"net\s*payable",
                  r"amount\s*payable", r"total\s*amount\b(?!\s*\(?in\s*words)", r"total\s*\((?:inr|rs)",
                  r"^total\b"]
TOTAL_EXCLUDE = re.compile(r"sub\s*-?\s*total|total\s*tax|taxable|total\s*(qty|quantity)|tax\s*amount", re.I)
SUBTOTAL_PATTERNS = [r"sub\s*-?\s*total", r"taxable\s*(?:value|amount)", r"freight\s*total", r"total\s*before\s*tax"]

IFSC_RX = re.compile(r"\b([A-Z]{4}[0O][A-Z0-9]{6})\b")
ACCOUNT_RX = re.compile(r"(?:a\s*/\s*c|account)\s*(?:no|number|#)?\.?\s*[:\-]?\s*([0-9][0-9 \-]{6,24}[0-9])", re.I)
EMAIL_RX = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _is_label(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    if any(rx.match(t) for rx in _ALL_LABELS) or _GENERIC_LABEL.match(t):
        return True
    return t.endswith(":") and len(t) < 30


def _seg_conf(seg_words, doc: DocText) -> float:
    if doc.method != "ocr":
        return 1.0
    confs = [w.conf for w in seg_words if w.conf is not None]
    return (sum(confs) / len(confs) / 100.0) if confs else 0.7


class Parser:
    def __init__(self, doc: DocText, buyer_gstin: str | None = None, buyer_name: str | None = None,
                 known_gstins: set[str] | None = None):
        self.doc = doc
        self.lines: list[Line] = doc.lines
        self.buyer_gstin = buyer_gstin
        self.buyer_name = (buyer_name or "").lower()
        self.known_gstins = set(known_gstins or ()) | ({buyer_gstin} if buyer_gstin else set())
        self.invalid_gstins: list[str] = []
        self.sources: dict[str, dict] = {}
        self.notes: list[str] = []

    # ------------------------------------------------------------------ helpers
    def _src(self, field, method, evidence, conf):
        self.sources[field] = {"method": method, "evidence": evidence[:160], "confidence": round(conf, 2)}

    def _labelled_values(self, field: str):
        """Yield (value_text, method, evidence, words) for each occurrence of a field label."""
        for li, line in enumerate(self.lines):
            for si, seg in enumerate(line.segments):
                text = seg.text
                for rx in _COMPILED[field]:
                    m = rx.search(text)
                    if not m or (m.start() > 0 and field != "po_number"):
                        continue
                    rest = text[m.end():].strip(" :#-–|").strip()
                    if rest:
                        yield rest, "label (same line)", text, seg.words
                        break
                    if si + 1 < len(line.segments) and not _is_label(line.segments[si + 1].text):
                        nxt = line.segments[si + 1]
                        yield nxt.text, "label (value to the right)", f"{text} → {nxt.text}", nxt.words
                        break
                    below = self._below(li, seg)
                    if below is not None:
                        yield below.text, "label (value below)", f"{text} ↓ {below.text}", below.words
                    break

    def _below(self, li: int, seg: Segment) -> Segment | None:
        base = self.lines[li]
        for lj in range(li + 1, min(li + 5, len(self.lines))):
            ln = self.lines[lj]
            if ln.top - base.bottom > 42:
                return None
            for s in ln.segments:
                if s.x1 >= seg.x0 - 2 and s.x0 <= seg.x1 + 30:
                    return None if _is_label(s.text) else s
        return None

    # ------------------------------------------------------------------ fields
    def invoice_number(self):
        for value, method, ev, words in self._labelled_values("invoice_number"):
            for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9/\-_.]*", value):
                tok = tok.rstrip(".-/")
                if 2 <= len(tok) <= 30 and re.search(r"\d", tok) and not find_dates(tok):
                    self._src("invoice_number", method, ev, 0.95 * _seg_conf(words, self.doc))
                    return tok
            return None  # label present but value blank/invalid -> treat as missing
        return None

    def date_field(self, field: str):
        for value, method, ev, words in self._labelled_values(field):
            ds = find_dates(value)
            if ds:
                self._src(field, method, ev, 0.95 * _seg_conf(words, self.doc))
                return ds[0][0]
        return None

    def po_number(self):
        for value, method, ev, words in self._labelled_values("po_number"):
            po = norm_po_number(value)
            if po:
                self._src("po_number", method, ev, 0.95 * _seg_conf(words, self.doc))
                return po, "labelled"
        m = PO_RE.search(self.doc.full_text.replace(" ", ""))
        if m:
            line = next((l.text for l in self.lines if m.group(1) in l.text.replace(" ", "")), m.group(1))
            self._src("po_number", "pattern (unlabelled reference)", line, 0.7)
            return m.group(1), "unlabelled"
        return None, None

    def gstins(self):
        ocr = self.doc.method == "ocr"
        found = G.find_all(self.doc.full_text, self.known_gstins if ocr else None, allow_repair=ocr)
        if not ocr:
            bad = [g for g in G.invalid_candidates(self.doc.full_text) if g != self.buyer_gstin]
            if bad:
                self.invalid_gstins = bad
        buyer = vendor = None
        for g, raw, how in found:
            if self.buyer_gstin and g == self.buyer_gstin:
                buyer = g
            elif vendor is None:
                vendor = g
                method = "pattern + checksum"
                if how == "checksum" and raw != g:
                    method = "pattern + checksum (OCR repaired)"
                    self.notes.append(f"Vendor GSTIN read as '{raw}' by OCR; repaired to {g} using the GSTIN checksum.")
                elif how == "master":
                    method = "pattern + checksum (OCR repaired)"
                    self.notes.append(f"Vendor GSTIN read as '{raw}' by OCR; one character from vendor-master GSTIN {g}, "
                                      f"which passes the checksum - using {g}.")
                line = next((l.text for l in self.lines if raw in l.text.upper().replace(" ", "")), raw)
                self._src("vendor_gstin", method, line, 0.97 if raw == g else 0.85)
        if buyer:
            self._src("buyer_gstin", "pattern + checksum", buyer, 0.97)
        elif len(found) >= 2:
            buyer = found[1][0] if found[0][0] == vendor else found[0][0]
        return vendor, buyer

    def vendor_name(self):
        sig = None
        for line in self.lines:
            for seg in line.segments:
                m = re.match(r"^for\s+(.{3,60}?)\s*$", seg.text, re.I)
                if m and not re.search(r"\b(business|queries|payment|your|any|the)\b", m.group(1), re.I):
                    cand = m.group(1).strip()
                    if self.buyer_name and self.buyer_name[:10] in cand.lower():
                        continue
                    sig = (cand, seg.text, seg.words)
        top = None
        candidates = []
        for line in self.lines:
            if line.page != 1 or line.top > 240:
                continue
            for seg in line.segments:
                t = seg.text.strip()
                if len(t) < 4 or TITLE_RX.match(t) or (self.buyer_name and self.buyer_name[:10] in t.lower()):
                    continue
                if re.search(r"\d{3,}|@|gstin|bill to|billed to", t, re.I):
                    continue
                size = max((w.size or w.height) for w in seg.words)
                entity = 1 if LEGAL_RX.search(t) else 0
                candidates.append((entity, size, -line.top, t, seg.words))
        if candidates:
            candidates.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)
            top = candidates[0][2:]
        if sig:
            self._src("vendor_name", "signature block ('For …')", sig[1], 0.9 * _seg_conf(sig[2], self.doc))
            return sig[0]
        if top:
            self._src("vendor_name", "largest heading on page 1", top[1], 0.75 * _seg_conf(top[2], self.doc))
            return top[1]
        return None

    # ------------------------------------------------------------------ table
    def _classify_header(self, line: Line):
        cols = []
        for seg in line.segments:
            t = seg.text.strip()
            typ = next((name for name, rx in HEADER_TYPES if rx.match(t)), None)
            if typ:
                cols.append({"type": typ, "x0": seg.x0, "x1": seg.x1, "text": t})
                continue
            # Fall back to word-level classification when header words share a segment.
            for w in seg.words:
                wt = w.text.strip()
                typ = next((name for name, rx in HEADER_TYPES if rx.match(wt)), None)
                if typ:
                    cols.append({"type": typ, "x0": w.x0, "x1": w.x1, "text": wt})
        types = {c["type"] for c in cols}
        if "desc" in types and "amount" in types and len(types) >= 3:
            return cols
        return None

    def line_items(self):
        header_idx, cols = None, None
        for i, line in enumerate(self.lines):
            cols = self._classify_header(line)
            if cols:
                header_idx = i
                break
        if header_idx is None:
            return [], None
        desc_col = next(c for c in cols if c["type"] == "desc")
        num_cols = [c for c in cols if c["type"] in ("hsn", "qty", "uom", "rate", "amount")]
        num_zone = min(c["x0"] for c in num_cols) - 4
        sl_col = next((c for c in cols if c["type"] == "sl"), None)
        items: list[dict] = []
        header_text = " | ".join(c["text"] for c in cols)
        for line in self.lines[header_idx + 1:]:
            if STOP_RX.search(line.text):
                break
            desc_words, cells = [], {}
            for w in line.words:
                cx = (w.x0 + w.x1) / 2
                if cx < num_zone:
                    if sl_col and w.x1 <= desc_col["x0"] + 1 and re.fullmatch(r"\d{1,3}\.?", w.text):
                        continue
                    desc_words.append(w)
                else:
                    best = max(num_cols, key=lambda c: (min(c["x1"], w.x1) - max(c["x0"], w.x0),
                                                        -abs((c["x0"] + c["x1"]) / 2 - cx)))
                    cells.setdefault(best["type"], []).append(w)
            if desc_words and not sl_col and re.fullmatch(r"\d{1,3}\.?", desc_words[0].text) and len(desc_words) > 1:
                desc_words = desc_words[1:]
            desc = " ".join(w.text for w in desc_words).strip()

            def num(col):
                ws = cells.get(col)
                if not ws:
                    return None
                vals = amounts_in(" ".join(w.text for w in ws))
                return vals[0] if vals else None

            qty, rate, amount = num("qty"), num("rate"), num("amount")
            hsn = " ".join(w.text for w in cells.get("hsn", [])) or None
            if amount is None and rate is None and qty is None:
                if desc and items and not cells:
                    items[-1]["description"] += " " + desc
                continue
            conf = _seg_conf(line.words, self.doc)
            items.append({"description": desc, "hsn": hsn,
                          "quantity": float(qty) if qty is not None else None,
                          "unit_price": float(rate) if rate is not None else None,
                          "amount": float(amount) if amount is not None else None,
                          "confidence": round(conf, 2)})
        return items, header_text

    # ------------------------------------------------------------------ totals
    def _amount_after_label(self, patterns, exclude=None):
        for rx_s in patterns:
            rx = re.compile(rx_s, re.I)
            for li, line in enumerate(self.lines):
                if exclude is not None and exclude.search(line.text):
                    continue
                for si, seg in enumerate(line.segments):
                    m = rx.search(seg.text)
                    if not m:
                        continue
                    tail = seg.text[m.end():] + " " + " ".join(s.text for s in line.segments[si + 1:])
                    vals = amounts_in(tail)
                    if vals:
                        words = seg.words + [w for s in line.segments[si + 1:] for w in s.words]
                        return vals[-1], line.text, _seg_conf(words, self.doc)
                    below = self._below(li, seg)
                    if below is not None:
                        vals = amounts_in(below.text)
                        if vals:
                            return vals[-1], f"{seg.text} ↓ {below.text}", _seg_conf(below.words, self.doc)
        return None, None, None

    def totals(self):
        subtotal, ev, conf = self._amount_after_label(SUBTOTAL_PATTERNS)
        if subtotal is not None:
            self._src("subtotal", "label", ev, 0.95 * conf)
        total, ev, conf = self._amount_after_label(TOTAL_PATTERNS, exclude=TOTAL_EXCLUDE)
        if total is not None:
            self._src("total", "label", ev, 0.95 * conf)
        taxes = []
        seen = set()
        for line in self.lines:
            for si, seg in enumerate(line.segments):
                m = re.search(r"\b(cgst|sgst|utgst|igst)\b", seg.text, re.I)
                if not m:
                    continue
                typ = m.group(1).upper()
                if typ in seen:
                    continue
                tail = seg.text[m.end():] + " " + " ".join(s.text for s in line.segments[si + 1:])
                rate_m = re.search(r"(\d{1,2}(?:\.\d+)?)\s*%", tail)
                vals = amounts_in(tail)
                if not vals:
                    continue
                seen.add(typ)
                taxes.append({"type": typ, "rate": float(rate_m.group(1)) if rate_m else None,
                              "amount": float(vals[-1])})
                break
        if taxes:
            self._src("taxes", "label", "; ".join(f"{t['type']} {t['rate']}% {t['amount']}" for t in taxes), 0.93)
        return subtotal, taxes, total

    def amount_in_words(self):
        for i, line in enumerate(self.lines):
            t = line.text
            m = re.search(r"in\s+words\s*\)?\s*[:\-]?\s*(.*)$", t, re.I)
            cand = None
            if m:
                cand = m.group(1).strip()
                if not cand and i + 1 < len(self.lines):
                    cand = self.lines[i + 1].text
            elif re.match(r"^\s*(rupees|inr)\b", t, re.I):
                cand = t
            if cand:
                cand = re.split(r"\s{2,}|E\.\s*&\s*O\.E", cand)[0]
                val = words_to_amount(cand)
                if val and val > 0:
                    self._src("amount_in_words", "words → number", cand, 0.9)
                    return cand.strip(), val
        return None, None

    def bank(self):
        text = self.doc.full_text
        out = {"account_number": None, "ifsc": None, "bank_name": None, "account_name": None}
        m = IFSC_RX.search(text.upper())
        if m:
            code = m.group(1)
            out["ifsc"] = code[:4] + "0" + code[5:]
        m = ACCOUNT_RX.search(text)
        if m:
            out["account_number"] = re.sub(r"\D", "", m.group(1))
        segs = [s.text for ln in self.lines for s in ln.segments]
        for s in segs:
            m = re.search(r"bank\s*name\s*[:\-]\s*([A-Za-z .&]+?)\s*(?:,|$)", s, re.I) or \
                re.search(r"^bank\s*[:\-]\s*([A-Za-z .&]+?)\s*(?:,|\||$)", s, re.I)
            if m:
                out["bank_name"] = m.group(1).strip()
                break
        if not out["bank_name"]:
            for ln in self.lines:
                m = re.search(r"\b([A-Z][A-Za-z.&]*(?:[ \t]+[A-Z][A-Za-z.&]*){0,3}[ \t]+Bank(?:[ \t]+of[ \t]+[A-Z][a-z]+)?)\b",
                              ln.text)
                if m:
                    out["bank_name"] = m.group(1).strip()
                    break
        for s in segs:
            m = re.search(r"account\s*name\s*[:\-]\s*(.+)$", s, re.I)
            if m:
                out["account_name"] = m.group(1).strip()
                break
        if out["account_number"] or out["ifsc"]:
            ev = next((l.text for l in self.lines if out["account_number"] and out["account_number"][-4:] in l.text), "")
            self._src("bank", "label + IFSC pattern", ev or (out["ifsc"] or ""), 0.93)
        return out

    # ------------------------------------------------------------------ main
    def parse(self) -> dict:
        vendor_gstin, buyer_gstin = self.gstins()
        po, po_ref_type = self.po_number()
        items, header = self.line_items()
        subtotal, taxes, total = self.totals()
        words_text, words_val = self.amount_in_words()
        inv_date = self.date_field("invoice_date")
        due = self.date_field("due_date")
        if inv_date is None:
            ds = find_dates("\n".join(l.text for l in self.lines[:25]))
            if ds:
                inv_date = ds[0][0]
                self._src("invoice_date", "first date on page (no label)", str(inv_date), 0.55)
        tax_total = q2(sum(Decimal(str(t["amount"])) for t in taxes)) if taxes else None
        text = self.doc.full_text
        head_segments = [s.text.strip() for l in self.lines[:14] for s in l.segments]
        doc_type = "invoice"
        title_rx = re.compile(r"(pro[\s-]?forma(\s+invoice)?|quotation|estimate|delivery\s+challan|purchase\s+order|"
                              r"credit\s+note|debit\s+note)", re.I)
        for seg in head_segments:
            if len(seg.split()) <= 4 and title_rx.fullmatch(seg):
                doc_type = re.sub(r"[\s-]+", " ", seg.lower()).replace("pro forma", "proforma")
                break
        if re.search(r"\bUSD\b|US\$|\$\s?\d", text):
            currency = "USD"
        elif re.search(r"\bEUR\b|€", text):
            currency = "EUR"
        elif re.search(r"\b(inr|rs\.?|rupees)\b|₹", text, re.I):
            currency = "INR"
        else:
            currency = None
        result = {
            "document_type": doc_type,
            "vendor_name": self.vendor_name(),
            "vendor_gstin": vendor_gstin,
            "invalid_gstins": self.invalid_gstins,
            "buyer_gstin": buyer_gstin,
            "invoice_number": self.invoice_number(),
            "invoice_date": inv_date.isoformat() if inv_date else None,
            "due_date": due.isoformat() if due else None,
            "po_number": po,
            "po_reference_type": po_ref_type,
            "currency": currency,
            "line_items": items,
            "table_header": header,
            "subtotal": float(subtotal) if subtotal is not None else None,
            "taxes": taxes,
            "tax_total": float(tax_total) if tax_total is not None else None,
            "total": float(total) if total is not None else None,
            "amount_in_words": words_text,
            "amount_in_words_value": float(words_val) if words_val is not None else None,
            "amounts_include_tax": bool(re.search(r"inclusive\s+of\s+(all\s+)?(gst|tax)|incl\.?\s*(of\s*)?gst", text, re.I)),
            "bank": self.bank(),
            "emails": list(dict.fromkeys(EMAIL_RX.findall(text))),
        }
        if items:
            self._src("line_items", "table under header", header or "", round(min(i["confidence"] for i in items) * 0.95, 2))
        result["field_sources"] = self.sources
        result["notes"] = self.notes
        return result


def parse_invoice(doc: DocText, buyer_gstin: str | None = None, buyer_name: str | None = None,
                  known_gstins: set[str] | None = None) -> dict:
    return Parser(doc, buyer_gstin, buyer_name, known_gstins).parse()


def parse_amount_safe(v) -> Decimal | None:
    return parse_amount(v) if isinstance(v, str) else (q2(v) if v is not None else None)
