from decimal import Decimal

import pytest

from app.utils import gstin as G
from app.utils.money import amount_in_words, amounts_in, inr, words_to_amount
from app.utils.textnorm import norm_invoice_number, norm_po_number, parse_date


def test_gstin_checksum_on_public_examples():
    assert G.is_valid("27AAPFU0939F1ZV")
    assert G.is_valid("24AAACC1206D1ZM")
    assert not G.is_valid("27AAPFU0939F1ZX")


def test_gstin_repairs_ocr_insertion_and_confusions():
    good = G.make_gstin("29", "BQRPA6634L")
    assert G.repair(good[:13] + "2" + good[13:]) == good             # spurious character inserted by OCR
    buyer = "29AABCN5432M1Z5"
    assert G.repair(buyer.replace("5432", "S432")) == buyer           # 'S' read in a digit slot


def test_gstin_ocr_repair_is_guarded_by_master_data():
    good = G.make_gstin("29", "BQRPA6634L")
    noisy = f"GSTIN: {good[:13]}2{good[13:]}"
    assert G.find_all(noisy, known={good}) == [(good, good[:13] + "2" + good[13:], "master")]
    # A deletion "repair" that happens to pass the checksum but is not a known GSTIN must not be trusted.
    assert G.repair("29BQRPA6634L12Z8") is not None
    assert G.find_all("GSTIN: 29BQRPA6634L12Z8", known={good}) == []


@pytest.mark.parametrize("value", ["497665", "604632", "151158.60", "42185", "12000000", "100"])
def test_amount_in_words_round_trip(value):
    assert words_to_amount(amount_in_words(Decimal(value))) == Decimal(value)


def test_indian_formatting_and_amount_parsing():
    assert inr(497665) == "4,97,665.00"
    assert amounts_in("CGST @ 9% 37,957.50") == [Decimal("37957.50")]
    assert amounts_in("Invoice Date: 03.11.2026") == []


@pytest.mark.parametrize("a,b", [("TS/26-27/0418", "TS-2627-418"), ("SGP/1189", "sgp 1189"), ("INV-0042", "INV42")])
def test_invoice_number_normalisation(a, b):
    assert norm_invoice_number(a) == norm_invoice_number(b)


def test_dates_are_day_first_and_multi_format():
    for s in ["18 Nov 2026", "18-11-2026", "18.11.2026", "November 18, 2026", "2026-11-18", "18-Nov-2026"]:
        assert str(parse_date(s)) == "2026-11-18"


def test_po_normalisation():
    assert norm_po_number("Your Order Ref: PO 4500001209") == "4500001209"
    assert norm_po_number("no po here") is None
