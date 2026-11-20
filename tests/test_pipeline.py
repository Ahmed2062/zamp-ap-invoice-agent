"""End-to-end decisions for the happy path and every edge case (fresh database, history seeded)."""
import json
from pathlib import Path

import pytest

from app.config import settings
from app.db import Database
from app.pipeline.reader import ocr_available
from app.pipeline.runner import Runner
from app.reference import Reference

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = {s["id"]: s for s in json.loads((ROOT / "samples" / "manifest.json").read_text())["demo"]}


@pytest.fixture(scope="module")
def runner(tmp_path_factory):
    db = Database(tmp_path_factory.mktemp("db") / "test.db")
    r = Runner(db, Reference(settings.reference_dir), settings)
    assert r.seed_history() == 7
    return r


def run(runner, sample_id):
    s = SAMPLES[sample_id]
    rid = runner.run_sync((ROOT / "samples" / s["file"]).read_bytes(), Path(s["file"]).name, "sample", sample_id=sample_id)
    return runner.db.get_run(rid)


def failed(run_row):
    return {c["id"]: c for c in run_row["result"]["checks"] if c["status"] == "fail"}


def test_history_seeded_with_mixed_outcomes(runner):
    rows = runner.db.list_runs()
    finals = sorted(r["final_decision"] for r in rows)
    assert finals.count("APPROVED") == 6 and finals.count("RETURNED") == 1
    assert any(r["decided_by"] == "Priya Menon (Buyer)" for r in rows)


def test_happy_path_is_approved_and_posted(runner):
    r = run(runner, "happy-path")
    assert r["decision"] == "APPROVED", r["headline"]
    assert not failed(r)
    pay = r["result"]["decision"]["outputs"][0]
    assert pay["type"] == "payment" and pay["amount"] == 497665.0 and "master" in pay["bank_source"]
    assert runner.db.ledger_for_po("4500001201")


@pytest.mark.skipif(not ocr_available(), reason="Tesseract not installed")
def test_scanned_invoice_without_po_infers_po(runner):
    r = run(runner, "ec1-scanned-no-po")
    assert r["read_method"] == "ocr"
    assert r["decision"] == "APPROVED", r["headline"]
    assert r["po_number"] == "4500001215" and r["po_inferred"] == 1
    assert "after OCR repair" in json.dumps(r["result"]["checks"])


def test_split_po_overbilling_needs_buyer_review(runner):
    r = run(runner, "ec2-split-overbilling")
    assert r["decision"] == "NEEDS_REVIEW"
    assert {"recon.qty", "recon.grn", "recon.value"} <= set(failed(r))
    assert r["queue"].startswith("Buyer")
    assert r["result"]["po"]["position"]["util_after"] == pytest.approx(115.0)


def test_disguised_duplicate_is_rejected(runner):
    r = run(runner, "ec3-duplicate-disguised")
    assert r["decision"] == "REJECTED"
    assert "dup.number" in failed(r)
    assert r["result"]["stages"]["reconcile"]["status"] == "skip"


def test_bank_change_puts_payment_on_hold(runner):
    r = run(runner, "ec4-bank-change")
    assert r["decision"] == "NEEDS_REVIEW" and r["priority"] == "High"
    assert failed(r)["vendor.bank"]["severity"] == "hold"
    assert any(c["id"] == "vendor.contact" and c["status"] == "warn" for c in r["result"]["checks"])


def test_price_variance_cites_precedent(runner):
    r = run(runner, "x1-price-variance")
    assert r["decision"] == "NEEDS_REVIEW"
    price = failed(r)["recon.price"]
    assert price["evidence"]["precedent"]["reviewer"] == "Priya Menon (Buyer)"


def test_missing_invoice_number_is_returned_with_email(runner):
    r = run(runner, "x2-missing-invoice-number")
    assert r["decision"] == "RETURNED"
    email = r["result"]["decision"]["outputs"][0]
    assert email["type"] == "vendor_email" and email["to"] == "accounts@metrofms.in"


def test_blocked_vendor_is_rejected(runner):
    r = run(runner, "x3-blocked-vendor")
    assert r["decision"] == "REJECTED" and "vendor.status" in failed(r)


def test_reprocessing_same_file_is_rejected(runner):
    r = run(runner, "happy-path")
    assert r["decision"] == "REJECTED"
    assert {"dup.file", "dup.number"} & set(failed(r))


def test_human_approval_posts_to_ledger(runner):
    r = run(runner, "ec4-bank-change")  # a second copy -> duplicate of the in-flight one
    assert r["decision"] == "REJECTED"
    pending = [x for x in runner.db.list_runs(decision="NEEDS_REVIEW") if x["sample_id"] == "x1-price-variance"][0]
    before = len(runner.db.ledger_for_po("4500001209"))
    done = runner.apply_review(pending["id"], "approve", "Test Buyer", "Rate revision agreed")
    assert done["final_decision"] == "APPROVED" and done["decided_by"] == "Test Buyer"
    assert len(runner.db.ledger_for_po("4500001209")) == before + 1
    final = done["result"]["decision"]
    assert final["decision"] == "APPROVED" and final["outputs"][0]["type"] == "payment"
    assert done["result"]["posting"]["posted"] is True
    assert done["result"]["agent_decision"]["decision"] == "NEEDS_REVIEW"
    assert not any(o["type"] == "review_task" for o in final["outputs"])
    with pytest.raises(ValueError):
        runner.apply_review(pending["id"], "approve", "Test Buyer", "again")
