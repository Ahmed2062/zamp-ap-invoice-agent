"""Robustness: hostile/odd inputs and concurrency must end in a safe, explained decision."""
import io
import json
import threading
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.config import settings
from app.db import Database
from app.pipeline import checks as C
from app.pipeline.runner import Runner
from app.reference import Reference
from app.utils import gstin as G

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = {s["id"]: s for s in json.loads((ROOT / "samples" / "manifest.json").read_text())["demo"]}


@pytest.fixture()
def runner(tmp_path):
    r = Runner(Database(tmp_path / "t.db"), Reference(settings.reference_dir), settings)
    r.seed_history()
    return r


def _pdf(lines: list[str]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for ln in lines:
        c.drawString(50, y, ln)
        y -= 18
    c.showPage()
    c.save()
    return buf.getvalue()


def _failed(run):
    return {c["id"]: c for c in run["result"]["checks"] if c["status"] == "fail"}


def test_corrupt_pdf_is_returned_not_stuck(runner):
    rid = runner.run_sync(b"%PDF-1.7\nthis is not really a pdf", "broken.pdf", "upload")
    run = runner.db.get_run(rid)
    assert run["status"] == "completed" and run["decision"] == "RETURNED"
    assert "intake.pdf" in _failed(run)


def test_proforma_is_not_payable(runner):
    vendor = Reference(settings.reference_dir).vendor("V-1001")
    pdf = _pdf(["Techsupply Solutions Pvt. Ltd.", "PROFORMA INVOICE", f"GSTIN: {vendor['gstin']}",
                "Bill To: Nimbus Retail Pvt. Ltd.  GSTIN: 29AABCN5432M1Z5", "Invoice No: PI-7781",
                "Invoice Date: 18 Nov 2026", "Sub Total 1,000.00", "CGST @ 9% 90.00", "SGST @ 9% 90.00",
                "Grand Total 1,180.00"])
    run = runner.db.get_run(runner.run_sync(pdf, "proforma.pdf", "upload"))
    assert run["decision"] == "RETURNED"
    assert _failed(run)["doc.type"]["severity"] == "return"


def test_header_only_invoice_never_auto_infers_po(runner):
    ref = Reference(settings.reference_dir)
    ctx = C.Ctx(run_id=0, ref=ref, db=runner.db, today=__import__("datetime").date(2026, 11, 20))
    ctx.fields = {"subtotal": 1.0, "line_items": []}
    ctx.vendor = ref.vendor("V-1004")
    out = C.match_po(ctx)
    assert out[0].status == "fail" and ctx.po is None


def test_concurrent_reviews_cannot_double_post(runner):
    s = SAMPLES["x1-price-variance"]
    rid = runner.run_sync((ROOT / "samples" / s["file"]).read_bytes(), "x1.pdf", "sample")
    assert runner.db.get_run(rid)["decision"] == "NEEDS_REVIEW"
    results = []

    def approve(name):
        try:
            runner.apply_review(rid, "approve", name, "ok")
            results.append("ok")
        except ValueError:
            results.append("conflict")

    threads = [threading.Thread(target=approve, args=(f"R{i}",)) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 1 and results.count("conflict") == 3
    assert len([e for e in runner.db.ledger_all() if e["run_id"] == rid]) == 1


def test_digital_gstin_typos_are_flagged_not_repaired():
    good = G.make_gstin("29", "AACCT4178K")
    typo = good[:-1] + ("A" if good[-1] != "A" else "B")
    assert G.find_all(f"GSTIN: {typo}", known={good}, allow_repair=False) == []
    assert G.invalid_candidates(f"GSTIN: {typo}") == [typo]
