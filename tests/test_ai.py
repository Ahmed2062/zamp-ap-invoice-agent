"""AI reader request/response handling (mocked HTTP) and the maker-checker merge."""
import json

import httpx
import pytest

from app.pipeline import ai_reader
from app.pipeline.merge import merge
from app.utils import gstin as G

GOOD = G.make_gstin("29", "AACCT4178K")

RULES = {
    "vendor_name": "Techsupply Solutions Pvt. Ltd.", "vendor_gstin": GOOD, "buyer_gstin": "29AABCN5432M1Z5",
    "invoice_number": "TS/26-27/0457", "invoice_date": "2026-11-18", "due_date": None, "po_number": "4500001201",
    "subtotal": 421750.0, "tax_total": 75915.0, "total": 497665.0, "amount_in_words_value": 497665.0,
    "line_items": [{"description": "Laptop", "quantity": 5, "unit_price": 68500.0, "amount": 342500.0},
                   {"description": "Monitor", "quantity": 5, "unit_price": 14200.0, "amount": 71000.0},
                   {"description": "Combo", "quantity": 5, "unit_price": 1650.0, "amount": 8250.0}],
    "taxes": [{"type": "CGST", "rate": 9, "amount": 37957.5}, {"type": "SGST", "rate": 9, "amount": 37957.5}],
    "bank": {"account_number": "50200034567812", "ifsc": "HDFC0000523"}, "emails": [], "field_sources": {},
}


def ai_copy(**changes):
    d = json.loads(json.dumps(RULES))
    d.update(changes)
    return d


def test_agreement_raises_confidence():
    merged, rows, conflicts = merge(RULES, ai_copy(invoice_number="TS-26-27-0457"), "text-layer")
    row = next(r for r in rows if r["field"] == "invoice_number")
    assert row["status"] == "agree" and row["confidence"] >= 0.97 and not conflicts


def test_money_conflict_settled_by_amount_in_words():
    merged, rows, conflicts = merge(RULES, ai_copy(total=479665.0), "ocr")
    assert merged["total"] == 497665.0 and not conflicts


def test_gstin_conflict_settled_by_checksum():
    bad = GOOD[:-1] + ("A" if GOOD[-1] != "A" else "B")
    merged, rows, conflicts = merge(RULES, ai_copy(vendor_gstin=bad), "ocr")
    assert merged["vendor_gstin"] == GOOD and not conflicts


def test_unresolvable_identity_conflict_on_scan_goes_to_a_person():
    merged, rows, conflicts = merge(RULES, ai_copy(invoice_number="TS/26-27/0451"), "ocr")
    assert conflicts and conflicts[0]["field"] == "invoice_number"


def test_digital_pdf_conflict_prefers_exact_text():
    merged, rows, conflicts = merge(RULES, ai_copy(invoice_number="TS/26-27/0451"), "text-layer")
    assert merged["invoice_number"] == "TS/26-27/0457" and not conflicts


AI_JSON = {"vendor_name": "Techsupply Solutions Pvt. Ltd.", "vendor_gstin": GOOD, "invoice_number": "TS/26-27/0457",
           "invoice_date": "2026-11-18", "po_number": "PO 4500001201", "total": "4,97,665.00", "subtotal": 421750,
           "cgst": 37957.5, "sgst": 37957.5, "gst_rate_percent": 18, "line_items": [{"description": "Laptop",
           "quantity": 5, "unit_price": 68500, "amount": 342500}], "bank_account_number": "5020 0034 5678 12"}


def _patch_client(monkeypatch, handler):
    real = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)
    monkeypatch.setattr(ai_reader.httpx, "Client", factory)


def test_interactions_api_request_and_parsing(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-goog-api-key")
        body = json.loads(request.content)
        seen["types"] = [p["type"] for p in body["input"]]
        seen["schema"] = body["response_format"]["schema"]["type"]
        return httpx.Response(200, json={"status": "completed", "steps": [
            {"type": "model_output", "content": [{"type": "text", "text": json.dumps(AI_JSON)}]}]})

    _patch_client(monkeypatch, handler)
    res = ai_reader.ai_extract(b"%PDF-1.4 test", "test-key", "gemini-3.8-flash")
    assert seen["url"].endswith("/v1/interactions") and seen["key"] == "test-key"
    assert seen["types"] == ["text", "document"] and seen["schema"] == "object"
    f = res["fields"]
    assert f["total"] == 497665.0 and f["po_number"] == "4500001201" and f["bank"]["account_number"] == "50200034567812"
    assert {t["type"] for t in f["taxes"]} == {"CGST", "SGST"} and f["tax_total"] == 75915.0


def test_falls_back_to_generate_content(monkeypatch):
    def handler(request):
        if request.url.path.endswith("/interactions"):
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(AI_JSON)}]}}]})

    _patch_client(monkeypatch, handler)
    res = ai_reader.ai_extract(b"%PDF-1.4 test", "k", "gemini-3.8-flash")
    assert res["api"].startswith("generateContent") and res["fields"]["invoice_number"] == "TS/26-27/0457"


def test_bad_key_is_reported_not_retried(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(403, json={"error": "API key not valid"})

    _patch_client(monkeypatch, handler)
    with pytest.raises(ai_reader.AIError, match="rejected the API key"):
        ai_reader.ai_extract(b"%PDF-1.4", "bad", "m")
    assert len(calls) == 1
