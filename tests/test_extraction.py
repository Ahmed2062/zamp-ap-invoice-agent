"""The rule-based reader must extract every sample correctly (digital and scanned)."""
import json
from pathlib import Path

import pytest

from app.pipeline.parser import parse_invoice
from app.pipeline.reader import ocr_available, read_document

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "samples" / "manifest.json").read_text())
COMPANY = json.loads((ROOT / "data" / "reference" / "company.json").read_text())
VENDORS = {v["name"]: v for v in json.loads((ROOT / "data" / "reference" / "vendors.json").read_text())}
ITEMS = MANIFEST["demo"] + MANIFEST["history"]


@pytest.mark.parametrize("item", ITEMS, ids=[i["id"] for i in ITEMS])
def test_extracts_core_fields(item):
    if item["scanned"] and not ocr_available():
        pytest.skip("Tesseract not installed")
    doc = read_document((ROOT / "samples" / item["file"]).read_bytes())
    assert doc.method == ("ocr" if item["scanned"] else "text-layer")
    f = parse_invoice(doc, COMPANY["gstin"], COMPANY["name"], known_gstins={v["gstin"] for v in VENDORS.values()})
    assert f["invoice_number"] == item["invoice_number"]
    assert f["total"] == pytest.approx(item["total"])
    assert f["vendor_gstin"] == VENDORS[item["vendor"]]["gstin"]
    assert f["buyer_gstin"] == COMPANY["gstin"]
    assert f["amount_in_words_value"] == pytest.approx(item["total"])
    assert f["line_items"], "line items should be found"
    assert f["subtotal"] + f["tax_total"] == pytest.approx(f["total"], abs=1)
