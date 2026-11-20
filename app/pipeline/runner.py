"""Orchestrates a run: 9 stages, each emitting live events, then a persisted audit trail."""
from __future__ import annotations

import hashlib
import json
import shutil
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.pipeline import checks as C
from app.pipeline.ai_reader import AIError, ai_extract
from app.pipeline.decision import LABELS, approval_gate, decide
from app.pipeline.merge import CRITICAL as MERGE_CRITICAL
from app.pipeline.merge import merge
from app.pipeline.parser import parse_invoice
from app.pipeline.reader import OCR_DPI, ReadError, page_count, read_document
from app.utils.money import inr
from app.utils.textnorm import norm_invoice_number

STAGES = [
    {"key": "intake", "name": "Receive invoice", "desc": "Check the file and fingerprint it"},
    {"key": "read", "name": "Read document", "desc": "Digital text layer, or OCR for scans"},
    {"key": "extract", "name": "Extract fields", "desc": "Rule-based reader (+ AI reader) with cross-check"},
    {"key": "validate", "name": "Validate invoice", "desc": "Arithmetic, GST and dates"},
    {"key": "vendor", "name": "Verify vendor", "desc": "Vendor master, status, bank details"},
    {"key": "duplicate", "name": "Check duplicates", "desc": "Fingerprint, normalised number, near-duplicates"},
    {"key": "po_match", "name": "Match purchase order", "desc": "Quoted PO, or inferred from the contents"},
    {"key": "reconcile", "name": "Reconcile PO & receipts", "desc": "2/3-way match including earlier invoices"},
    {"key": "decide", "name": "Decide & act", "desc": "Policy decision and outputs"},
]
SEVERITY_RANK = {"reject": 4, "hold": 3, "return": 2, "review": 1}
ICON = {"pass": "✓", "warn": "!", "fail": "✗", "skip": "–"}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


class Stage:
    def __init__(self, runner: "Runner", run_id: int, key: str, pace_ms: int):
        self.r, self.run_id, self.key, self.pace_ms = runner, run_id, key, pace_ms
        self.checks: list[C.Check] = []
        self.summary = ""
        self.data: dict = {}
        self.forced_status: str | None = None
        self.slept = 0.0

    def _pause(self, seconds: float) -> None:
        """Demo pacing only - excluded from the reported machine time."""
        time.sleep(seconds)
        self.slept += seconds

    def __enter__(self):
        self.t0 = time.perf_counter()
        self.r.db.update_run(self.run_id, current_stage=self.key)
        self.r.db.add_event(self.run_id, "stage_start", self.key)
        return self

    def log(self, message: str) -> None:
        self.r.db.add_event(self.run_id, "log", self.key, message=message)
        if self.pace_ms:
            self._pause(min(0.16, self.pace_ms / 5000))

    def add(self, checks: list[C.Check]) -> None:
        for c in checks:
            self.checks.append(c)
            self.r.db.add_event(self.run_id, "check", self.key, status=c.status, message=c.message, data=c.to_dict())
            if self.pace_ms:
                self._pause(min(0.12, self.pace_ms / 6000))

    @property
    def status(self) -> tuple[str, str | None]:
        if self.forced_status:
            return self.forced_status, None
        fails = [c for c in self.checks if c.status == "fail"]
        if fails:
            return "fail", max((c.severity for c in fails), key=lambda s: SEVERITY_RANK.get(s, 0))
        if any(c.status == "warn" for c in self.checks):
            return "warn", None
        if self.checks and all(c.status == "skip" for c in self.checks):
            return "skip", None
        return "pass", None

    def __exit__(self, exc_type, exc, tb):
        compute_ms = round((time.perf_counter() - self.t0 - self.slept) * 1000)
        if exc is not None:
            self.forced_status = "error"
            self.summary = f"Error: {exc}"
        if self.pace_ms:
            remaining = self.pace_ms / 1000 - (time.perf_counter() - self.t0)
            if remaining > 0:
                time.sleep(remaining)
        status, severity = self.status
        self.result = {"status": status, "severity": severity, "summary": self.summary, "compute_ms": compute_ms,
                       "data": self.data, "checks": [c.to_dict() for c in self.checks]}
        self.r.db.add_event(self.run_id, "stage_end", self.key, status=status, message=self.summary,
                            data={"severity": severity, "compute_ms": compute_ms, "data": self.data})
        return False


class BusyError(Exception):
    pass


class Runner:
    MAX_IN_FLIGHT = 6

    def __init__(self, db, ref, settings):
        self.db, self.ref, self.settings = db, ref, settings
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="run")
        self.post_lock = threading.Lock()
        self._admit = threading.Lock()
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ public
    def submit(self, pdf_bytes: bytes, filename: str, source: str, sample_id: str | None = None) -> int:
        with self._admit:
            if self.db.count_active() >= self.MAX_IN_FLIGHT:
                raise BusyError("The agent is busy processing other invoices - please try again in a few seconds")
            run_id = self._create(pdf_bytes, filename, source, sample_id)
        self.pool.submit(self._safe_execute, run_id, self.settings.stage_delay_ms, None)
        return run_id

    def run_sync(self, pdf_bytes: bytes, filename: str, source: str, *, sample_id=None, created_at: str | None = None,
                 today: date | None = None, seeded: bool = False, pace_ms: int = 0) -> int:
        run_id = self._create(pdf_bytes, filename, source, sample_id, seeded=seeded, created_at=created_at)
        self._safe_execute(run_id, pace_ms, today)
        return run_id

    # ------------------------------------------------------------------ internals
    def _create(self, pdf_bytes, filename, source, sample_id, seeded=False, created_at=None) -> int:
        sha = hashlib.sha256(pdf_bytes).hexdigest()
        run_id = self.db.create_run(source=source, filename=filename, sha256=sha, file_path="", file_size=len(pdf_bytes),
                                    sample_id=sample_id, seeded=seeded, created_at=created_at)
        try:
            path = self.settings.uploads_dir / f"{run_id}.pdf"
            path.write_bytes(pdf_bytes)
            self.db._exec("UPDATE runs SET file_path = ? WHERE id = ?", (str(path), run_id))
            self.db.add_event(run_id, "run_start", data={"stages": STAGES, "filename": filename, "source": source,
                                                          "ai_enabled": self.settings.ai_enabled})
        except Exception as exc:
            self._fail(run_id, exc)
            raise
        return run_id

    def _fail(self, run_id: int, exc: Exception) -> None:
        msg = str(exc)[:300] or exc.__class__.__name__
        result = {"stages": {}, "checks": [], "fields": {}, "ai": {}, "document": {}, "vendor": None, "po": None,
                  "decision": {"decision": "ERROR", "label": LABELS["ERROR"], "headline": f"Processing error · {msg}",
                               "explanation": "The process hit an unexpected error and stopped safely. Nothing was booked "
                                              "or paid. The invoice can be re-submitted.",
                               "reasons": [], "notes": [], "outputs": [], "next_actions": ["Re-submit or process manually"]},
                  "posting": {"allocations": [], "posted": False}, "review": None}
        self.db.update_run(run_id, status="error", decision="ERROR", final_decision="ERROR", error=msg,
                           headline=f"Processing error · {msg}", current_stage=None,
                           completed_at=_iso(datetime.now(timezone.utc)), result_json=json.dumps(result))
        self.db.add_event(run_id, "run_end", status="error", message=msg)

    def _safe_execute(self, run_id, pace_ms, today):
        try:
            run = self.db.get_run(run_id)
            pdf_bytes = Path(run["file_path"]).read_bytes()
            self._execute(run_id, pdf_bytes, pace_ms, today)
        except Exception as exc:  # never leave a run hanging
            traceback.print_exc()
            self._fail(run_id, exc)

    def _execute(self, run_id: int, pdf_bytes: bytes, pace_ms: int, today: date | None):
        db, ref, settings = self.db, self.ref, self.settings
        run = db.get_run(run_id)
        t_start = time.perf_counter()
        ctx = C.Ctx(run_id=run_id, ref=ref, db=db, today=today or date.today(), sha256=run["file_sha256"])
        stages: dict[str, dict] = {}
        all_checks: list[C.Check] = []
        doc = None
        ai_info = {"enabled": settings.ai_enabled, "model": settings.gemini_model if settings.ai_enabled else None,
                   "used": False, "error": None}

        def finish(stage: Stage):
            stages[stage.key] = stage.result
            all_checks.extend(stage.checks)

        # 1. Intake ------------------------------------------------------------
        openable = True
        with Stage(self, run_id, "intake", pace_ms) as s:
            size_kb = len(pdf_bytes) / 1024
            try:
                pages = page_count(pdf_bytes)
                if pages < 1:
                    raise ReadError("the PDF has no pages")
            except Exception as exc:
                openable, pages = False, 0
                s.log(f"Received '{run['filename']}' via {run['source']} ({size_kb:.1f} KB) - but it cannot be opened.")
                s.add([C.Check("intake.pdf", "intake", "Valid PDF", "fail",
                               f"The file is not a readable PDF ({str(exc)[:120]}).", "return", C.Q_AP,
                               "the file is not a readable PDF", vendor_fix="The PDF we received is damaged - please re-send it.")])
                s.summary = "Damaged / unreadable PDF"
            if openable:
                s.log(f"Received '{run['filename']}' via {run['source']} ({size_kb:.1f} KB, {pages} page{'s' if pages != 1 else ''}).")
                s.log(f"SHA-256 fingerprint {run['file_sha256'][:16]}… (used for exact-duplicate detection).")
                s.add([C.Check("intake.pdf", "intake", "Valid PDF", "pass", f"Valid PDF, {pages} page(s), {size_kb:.1f} KB.")])
                s.summary = f"PDF · {pages} page{'s' if pages != 1 else ''} · {size_kb:.1f} KB"
            s.data = {"pages": pages, "size_kb": round(size_kb, 1), "sha256": run["file_sha256"]}
        finish(s)

        # 2. Read --------------------------------------------------------------
        with Stage(self, run_id, "read", pace_ms) as s:
            if not openable:
                s.forced_status = "skip"
                s.summary = "Skipped - file cannot be opened"
            else:
                s.log("Looking for a machine-readable text layer…")
                try:
                    doc = read_document(pdf_bytes)
                except ReadError as exc:
                    doc = None
                    s.add([C.Check("read.text", "read", "Document readable", "fail", f"Could not read the PDF: {exc}", "return",
                                   C.Q_AP, "the PDF is unreadable", vendor_fix="Please re-send the invoice as a valid PDF.")])
            if doc is not None:
                if doc.method == "text-layer":
                    s.log(f"Digital PDF - read {doc.char_count:,} characters directly from the text layer.")
                    s.add([C.Check("read.text", "read", "Document readable", "pass",
                                   f"Digital PDF: {doc.char_count:,} characters, {len(doc.lines)} lines read exactly.")])
                    s.summary = f"Digital PDF · {doc.char_count:,} characters"
                elif doc.method == "ocr":
                    s.log(f"No text layer - this is a scanned image. Rendering at {OCR_DPI} DPI for OCR…")
                    s.log(f"Deskewed the page by {doc.deskew_angle:+.2f}° and ran {doc.ocr_engine}.")
                    conf = doc.ocr_confidence or 0
                    minc = ref.policy["ocr_min_confidence"]
                    if conf < minc:
                        s.add([C.Check("read.ocr", "read", "OCR confidence", "fail",
                                       f"OCR confidence {conf:.1f}% is below the {minc:.0f}% policy floor - figures may be misread.",
                                       "review", C.Q_AP, "the scan is hard to read",
                                       question="Verify the key figures against the image, or ask the vendor for a clearer copy.")])
                    else:
                        s.add([C.Check("read.ocr", "read", "OCR confidence", "pass" if conf >= 85 else "warn",
                                       f"Scanned image read by OCR: {doc.char_count:,} characters, mean word confidence {conf:.1f}%.")])
                    s.summary = f"Scanned image · OCR {conf:.1f}% confidence · deskewed {doc.deskew_angle:+.2f}°"
                else:
                    s.add([C.Check("read.text", "read", "Document readable", "fail",
                                   "No text layer and OCR could not read the page. " + " ".join(doc.warnings), "return", C.Q_AP,
                                   "the document is unreadable", vendor_fix="Please send a clear, legible copy of the invoice.")])
                    s.summary = "Unreadable"
                for w in doc.warnings:
                    s.log(w)
                s.data = {"method": doc.method, "pages": doc.pages, "chars": doc.char_count, "ocr_confidence": doc.ocr_confidence,
                          "deskew": doc.deskew_angle, "timings_ms": doc.timings_ms,
                          "text_preview": [ln.text for ln in doc.lines[:60]]}
        finish(s)
        ctx.read_method = doc.method if doc else "none"
        ctx.doc_pages = doc.pages if doc else 1
        readable = doc is not None and doc.method in ("text-layer", "ocr")

        # 3. Extract -----------------------------------------------------------
        with Stage(self, run_id, "extract", pace_ms) as s:
            if not readable:
                s.forced_status = "skip"
                s.summary = "Skipped - nothing to read"
                fields, rows = {}, []
            else:
                s.log("Rule-based reader: label-anchored fields, table columns, totals, GST, bank details…")
                rules = parse_invoice(doc, ref.company["gstin"], ref.company["name"],
                                      known_gstins={v["gstin"] for v in ref.vendors})
                ai_fields = None
                if settings.ai_enabled:
                    s.log(f"AI reader: sending the PDF to Gemini ({settings.gemini_model}) for an independent read…")
                    try:
                        res = ai_extract(pdf_bytes, settings.gemini_api_key, settings.gemini_model)
                        ai_fields = res["fields"]
                        ai_info.update(used=True, latency_ms=res["latency_ms"], api=res["api"])
                        s.log(f"AI reader returned in {res['latency_ms'] / 1000:.1f}s via {res['api']}.")
                    except AIError as exc:
                        ai_info["error"] = str(exc)[:300]
                        s.log(f"AI reader unavailable ({str(exc)[:140]}) - continuing with the rule-based reader only.")
                else:
                    s.log("AI reader not configured (no GEMINI_API_KEY) - rule-based reader only.")
                fields, rows, conflicts = merge(rules, ai_fields, doc.method)
                for n in rules.get("notes", []):
                    s.log(n)
                ctx.fields = fields
                items = fields.get("line_items") or []
                found = [r for r in rows if r.get("value") is not None]
                agree = [r for r in rows if r.get("status") == "agree"]
                s.log(f"Extracted {len(found)} header fields and {len(items)} line item(s)."
                      + (f" Readers agree on {len(agree)} of {len([r for r in rows if r['ai'] is not None or r['rules'] is not None])} fields." if ai_fields else ""))
                req = []
                for key, label, why in [("invoice_number", "Invoice number", "it cannot be booked, de-duplicated or claimed for GST credit"),
                                        ("invoice_date", "Invoice date", "GST credit and payment terms depend on it"),
                                        ("total", "Invoice total", "there is no amount to pay")]:
                    if fields.get(key) in (None, ""):
                        req.append(C.Check(f"extract.{key}", "extract", f"{label} present", "fail",
                                           f"{label} is missing on the invoice - {why}.", "return", C.Q_AP,
                                           f"{label.lower()} missing", vendor_fix=f"{label} is missing - please add it."))
                    else:
                        val = fields[key]
                        shown = f"₹{inr(val)}" if key == "total" else C.fmt_date(val) if key == "invoice_date" else val
                        req.append(C.Check(f"extract.{key}", "extract", f"{label} present", "pass", f"{label}: {shown}."))
                if not fields.get("vendor_gstin") and not fields.get("vendor_name"):
                    req.append(C.Check("extract.vendor", "extract", "Supplier identifiable", "fail",
                                       "Neither a supplier GSTIN nor a supplier name could be found.", "review", C.Q_AP,
                                       "supplier cannot be identified"))
                for cf in conflicts:
                    req.append(C.Check(f"extract.conflict.{cf['field']}", "extract", f"Readers agree on {cf['field']}", "fail",
                                       f"AI read '{cf['ai']}' but the rule-based reader read '{cf['rules']}' for {cf['field']}, "
                                       f"and no evidence on the invoice settles it.", "review", C.Q_AP,
                                       f"readers disagree on {cf['field']}",
                                       question=f"Check {cf['field']} against the document image."))
                ai_only = [r["field"] for r in rows if r.get("status") == "AI only" and r["field"] in MERGE_CRITICAL]
                if ai_only:
                    req.append(C.Check("extract.ai_only", "extract", "Critical fields corroborated", "warn",
                                       f"Only the AI reader found: {', '.join(ai_only)}. These values must still pass the "
                                       f"arithmetic, GST and PO checks that follow."))
                if not items:
                    req.append(C.Check("extract.lines", "extract", "Line items found", "warn",
                                       "No itemised lines found - matching will be at header level."))
                s.add(req)
                s.summary = (f"{fields.get('invoice_number') or 'No invoice no.'} · "
                             f"{('₹' + inr(fields['total'])) if fields.get('total') is not None else 'no total'} · "
                             f"{len(items)} line{'s' if len(items) != 1 else ''}" + (" · AI cross-checked" if ai_fields else ""))
                s.data = {"fields": {k: v for k, v in fields.items() if k not in ("field_sources",)},
                          "sources": fields.get("field_sources", {}), "comparison": rows}
        finish(s)

        # 4-8. Checks ----------------------------------------------------------
        def rejected_by():
            return next((c for c in all_checks if c.status == "fail" and c.severity == "reject"), None)

        def run_stage(key, fn, summary_fn, data_fn=None):
            with Stage(self, run_id, key, pace_ms) as st:
                hard = rejected_by()
                if not readable:
                    st.forced_status = "skip"
                    st.summary = "Skipped"
                elif hard and key in ("duplicate", "po_match", "reconcile"):
                    st.forced_status = "skip"
                    st.summary = f"Skipped - invoice already rejected ({hard.title})"
                    st.log("Stopping here: a rejected invoice is never matched against POs or posted.")
                else:
                    st.add(fn(ctx))
                    st.summary = summary_fn(st)
                    if data_fn:
                        st.data = data_fn()
            finish(st)
            return st

        def summarize_counts(st):
            fails = [c for c in st.checks if c.status == "fail"]
            if fails:
                return fails[0].title[0].upper() + fails[0].title[1:] + (f" (+{len(fails) - 1})" if len(fails) > 1 else "")
            n = len([c for c in st.checks if c.status in ("pass", "warn")])
            w = len([c for c in st.checks if c.status == "warn"])
            return f"{n} check{'s' if n != 1 else ''} passed" + (f" · {w} note{'s' if w != 1 else ''}" if w else "")

        run_stage("validate", C.validate_invoice, summarize_counts)
        run_stage("vendor", C.verify_vendor,
                  lambda st: (f"{ctx.vendor['vendor_id']} · {ctx.vendor['name']} · {ctx.vendor['status']}"
                              if ctx.vendor else "Vendor not in master") if not any(c.status == "fail" for c in st.checks)
                  else summarize_counts(st))
        run_stage("duplicate", C.check_duplicates,
                  lambda st: summarize_counts(st) if any(c.status == "fail" for c in st.checks) else "No duplicates found")
        run_stage("po_match", C.match_po,
                  lambda st: (f"PO {ctx.po['po_number']}" + (" (inferred)" if ctx.po_inferred else " (quoted)"))
                  if ctx.po else summarize_counts(st),
                  lambda: {"candidates": ctx.po_candidates[:4]} if ctx.po_candidates else {})
        run_stage("reconcile", C.reconcile,
                  lambda st: summarize_counts(st) if any(c.status == "fail" for c in st.checks) else (
                      f"{len(ctx.line_matches)} line(s) matched · PO {ctx.po_position.get('util_before', 0):.0f}% → "
                      f"{ctx.po_position.get('util_after', 0):.0f}%" if ctx.po else "No PO to reconcile"),
                  lambda: {"lines": ctx.line_matches, "position": ctx.po_position})

        # 9. Decide ------------------------------------------------------------
        with Stage(self, run_id, "decide", pace_ms) as s:
            gate = approval_gate(ctx)
            s.add([gate])
            all_checks.append(gate)
            read_summary = ("Read a digital PDF exactly." if ctx.read_method == "text-layer" else
                            f"Read a scanned image with OCR ({doc.ocr_confidence:.1f}% confidence)." if ctx.read_method == "ocr"
                            else "The document could not be read.")
            outcome = decide(ctx, all_checks, read_summary)
            posted = False
            if outcome["decision"] == "APPROVED":
                with self.post_lock:
                    # Final guard: other runs may have posted against the same vendor/PO while this one was in
                    # flight. Re-run every ledger-dependent check against the current ledger before posting.
                    fresh = C.check_duplicates(ctx) + (C.reconcile(ctx) if ctx.po else [])
                    hit = db.ledger_find((ctx.vendor or {}).get("vendor_id"),
                                         norm_invoice_number(ctx.fields.get("invoice_number")), run_id)
                    if hit:
                        fresh.append(C.Check("dup.number", "decide", "Invoice number not already booked", "fail",
                                             f"Invoice {hit['invoice_number']} was posted by run #{hit['run_id']} while this run "
                                             f"was in progress. Paying it again would be a duplicate payment.", "reject", C.Q_AP,
                                             f"duplicate of {hit['invoice_number']} (run #{hit['run_id']})",
                                             evidence={"run_id": hit["run_id"], "original": hit["invoice_number"]}))
                    new_fails = [c for c in fresh if c.status == "fail"]
                    if new_fails:
                        for c in new_fails:
                            c.message = "Re-checked just before posting: " + c.message
                        s.add(new_fails)
                        all_checks.extend(new_fails)
                        outcome = decide(ctx, all_checks, read_summary)
                    else:
                        self._post(ctx, run_id, "AP agent (auto)")
                        posted = True
            s.log(outcome["headline"])
            for a in outcome["next_actions"]:
                s.log(a)
            s.summary = outcome["label"]
            s.data = {"decision": outcome["decision"], "posted": posted}
        finish(s)

        duration_ms = round((time.perf_counter() - t_start) * 1000)
        f = ctx.fields
        created = datetime.fromisoformat(run["created_at"])
        result = {
            "stages": stages,
            "checks": [c.to_dict() for c in all_checks],
            "fields": {k: v for k, v in f.items() if k != "field_sources"},
            "ai": ai_info,
            "document": stages.get("read", {}).get("data", {}),
            "vendor": ({k: ctx.vendor[k] for k in ("vendor_id", "name", "gstin", "status", "email", "phone", "payment_terms_days")}
                       | {"bank": {"bank_name": ctx.vendor["bank"]["bank_name"], "ifsc": ctx.vendor["bank"]["ifsc"],
                                   "account": "…" + ctx.vendor["bank"]["account_number"][-4:]}}) if ctx.vendor else None,
            "po": ({k: ctx.po[k] for k in ("po_number", "po_date", "type", "requester", "buyer", "cost_center", "value_ex_tax")}
                   | {"inferred": ctx.po_inferred, "candidates": ctx.po_candidates[:4], "position": ctx.po_position,
                      "lines": ctx.line_matches}) if ctx.po else ({"candidates": ctx.po_candidates[:4]} if ctx.po_candidates else None),
            "decision": outcome,
            "posting": {"allocations": ctx.allocations, "posted": posted},
            "review": None,
        }
        db.update_run(
            run_id, status="completed", current_stage=None, decision=outcome["decision"], final_decision=outcome["decision"],
            decided_by="agent", headline=outcome["headline"], queue=outcome["queue"], priority=outcome["priority"],
            vendor_id=(ctx.vendor or {}).get("vendor_id"), vendor_name=(ctx.vendor or {}).get("name") or f.get("vendor_name"),
            vendor_gstin=f.get("vendor_gstin"), invoice_number=f.get("invoice_number"),
            invoice_number_norm=norm_invoice_number(f.get("invoice_number")), invoice_date=f.get("invoice_date"),
            po_number=(ctx.po or {}).get("po_number") or f.get("po_number"), po_inferred=1 if ctx.po_inferred else 0,
            subtotal=f.get("subtotal"), total=f.get("total"), currency="INR", read_method=ctx.read_method,
            ai_used=1 if ai_info["used"] else 0, duration_ms=duration_ms,
            completed_at=_iso(created + timedelta(milliseconds=duration_ms)), result_json=json.dumps(result, default=str))
        db.add_event(run_id, "run_end", status=outcome["decision"], message=outcome["headline"],
                     data={"decision": outcome["decision"], "duration_ms": duration_ms})

    def _post(self, ctx: C.Ctx | None, run_id: int, by: str, posted_at: str | None = None,
              allocations: list | None = None, fields: dict | None = None, po_number: str | None = None,
              vendor_id: str | None = None) -> None:
        f = fields if fields is not None else ctx.fields
        if ctx is not None:
            vendor_id = vendor_id or (ctx.vendor or {}).get("vendor_id")
            po_number = po_number or (ctx.po or {}).get("po_number")
            allocations = allocations if allocations is not None else ctx.allocations
        self.db.post_ledger(
            run_id=run_id, vendor_id=vendor_id, po_number=po_number, invoice_number=f.get("invoice_number"),
            invoice_number_norm=norm_invoice_number(f.get("invoice_number")), invoice_date=f.get("invoice_date"),
            amount_ex_tax=float(f.get("subtotal") or 0), total=float(f.get("total") or 0),
            allocations=allocations or [], posted_by=by, posted_at=posted_at)

    # ------------------------------------------------------------------ human-in-the-loop
    def apply_review(self, run_id: int, action: str, reviewer: str, note: str, at: str | None = None) -> dict:
        if action not in ("approve", "reject", "return"):
            raise ValueError("action must be approve, reject or return")
        reviewer = (reviewer or "").strip()[:80] or "AP reviewer"
        note = (note or "").strip()[:1000]
        final = {"approve": "APPROVED", "reject": "REJECTED", "return": "RETURNED"}[action]
        with self.post_lock:
            # Read state inside the lock so two concurrent reviews cannot both act on the same run.
            run = self.db.get_run(run_id)
            if not run:
                raise ValueError("Run not found")
            current = run["final_decision"] or run["decision"]
            if current != "NEEDS_REVIEW":
                raise ValueError(f"Run is '{current}', only runs that need review can be actioned")
            result = run["result"] or {}
            f = result.get("fields", {})
            vendor_id = (result.get("vendor") or {}).get("vendor_id")
            po_number = (result.get("po") or {}).get("po_number")
            if action == "approve":
                hit = self.db.ledger_find(vendor_id, norm_invoice_number(f.get("invoice_number")), run_id)
                if hit:
                    raise ValueError(f"Invoice {hit['invoice_number']} is already posted by run #{hit['run_id']} - "
                                     f"approving would pay it twice")
                self._post(None, run_id, reviewer, posted_at=at, allocations=result.get("posting", {}).get("allocations", []),
                           fields=f, po_number=po_number, vendor_id=vendor_id)
                result.setdefault("posting", {})["posted"] = True
            when = at or _iso(datetime.now(timezone.utc))
            self.db.add_review(run_id, action, reviewer, note, created_at=at)
            agent = result.get("decision") or {}
            result["agent_decision"] = agent
            result["decision"] = self._reviewed_outcome(agent, final, reviewer, note, f, vendor_id, po_number)
            result["review"] = {"action": action, "reviewer": reviewer, "note": note, "at": when,
                                "agent_decision": run["decision"], "final_decision": final}
            self.db.update_run(run_id, final_decision=final, decided_by=reviewer, headline=result["decision"]["headline"][:300],
                               queue=None, result_json=json.dumps(result, default=str))
            self.db.add_event(run_id, "review", "review", status=final,
                              message=f"{reviewer} → {LABELS[final]}" + (f": {note}" if note else ""),
                              data=result["review"], ts=at)
        return self.db.get_run(run_id)

    def _reviewed_outcome(self, agent: dict, final: str, reviewer: str, note: str, f: dict, vendor_id, po_number) -> dict:
        """Final outcome after a human decision; the agent's recommendation is kept separately."""
        vendor = self.ref.vendor(vendor_id) if vendor_id else None
        name = (vendor or {}).get("name") or f.get("vendor_name") or "the vendor"
        reason = f" - {note.rstrip('. ')}" if note else ""
        outcome = {"decision": final, "label": LABELS[final], "priority": None, "queue": None,
                   "reasons": agent.get("reasons", []), "notes": agent.get("notes", []), "outputs": [], "next_actions": []}
        total = f.get("total")
        if final == "APPROVED":
            outcome["headline"] = f"Approved for payment by {reviewer} · {C.rs(total or 0)} to {name}{reason}"
            outcome["explanation"] = (f"The agent routed this invoice for review ({agent.get('headline', '')}). "
                                      f"{reviewer} reviewed the evidence and approved it{reason}.")
            if vendor:
                mb = vendor["bank"]
                inv = C.parse_date(f.get("invoice_date"))
                due = C.parse_date(f.get("due_date")) or (inv + timedelta(days=vendor.get("payment_terms_days", 30)) if inv else None)
                outcome["outputs"].append({
                    "type": "payment", "title": "Payment instruction", "payee": vendor["name"], "vendor_id": vendor["vendor_id"],
                    "amount": total, "currency": "INR", "due_date": due.isoformat() if due else None,
                    "due_basis": "invoice terms", "bank": f"{mb['bank_name']} {C.mask(mb['account_number'])} · {mb['ifsc']}",
                    "bank_source": "vendor master (verified)", "invoice_number": f.get("invoice_number"),
                    "po_number": po_number, "cost_center": (self.ref.po(po_number) or {}).get("cost_center")})
                outcome["next_actions"] = [f"Posted to the AP ledger{f' against PO {po_number}' if po_number else ''} by {reviewer}",
                                           f"Payment scheduled for {C.fmt_date(due)} to the master bank account {C.mask(mb['account_number'])}"]
        elif final == "RETURNED":
            outcome["headline"] = f"Returned to vendor by {reviewer}{reason}"
            outcome["explanation"] = f"{reviewer} reviewed this invoice and returned it to {name}{reason}."
            co = self.ref.company
            body = (f"Dear {name} team,\n\nWe are unable to process invoice {f.get('invoice_number') or ''} for "
                    f"{C.rs(total or 0)} in its current form.\n\n  1. {note or 'Please contact us for details.'}\n\n"
                    f"Please send a corrected invoice to {co['ap_email']}.\n\nRegards,\nAccounts Payable\n{co['name']}")
            outcome["outputs"].append({"type": "vendor_email", "title": "Correction request to vendor",
                                       "to": (vendor or {}).get("email") or "(via Procurement)",
                                       "to_source": "vendor master" if vendor else "no verified contact",
                                       "subject": f"Invoice {f.get('invoice_number') or ''} - correction needed", "body": body})
            outcome["next_actions"] = ["Correction request drafted to the vendor's master contact", "Invoice not booked"]
        else:
            outcome["headline"] = f"Rejected by {reviewer}{reason}"
            outcome["explanation"] = f"{reviewer} reviewed this invoice and rejected it{reason}. Nothing was booked or paid."
            outcome["next_actions"] = ["Invoice not booked and will not be paid"]
        return outcome

    # ------------------------------------------------------------------ demo data
    def seed_history(self) -> int:
        manifest = json.loads((self.settings.samples_dir / "manifest.json").read_text())
        items = sorted(manifest["history"], key=lambda x: x["received_at"])
        for item in items:
            pdf = (self.settings.samples_dir / item["file"]).read_bytes()
            received = datetime.fromisoformat(item["received_at"])
            run_id = self.run_sync(pdf, Path(item["file"]).name, "email", sample_id=item["id"], created_at=_iso(received),
                                   today=received.date(), seeded=True)
            if item.get("review"):
                rv = item["review"]
                self.apply_review(run_id, rv["action"], rv["reviewer"], rv["note"],
                                  at=_iso(datetime.fromisoformat(rv["at"])))
        return len(items)

    def reset(self) -> int:
        self.db.reset()
        shutil.rmtree(self.settings.uploads_dir, ignore_errors=True)
        self.settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        return self.seed_history()
