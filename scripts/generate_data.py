"""Generate reference data (vendor master, POs, GRNs, policy) and sample invoice PDFs.

This is the single source of truth for the demo world:
  * buyer: Nimbus Retail Pvt. Ltd. (Bengaluru, Karnataka - GST state 29)
  * 7 vendors, 8 purchase orders, 7 goods receipts
  * 7 "history" invoices (seeded through the real pipeline at startup)
  * 8 "demo" invoices (happy path + 4 headline edge cases + 3 extra scenarios)

Every vendor uses a visually different invoice template with different labels,
date formats and table layouts, and one invoice is a simulated paper scan.

Run:  python scripts/generate_data.py
"""
from __future__ import annotations

import io
import json
import math
import random
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageChops, ImageFilter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.utils.gstin import make_gstin  # noqa: E402
from app.utils.money import amount_in_words, inr, q2  # noqa: E402

REF_DIR = ROOT / "data" / "reference"
SAMPLES_DIR = ROOT / "samples"
W, H = A4
D = Decimal

# --------------------------------------------------------------------------------------
# Reference data
# --------------------------------------------------------------------------------------
COMPANY = {
    "name": "Nimbus Retail Pvt. Ltd.",
    "short_name": "Nimbus Retail",
    "gstin": make_gstin("29", "AABCN5432M"),
    "state_code": "29",
    "state": "Karnataka",
    "address": ["4th Floor, Prestige Meridian II", "29 MG Road, Bengaluru, Karnataka 560001"],
    "ap_email": "ap@nimbusretail.in",
    "currency": "INR",
}

VENDORS = [
    {
        "vendor_id": "V-1001", "name": "Techsupply Solutions Pvt. Ltd.",
        "aliases": ["Techsupply Solutions", "TechSupply"],
        "gstin": make_gstin("29", "AACCT4178K"), "state_code": "29", "state": "Karnataka",
        "address": "No. 14, 3rd Cross, Koramangala 5th Block, Bengaluru 560095",
        "category": "IT Hardware", "status": "approved", "payment_terms_days": 30,
        "email": "accounts@techsupply.co.in", "phone": "+91 80 4123 7788",
        "bank": {"bank_name": "HDFC Bank", "branch": "Koramangala", "account_name": "Techsupply Solutions Pvt Ltd",
                 "account_number": "50200034567812", "ifsc": "HDFC0000523"},
    },
    {
        "vendor_id": "V-1002", "name": "Shree Ganesh Packaging Industries",
        "aliases": ["Shree Ganesh Packaging", "SG Packaging"],
        "gstin": make_gstin("27", "ABKFS7721Q"), "state_code": "27", "state": "Maharashtra",
        "address": "Plot B-42, MIDC Bhosari, Pune, Maharashtra 411026",
        "category": "Packaging", "status": "approved", "payment_terms_days": 45,
        "email": "billing@sgpackaging.in", "phone": "+91 20 2712 5566",
        "bank": {"bank_name": "State Bank of India", "branch": "Bhosari", "account_name": "Shree Ganesh Packaging Industries",
                 "account_number": "38291045672", "ifsc": "SBIN0011392"},
    },
    {
        "vendor_id": "V-1003", "name": "Annapurna Office Supplies",
        "aliases": ["Annapurna Stationers"],
        "gstin": make_gstin("29", "BQRPA6634L"), "state_code": "29", "state": "Karnataka",
        "address": "22, 9th Main Road, Jayanagar 4th Block, Bengaluru 560011",
        "category": "Office Supplies", "status": "approved", "payment_terms_days": 15,
        "email": "annapurna.supplies@gmail.com", "phone": "+91 98450 12345",
        "bank": {"bank_name": "Canara Bank", "branch": "Jayanagar", "account_name": "Annapurna Office Supplies",
                 "account_number": "0412101009876", "ifsc": "CNRB0000412"},
    },
    {
        "vendor_id": "V-1004", "name": "Vertex CloudWorks Pvt. Ltd.",
        "aliases": ["Vertex CloudWorks", "Vertex Cloud Works"],
        "gstin": make_gstin("36", "AAFCV2290H"), "state_code": "36", "state": "Telangana",
        "address": "Level 7, Cyber Pearl, HITEC City, Hyderabad, Telangana 500081",
        "category": "IT Services", "status": "approved", "payment_terms_days": 30,
        "email": "finance@vertexcloudworks.com", "phone": "+91 40 6612 4400",
        "bank": {"bank_name": "Kotak Mahindra Bank", "branch": "HITEC City", "account_name": "Vertex CloudWorks Pvt Ltd",
                 "account_number": "9412345678", "ifsc": "KKBK0007461"},
    },
    {
        "vendor_id": "V-1005", "name": "Metro Facility Services LLP",
        "aliases": ["Metro Facility Services", "Metro FMS"],
        "gstin": make_gstin("29", "AAQFM8812D"), "state_code": "29", "state": "Karnataka",
        "address": "88, 2nd Floor, 100 Feet Road, Indiranagar, Bengaluru 560038",
        "category": "Facility Management", "status": "approved", "payment_terms_days": 30,
        "email": "accounts@metrofms.in", "phone": "+91 80 4092 1100",
        "bank": {"bank_name": "ICICI Bank", "branch": "Indiranagar", "account_name": "Metro Facility Services LLP",
                 "account_number": "002405501234", "ifsc": "ICIC0000024"},
    },
    {
        "vendor_id": "V-1006", "name": "Swift Freight Carriers",
        "aliases": ["Swift Freight"],
        "gstin": make_gstin("33", "AAJFS5567R"), "state_code": "33", "state": "Tamil Nadu",
        "address": "45, GST Road, Guindy, Chennai, Tamil Nadu 600032",
        "category": "Logistics", "status": "approved", "payment_terms_days": 30,
        "email": "bills@swiftfreight.in", "phone": "+91 44 2250 7788",
        "bank": {"bank_name": "Indian Bank", "branch": "Guindy", "account_name": "Swift Freight Carriers",
                 "account_number": "6045123987", "ifsc": "IDIB000G012"},
    },
    {
        "vendor_id": "V-1007", "name": "Prakash Electricals",
        "aliases": ["Prakash Electrical Traders"],
        "gstin": make_gstin("29", "ACZPP4410N"), "state_code": "29", "state": "Karnataka",
        "address": "17, SP Road, Bengaluru 560002",
        "category": "Electricals", "status": "blocked", "payment_terms_days": 30,
        "block_reason": "GST registration cancelled w.e.f. 30-Sep-2026; vendor blocked by Procurement on 02-Oct-2026.",
        "email": "prakashelectricals@yahoo.co.in", "phone": "+91 80 2222 3344",
        "bank": {"bank_name": "Bank of Baroda", "branch": "SP Road", "account_name": "Prakash Electricals",
                 "account_number": "31250100004567", "ifsc": "BARB0SPROAD"},
    },
]
VENDOR_BY_ID = {v["vendor_id"]: v for v in VENDORS}


def _po(po_number, vendor_id, po_date, kind, cost_center, requester, buyer, lines, note=""):
    out_lines = []
    for i, (desc, hsn, qty, uom, price) in enumerate(lines, start=1):
        out_lines.append({"line_no": i, "description": desc, "hsn_sac": hsn, "qty": qty, "uom": uom,
                          "unit_price": price, "tax_rate": 18})
    value = sum(D(str(l["qty"])) * D(str(l["unit_price"])) for l in out_lines)
    return {"po_number": po_number, "vendor_id": vendor_id, "po_date": po_date, "type": kind,
            "status": "open", "currency": "INR", "cost_center": cost_center, "requester": requester,
            "buyer": buyer, "note": note, "lines": out_lines, "value_ex_tax": float(q2(value))}


PURCHASE_ORDERS = [
    _po("4500001150", "V-1004", "2026-10-01", "services", "IT-INFRA", "Kiran Rao (IT)", "Priya Menon",
        [("Managed Cloud Operations (monthly)", "998316", 6, "Month", 185000.00),
         ("24x7 NOC Monitoring Add-on (monthly)", "998316", 6, "Month", 25000.00)],
        note="Blanket PO Oct 2026 - Mar 2027, billed monthly."),
    _po("4500001163", "V-1007", "2026-09-15", "goods", "STORE-OPS", "Arjun Shetty (Stores)", "Priya Menon",
        [("LED Panel Light 2x2 ft 36W", "94054090", 40, "Nos", 1450.00),
         ("Copper Wire 2.5 sq mm (90 m coil)", "85444999", 10, "Coil", 2350.00)]),
    _po("4500001176", "V-1005", "2026-10-01", "services", "STORE-OPS", "Arjun Shetty (Stores)", "Priya Menon",
        [("Housekeeping services - Store BLR-01 Indiranagar (monthly)", "998533", 6, "Month", 48000.00),
         ("Housekeeping services - Store BLR-02 Whitefield (monthly)", "998533", 6, "Month", 52000.00),
         ("Housekeeping consumables (monthly)", "998533", 6, "Month", 9500.00)],
        note="Blanket PO Oct 2026 - Mar 2027, billed monthly."),
    _po("4500001187", "V-1002", "2026-10-20", "goods", "WAREHOUSE", "Meera Iyer (Warehouse)", "Rahul Verma",
        [("5-Ply Corrugated Box 18x12x12 in", "48191010", 20000, "Nos", 38.50),
         ("BOPP Packaging Tape 48mm x 65m Brown", "39191000", 2000, "Nos", 42.00)],
        note="Partial deliveries allowed; vendor may invoice per delivery."),
    _po("4500001188", "V-1001", "2026-10-24", "goods", "IT-INFRA", "Kiran Rao (IT)", "Rahul Verma",
        [("HP LaserJet Pro MFP 4104fdw Printer", "84433100", 4, "Nos", 32400.00),
         ("HP 151A Black Toner Cartridge", "84439959", 12, "Nos", 6150.00)]),
    _po("4500001196", "V-1003", "2026-11-02", "goods", "ADMIN", "Neha Gupta (Admin)", "Rahul Verma",
        [("Whiteboard Marker Assorted (Pack of 10)", "96082000", 30, "Pack", 180.00),
         ("Sticky Notes 3x3 in (Pack of 5)", "48201090", 40, "Pack", 110.00),
         ("Stapler HD-45", "84721000", 10, "Nos", 260.00)]),
    _po("4500001201", "V-1001", "2026-11-05", "goods", "IT-INFRA", "Kiran Rao (IT)", "Rahul Verma",
        [("Dell Latitude 5450 Laptop (Core Ultra 5, 16GB, 512GB SSD)", "84713010", 5, "Nos", 68500.00),
         ("Dell 24 Monitor P2425H", "85285200", 5, "Nos", 14200.00),
         ("Logitech MK295 Wireless Keyboard & Mouse Combo", "84716060", 5, "Nos", 1650.00)]),
    _po("4500001209", "V-1006", "2026-11-01", "services", "LOGISTICS", "Meera Iyer (Warehouse)", "Priya Menon",
        [("FTL Freight Bengaluru to Chennai (32 ft MXL)", "996511", 10, "Trip", 28000.00),
         ("Loading & Unloading Charges", "996719", 10, "Trip", 1500.00)]),
    _po("4500001215", "V-1003", "2026-11-10", "goods", "ADMIN", "Neha Gupta (Admin)", "Rahul Verma",
        [("A4 Copier Paper 75 GSM (Ream of 500)", "48025690", 100, "Ream", 265.00),
         ("Ball Pen Blue (Box of 50)", "96081019", 20, "Box", 225.00),
         ("Box File FC Size", "48203000", 50, "Nos", 95.00)]),
]
PO_BY_NUMBER = {p["po_number"]: p for p in PURCHASE_ORDERS}

GOODS_RECEIPTS = [
    {"grn_number": "GRN-26-0802", "po_number": "4500001163", "date": "2026-09-25", "lines": {"1": 40, "2": 10}},
    {"grn_number": "GRN-26-0844", "po_number": "4500001188", "date": "2026-10-30", "lines": {"1": 4, "2": 12}},
    {"grn_number": "GRN-26-0871", "po_number": "4500001187", "date": "2026-11-03", "lines": {"1": 12000, "2": 1200}},
    {"grn_number": "GRN-26-0898", "po_number": "4500001196", "date": "2026-11-12", "lines": {"1": 30, "2": 40, "3": 10}},
    {"grn_number": "GRN-26-0905", "po_number": "4500001187", "date": "2026-11-14", "lines": {"1": 8000, "2": 800}},
    {"grn_number": "GRN-26-0912", "po_number": "4500001201", "date": "2026-11-17", "lines": {"1": 5, "2": 5, "3": 5}},
    {"grn_number": "GRN-26-0921", "po_number": "4500001215", "date": "2026-11-18", "lines": {"1": 100, "2": 20, "3": 50}},
]

POLICY = {
    "version": "AOP v1.4",
    "name": "Nimbus Retail - Accounts Payable invoice policy",
    "currency": "INR",
    "price_tolerance_pct": 2.0,
    "amount_tolerance_abs": 5000.0,
    "po_overbilling_tolerance_pct": 2.0,
    "rounding_tolerance": 1.0,
    "auto_approve_limit": 1000000.0,
    "po_inference_min_score": 0.80,
    "po_inference_min_margin": 0.15,
    "fuzzy_duplicate_window_days": 7,
    "invoice_max_age_days": 180,
    "ocr_min_confidence": 70.0,
    "standard_gst_rates": [0, 5, 12, 18, 28],
    "goods_require_grn": True,
}

# --------------------------------------------------------------------------------------
# Invoice specs
# --------------------------------------------------------------------------------------
LINES_4500001188 = [("HP LaserJet Pro MFP 4104fdw Printer", "84433100", 4, "Nos", 32400),
                    ("HP 151A Black Toner Cartridge", "84439959", 12, "Nos", 6150)]

INVOICES = [
    # ---------------- history (seeded at startup, backdated) ----------------
    dict(id="H1", set="history", template="A", vendor_id="V-1001", file="techsupply_TS-26-27-0418.pdf",
         number="TS/26-27/0418", inv_date=date(2026, 11, 6), po="4500001188", lines=LINES_4500001188,
         received_at="2026-11-06T11:20:00+05:30", title="Techsupply - printers & toner"),
    dict(id="H2", set="history", template="B", vendor_id="V-1002", file="sgpackaging_SGP-1123.pdf",
         number="SGP/1123", inv_date=date(2026, 11, 4), po="4500001187", order_date=date(2026, 10, 20),
         lines=[("5-Ply Corrugated Box 18x12x12 in", "48191010", 12000, "Nos", 38.50),
                ("BOPP Packaging Tape 48mm x 65m Brown", "39191000", 1200, "Nos", 42.00)],
         delivery_note="DN-1123", received_at="2026-11-05T09:45:00+05:30", title="Shree Ganesh - delivery 1 of 2"),
    dict(id="H3", set="history", template="D", vendor_id="V-1004", file="vertex_VCW-26-27-0198.pdf",
         number="VCW/26-27/0198", inv_date=date(2026, 11, 1), po="4500001150", period="01 Oct 2026 - 31 Oct 2026",
         lines=[("Managed Cloud Operations - October 2026", "998316", 1, "Month", 185000),
                ("24x7 NOC Monitoring Add-on - October 2026", "998316", 1, "Month", 25000)],
         received_at="2026-11-02T10:05:00+05:30", title="Vertex CloudWorks - October services"),
    dict(id="H4", set="history", template="E", vendor_id="V-1005", file="metrofms_MFS-2026-1043.pdf",
         number="MFS/2026/1043", inv_date=date(2026, 11, 3), po="4500001176", period="October 2026",
         bundled=("Housekeeping services for October 2026 - Stores BLR-01 (Indiranagar) and BLR-02 "
                  "(Whitefield), including consumables, as per PO", "998533", 109500),
         received_at="2026-11-04T14:30:00+05:30", title="Metro FMS - October housekeeping (bundled)"),
    dict(id="H5", set="history", template="F", vendor_id="V-1006", file="swiftfreight_SFC-2627-0544.pdf",
         number="SFC/2627/0544", inv_date=date(2026, 11, 10), po="4500001209",
         lines=[("FTL Freight Bengaluru to Chennai (32 ft MXL)", "996511", 4, "Trip", 29680),
                ("Loading & Unloading Charges", "996719", 4, "Trip", 1500)],
         received_at="2026-11-11T12:10:00+05:30", title="Swift Freight - 4 trips (fuel surcharge)",
         review={"action": "approve", "reviewer": "Priya Menon (Buyer)",
                 "note": "Diesel surcharge of 6% agreed for November; PO amendment raised.",
                 "at": "2026-11-11T16:05:00+05:30"}),
    dict(id="H6", set="history", template="C", vendor_id="V-1003", file="annapurna_AOS-271.pdf",
         number="AOS/271", inv_date=date(2026, 11, 13), po="4500001196",
         lines=[("Whiteboard Marker Assorted (Pack of 10)", "96082000", 30, "Pack", 180),
                ("Sticky Notes 3x3 in (Pack of 5)", "48201090", 40, "Pack", 110),
                ("Stapler HD-45", "84721000", 10, "Nos", 260)],
         tax_base_override=14200, received_at="2026-11-14T10:00:00+05:30",
         title="Annapurna - GST computed on wrong base"),
    dict(id="H7", set="history", template="B", vendor_id="V-1002", file="sgpackaging_SGP-1167.pdf",
         number="SGP/1167", inv_date=date(2026, 11, 15), po="4500001187", order_date=date(2026, 10, 20),
         lines=[("5-Ply Corrugated Box 18x12x12 in", "48191010", 8000, "Nos", 38.50),
                ("BOPP Packaging Tape 48mm x 65m Brown", "39191000", 800, "Nos", 42.00)],
         delivery_note="DN-1167", received_at="2026-11-15T16:40:00+05:30", title="Shree Ganesh - delivery 2 of 2"),

    # ---------------- demo set ----------------
    dict(id="happy-path", set="demo", template="A", vendor_id="V-1001", file="01_happy_path_techsupply_TS-26-27-0457.pdf",
         number="TS/26-27/0457", inv_date=date(2026, 11, 18), po="4500001201",
         lines=[("Dell Latitude 5450 Laptop (Core Ultra 5, 16GB, 512GB SSD)", "84713010", 5, "Nos", 68500),
                ("Dell 24 Monitor P2425H", "85285200", 5, "Nos", 14200),
                ("Logitech MK295 Wireless Keyboard & Mouse Combo", "84716060", 5, "Nos", 1650)],
         group="Happy path", title="Clean digital invoice, explicit PO",
         description="Techsupply bills 5 laptops, monitors and keyboard combos against PO 4500001201. "
                     "Digital PDF, PO quoted, goods received in full. Should flow straight through."),
    dict(id="ec1-scanned-no-po", set="demo", template="C", vendor_id="V-1003", file="02_scanned_no_po_annapurna_AOS-287.pdf",
         number="AOS/287", inv_date=date(2026, 11, 19), po=None, scan=True,
         lines=[("A4 Copier Paper 75 GSM (Ream of 500)", "48025690", 100, "Ream", 265),
                ("Ball Pen Blue (Box of 50)", "96081019", 20, "Box", 225),
                ("Box File FC Size", "48203000", 50, "Nos", 95)],
         group="Edge case 1", title="Scanned paper invoice with no PO number",
         description="A small stationery vendor prints, stamps and scans the invoice. No text layer, and no PO "
                     "reference anywhere. The vendor has two open POs - the process must OCR the page and work "
                     "out which PO this bills, and say how sure it is."),
    dict(id="ec2-split-overbilling", set="demo", template="B", vendor_id="V-1002", file="03_split_po_overbilling_sgpackaging_SGP-1189.pdf",
         number="SGP/1189", inv_date=date(2026, 11, 19), po="4500001187", order_date=date(2026, 10, 20),
         lines=[("5-Ply Corrugated Box 18x12x12 in", "48191010", 3000, "Nos", 38.50),
                ("BOPP Packaging Tape 48mm x 65m Brown", "39191000", 300, "Nos", 42.00)],
         delivery_note="DN-1189", group="Edge case 2", title="Third invoice on a PO that is already fully billed",
         description="PO 4500001187 was split across two deliveries and both invoices were already approved. "
                     "This 'balance supply' invoice looks perfect on its own - correct prices, valid PO - but "
                     "cumulatively it bills 115% of the PO and more than was ever received."),
    dict(id="ec3-duplicate-disguised", set="demo", template="A2", vendor_id="V-1001", file="04_duplicate_reminder_techsupply_TS-2627-418.pdf",
         number="TS-2627-418", inv_date=date(2026, 11, 6), po="4500001188", lines=LINES_4500001188,
         group="Edge case 3", title="Duplicate in disguise (payment reminder)",
         description="Techsupply's new billing system re-sends an old invoice as a 'payment reminder'. Different "
                     "file, different layout, invoice number reformatted from TS/26-27/0418 to TS-2627-418. "
                     "A naive exact-match check would pay it twice."),
    dict(id="ec4-bank-change", set="demo", template="D", vendor_id="V-1004", file="05_bank_details_changed_vertex_VCW-26-27-0231.pdf",
         number="VCW/26-27/0231", inv_date=date(2026, 11, 18), po="4500001150", period="01 Nov 2026 - 30 Nov 2026",
         lines=[("Managed Cloud Operations - November 2026", "998316", 1, "Month", 185000),
                ("24x7 NOC Monitoring Add-on - November 2026", "998316", 1, "Month", 25000)],
         bank_override={"bank_name": "Yes Bank", "branch": "Banjara Hills", "account_name": "Vertex Cloudworks",
                        "account_number": "038861900004417", "ifsc": "YESB0000388"},
         contact_override="vertexcloudworks.billing@outlook.com",
         group="Edge case 4", title="Perfect invoice, new bank account",
         description="Everything matches - vendor, PO, prices, maths. But the invoice asks to pay a NEW bank "
                     "account, from a contact email that is not on the vendor master. Classic payment-diversion "
                     "(BEC) fraud pattern."),
    dict(id="x1-price-variance", set="demo", template="F", vendor_id="V-1006", file="06_price_variance_swiftfreight_SFC-2627-0571.pdf",
         number="SFC/2627/0571", inv_date=date(2026, 11, 17), po="4500001209",
         lines=[("FTL Freight Bengaluru to Chennai (32 ft MXL)", "996511", 3, "Trip", 30240),
                ("Loading & Unloading Charges", "996719", 3, "Trip", 1500)],
         group="More scenarios", title="Rate charged above PO price",
         description="Freight rate billed at Rs 30,240/trip against a PO rate of Rs 28,000 (+8%), beyond the 2% "
                     "tolerance. A similar +6% surcharge was approved by the buyer last week."),
    dict(id="x2-missing-invoice-number", set="demo", template="E", vendor_id="V-1005", file="07_missing_invoice_number_metrofms.pdf",
         number=None, inv_date=date(2026, 11, 19), po="4500001176", period="November 2026",
         bundled=("Housekeeping services for November 2026 - Stores BLR-01 (Indiranagar) and BLR-02 "
                  "(Whitefield), including consumables, as per PO", "998533", 109500),
         group="More scenarios", title="Invoice number missing",
         description="The vendor's template glitched and the invoice number field is blank. An invoice without a "
                     "number cannot be booked or claimed for GST input credit."),
    dict(id="x3-blocked-vendor", set="demo", template="G", vendor_id="V-1007", file="08_blocked_vendor_prakash_PE-0932.pdf",
         number="PE/0932", inv_date=date(2026, 11, 16), po="4500001163",
         lines=[("LED Panel Light 2x2 ft 36W", "94054090", 40, "Nos", 1450),
                ("Copper Wire 2.5 sq mm (90 m coil)", "85444999", 10, "Coil", 2350)],
         group="More scenarios", title="Invoice from a blocked vendor",
         description="Valid-looking invoice against a real PO, but the vendor's GST registration was cancelled "
                     "and Procurement blocked them in October."),
]


def compute(spec: dict) -> dict:
    """Derive amounts for an invoice spec."""
    vendor = VENDOR_BY_ID[spec["vendor_id"]]
    intra = vendor["state_code"] == COMPANY["state_code"]
    rows = []
    if spec.get("bundled"):
        desc, hsn, amt = spec["bundled"]
        rows.append({"desc": desc, "hsn": hsn, "qty": None, "uom": None, "rate": None, "amount": q2(amt)})
    else:
        for desc, hsn, qty, uom, rate in spec["lines"]:
            rows.append({"desc": desc, "hsn": hsn, "qty": qty, "uom": uom, "rate": q2(rate),
                         "amount": q2(D(str(qty)) * D(str(rate)))})
    subtotal = q2(sum(r["amount"] for r in rows))
    base = q2(spec.get("tax_base_override", subtotal))
    if intra:
        cg = q2(base * D("0.09"))
        taxes = [("CGST", 9, cg), ("SGST", 9, cg)]
    else:
        taxes = [("IGST", 18, q2(base * D("0.18")))]
    total = q2(subtotal + sum(t[2] for t in taxes))
    terms = vendor["payment_terms_days"]
    return {"vendor": vendor, "rows": rows, "subtotal": subtotal, "taxes": taxes, "total": total,
            "due": spec["inv_date"] + timedelta(days=terms), "terms": terms,
            "bank": spec.get("bank_override") or vendor["bank"]}


# --------------------------------------------------------------------------------------
# Drawing helpers
# --------------------------------------------------------------------------------------
class Page:
    def __init__(self, title: str):
        self.buf = io.BytesIO()
        self.c = canvas.Canvas(self.buf, pagesize=A4)
        self.c.setTitle(title)
        self.c.setAuthor("generated sample")

    def text(self, x, top, s, size=9, font="Helvetica", color=colors.black, align="left"):
        c = self.c
        c.setFont(font, size)
        c.setFillColor(color)
        y = H - top
        if align == "right":
            c.drawRightString(x, y, s)
        elif align == "center":
            c.drawCentredString(x, y, s)
        else:
            c.drawString(x, y, s)

    def hline(self, x1, x2, top, width=0.6, color=colors.black, dash=None):
        c = self.c
        c.setStrokeColor(color)
        c.setLineWidth(width)
        if dash:
            c.setDash(*dash)
        c.line(x1, H - top, x2, H - top)
        c.setDash()

    def vline(self, x, top1, top2, width=0.6, color=colors.black):
        self.c.setStrokeColor(color)
        self.c.setLineWidth(width)
        self.c.line(x, H - top1, x, H - top2)

    def rect(self, x, top, w, h, fill=None, stroke=colors.black, width=0.6):
        c = self.c
        c.setLineWidth(width)
        if fill is not None:
            c.setFillColor(fill)
        if stroke is not None:
            c.setStrokeColor(stroke)
        c.rect(x, H - top - h, w, h, fill=1 if fill is not None else 0, stroke=1 if stroke is not None else 0)

    def wrap(self, s, width, size, font="Helvetica"):
        words, lines, cur = s.split(), [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if self.c.stringWidth(trial, font, size) <= width:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    def signature(self, x, top, color=colors.HexColor("#1a2b6d")):
        c = self.c
        c.setStrokeColor(color)
        c.setLineWidth(1.1)
        p = c.beginPath()
        y = H - top
        p.moveTo(x, y)
        p.curveTo(x + 10, y + 18, x + 22, y - 12, x + 32, y + 6)
        p.curveTo(x + 40, y + 16, x + 48, y - 8, x + 62, y + 4)
        p.curveTo(x + 70, y + 10, x + 78, y - 2, x + 90, y + 2)
        c.drawPath(p, stroke=1, fill=0)

    def stamp(self, cx, ctop, r, lines, color=colors.HexColor("#3b3fb0")):
        c = self.c
        c.saveState()
        c.setStrokeColor(color)
        c.setFillColor(color)
        c.setLineWidth(1.4)
        c.circle(cx, H - ctop, r, stroke=1, fill=0)
        c.circle(cx, H - ctop, r - 4, stroke=1, fill=0)
        c.setFont("Helvetica-Bold", 6.5)
        for i, ln in enumerate(lines):
            c.drawCentredString(cx, H - ctop + 8 - i * 9, ln)
        c.restoreState()

    def bytes(self) -> bytes:
        self.c.showPage()
        self.c.save()
        return self.buf.getvalue()


def fmt_qty(q):
    if q is None:
        return ""
    return f"{q:,}" if isinstance(q, int) else str(q)


# --------------------------------------------------------------------------------------
# Templates (one per vendor style)
# --------------------------------------------------------------------------------------
def template_a(spec, calc, reminder=False):
    """Techsupply: modern layout. A2 (reminder=True) is their 'new billing system' re-send."""
    v, cp = calc["vendor"], COMPANY
    pg = Page(f"Invoice {spec['number']}")
    brand = colors.HexColor("#0b5cab") if not reminder else colors.HexColor("#5b2a86")
    pg.text(40, 48, v["name"], 17, "Helvetica-Bold", brand)
    pg.text(555, 48, "INVOICE" if reminder else "TAX INVOICE", 18, "Helvetica-Bold", brand, "right")
    pg.text(40, 64, v["address"], 8.5, color=colors.HexColor("#444444"))
    pg.text(40, 76, f"GSTIN: {v['gstin']}   |   {v['email']}   |   {v['phone']}", 8.5,
            color=colors.HexColor("#444444"))
    pg.hline(40, 555, 86, 1.5, brand)
    top = 96
    if reminder:
        pg.rect(40, 94, 515, 22, fill=colors.HexColor("#fde8e8"), stroke=colors.HexColor("#c0392b"))
        pg.text(48, 109, "PAYMENT REMINDER - 2ND NOTICE: This invoice is overdue. Please arrange payment at the earliest.",
                8.5, "Helvetica-Bold", colors.HexColor("#c0392b"))
        top = 126
    if reminder:
        meta = [("Invoice #:", spec["number"]), ("Date:", spec["inv_date"].strftime("%d/%m/%Y")),
                ("Payment Due:", calc["due"].strftime("%d/%m/%Y")), ("Customer PO:", spec["po"])]
    else:
        meta = [("Invoice No.", spec["number"]), ("Invoice Date", spec["inv_date"].strftime("%d %b %Y")),
                ("Due Date", calc["due"].strftime("%d %b %Y")), ("PO No.", spec["po"])]
    for i, (k, val) in enumerate(meta):
        pg.text(365, top + 14 + i * 14, k, 9, "Helvetica-Bold", colors.HexColor("#333333"))
        pg.text(555, top + 14 + i * 14, val, 9, align="right")
    pg.text(40, top + 14, "BILL TO", 8, "Helvetica-Bold", colors.HexColor("#777777"))
    pg.text(40, top + 28, cp["name"], 10, "Helvetica-Bold")
    pg.text(40, top + 41, cp["address"][0], 8.5)
    pg.text(40, top + 53, cp["address"][1], 8.5)
    pg.text(40, top + 65, f"GSTIN: {cp['gstin']}", 8.5)
    pg.text(40, top + 77, "Place of Supply: Karnataka (29)", 8.5)

    t = top + 100
    pg.rect(40, t, 515, 20, fill=colors.HexColor("#eaf2fb") if not reminder else colors.HexColor("#f1eaf7"), stroke=None)
    cols = [(46, "#", "left"), (66, "Description", "left"), (330, "HSN", "left"), (405, "Qty", "right"),
            (480, "Unit Price", "right"), (552, "Amount", "right")]
    for x, label, al in cols:
        pg.text(x, t + 13, label, 8.5, "Helvetica-Bold", align=al)
    y = t + 34
    for i, r in enumerate(calc["rows"], start=1):
        pg.text(46, y, str(i), 8.5)
        pg.text(66, y, r["desc"], 8.5)
        pg.text(330, y, r["hsn"], 8.5)
        pg.text(405, y, fmt_qty(r["qty"]), 8.5, align="right")
        pg.text(480, y, inr(r["rate"]), 8.5, align="right")
        pg.text(552, y, inr(r["amount"]), 8.5, align="right")
        y += 20
    pg.hline(40, 555, y - 8, 0.6, colors.HexColor("#999999"))
    y += 8
    labels = [("Sub Total" if not reminder else "Subtotal", calc["subtotal"])]
    for name, rate, amt in calc["taxes"]:
        labels.append((f"{name} @ {rate}%" if not reminder else f"{name} {rate}%", amt))
    for k, val in labels:
        pg.text(380, y, k, 9)
        pg.text(552, y, inr(val), 9, align="right")
        y += 15
    pg.hline(375, 555, y - 9, 0.8)
    y += 5
    pg.text(380, y, "Total (INR)" if not reminder else "Amount Due (INR)", 10.5, "Helvetica-Bold")
    pg.text(552, y, inr(calc["total"]), 10.5, "Helvetica-Bold", align="right")
    y += 26
    pg.text(40, y, f"Amount in words: {amount_in_words(calc['total'])}", 8.5, "Helvetica-Oblique")
    y += 30
    b = calc["bank"]
    pg.rect(40, y, 260, 74, fill=colors.HexColor("#f7f7f7"), stroke=colors.HexColor("#dddddd"))
    pg.text(48, y + 15, "Bank Details", 9, "Helvetica-Bold")
    pg.text(48, y + 29, f"Bank: {b['bank_name']}, {b['branch']} Branch", 8.5)
    pg.text(48, y + 42, f"Account Name: {b['account_name']}", 8.5)
    pg.text(48, y + 55, f"Account No.: {b['account_number']}", 8.5)
    pg.text(48, y + 68, f"IFSC: {b['ifsc']}", 8.5)
    pg.text(555, y + 15, f"For {v['name']}", 9, "Helvetica-Bold", align="right")
    pg.signature(470, y + 48)
    pg.text(555, y + 68, "Authorised Signatory", 8.5, align="right")
    pg.text(297, 800, f"Payment terms: Net {calc['terms']} days. This is a computer generated invoice.", 7.5,
            color=colors.HexColor("#777777"), align="center")
    return pg.bytes()


def template_b(spec, calc):
    """Shree Ganesh: Tally-style bordered invoice; labels above values; tax rows inside the item table."""
    v, cp = calc["vendor"], COMPANY
    pg = Page(f"Invoice {spec['number']}")
    pg.text(297, 40, "TAX INVOICE", 13, "Times-Bold", align="center")
    L, R, T = 36, 559, 50
    pg.rect(L, T, R - L, 330, stroke=colors.black)
    mid = 300
    pg.vline(mid, T, T + 180)
    # Vendor block
    pg.text(L + 6, T + 16, v["name"], 11, "Times-Bold")
    pg.text(L + 6, T + 29, "Plot B-42, MIDC Bhosari", 8.5, "Times-Roman")
    pg.text(L + 6, T + 40, "Pune, Maharashtra 411026", 8.5, "Times-Roman")
    pg.text(L + 6, T + 51, f"GSTIN/UIN: {v['gstin']}", 8.5, "Times-Roman")
    pg.text(L + 6, T + 62, "State Name : Maharashtra, Code : 27", 8.5, "Times-Roman")
    pg.text(L + 6, T + 73, f"E-Mail : {v['email']}", 8.5, "Times-Roman")
    pg.hline(L, mid, T + 84)
    # Buyer block
    pg.text(L + 6, T + 97, "Buyer (Bill to)", 8, "Times-Italic")
    pg.text(L + 6, T + 110, cp["name"], 10, "Times-Bold")
    pg.text(L + 6, T + 122, cp["address"][0], 8.5, "Times-Roman")
    pg.text(L + 6, T + 133, cp["address"][1], 8.5, "Times-Roman")
    pg.text(L + 6, T + 145, f"GSTIN/UIN : {cp['gstin']}", 8.5, "Times-Roman")
    pg.text(L + 6, T + 156, "State Name : Karnataka, Code : 29", 8.5, "Times-Roman")
    # Right meta grid: label on top, value below
    cells = [("Invoice No.", spec["number"], "Dated", spec["inv_date"].strftime("%-d-%b-%Y")),
             ("Delivery Note", spec.get("delivery_note", ""), "Mode/Terms of Payment", f"{calc['terms']} Days"),
             ("Buyer's Order No.", spec["po"], "Dated", spec["order_date"].strftime("%-d-%b-%Y")),
             ("Dispatched through", "Road - Own Vehicle", "Destination", "Bengaluru")]
    cm = (mid + R) / 2
    for i, (k1, v1, k2, v2) in enumerate(cells):
        top = T + i * 45
        if i:
            pg.hline(mid, R, top)
        pg.text(mid + 5, top + 13, k1, 7.5, "Times-Roman")
        pg.text(mid + 5, top + 27, v1, 9.5, "Times-Bold")
        pg.text(cm + 5, top + 13, k2, 7.5, "Times-Roman")
        pg.text(cm + 5, top + 27, v2, 9.5, "Times-Bold")
    pg.vline(cm, T, T + 180)
    pg.hline(L, R, T + 180)
    # Item table
    t = T + 180
    cols = [(L + 4, "Sl No.", "left"), (L + 36, "Description of Goods", "left"), (300, "HSN/SAC", "left"),
            (398, "Quantity", "right"), (450, "Rate", "right"), (462, "per", "left"), (R - 4, "Amount", "right")]
    for x, label, al in cols:
        pg.text(x, t + 13, label, 8.5, "Times-Bold", align=al)
    pg.hline(L, R, t + 19)
    y = t + 33
    for i, r in enumerate(calc["rows"], start=1):
        pg.text(L + 4, y, str(i), 9, "Times-Roman")
        pg.text(L + 36, y, r["desc"], 9, "Times-Bold")
        pg.text(300, y, r["hsn"], 9, "Times-Roman")
        pg.text(398, y, f"{fmt_qty(r['qty'])} {r['uom']}", 9, "Times-Bold", align="right")
        pg.text(450, y, inr(r["rate"]), 9, "Times-Roman", align="right")
        pg.text(462, y, r["uom"], 9, "Times-Roman")
        pg.text(R - 4, y, inr(r["amount"]), 9, "Times-Bold", align="right")
        y += 16
    pg.hline(450, R, y - 9, 0.5)
    y += 4
    for name, rate, amt in calc["taxes"]:
        pg.text(260, y, f"{name} @ {rate}%", 9, "Times-BoldItalic", align="right")
        pg.text(R - 4, y, inr(amt), 9, "Times-Bold", align="right")
        y += 15
    pg.hline(L, R, T + 300)
    tq = sum(r["qty"] for r in calc["rows"])
    pg.text(L + 36, T + 315, "Total", 9.5, "Times-Bold")
    pg.text(398, T + 315, f"{tq:,} Nos", 9.5, "Times-Bold", align="right")
    pg.text(R - 4, T + 315, f"INR {inr(calc['total'])}", 9.5, "Times-Bold", align="right")
    # Words + HSN summary
    y = T + 348
    pg.text(L, y, "Amount Chargeable (in words)", 8, "Times-Roman")
    pg.text(R, y, "E. & O.E", 8, "Times-Italic", align="right")
    pg.text(L, y + 13, amount_in_words(calc["total"], prefix="INR"), 9.5, "Times-Bold")
    y += 34
    pg.rect(L, y, R - L, 44)
    for x, label, al in [(L + 4, "HSN/SAC", "left"), (300, "Taxable Value", "right"), (390, "IGST Rate", "right"),
                         (470, "IGST Amount", "right"), (R - 4, "Total Tax Amount", "right")]:
        pg.text(x, y + 12, label, 8, "Times-Bold", align=al)
    pg.hline(L, R, y + 17, 0.4)
    tax_amt = calc["taxes"][0][2]
    pg.text(L + 4, y + 30, "As per item list", 8.5, "Times-Roman")
    pg.text(300, y + 30, inr(calc["subtotal"]), 8.5, "Times-Roman", align="right")
    pg.text(390, y + 30, "18%", 8.5, "Times-Roman", align="right")
    pg.text(470, y + 30, inr(tax_amt), 8.5, "Times-Roman", align="right")
    pg.text(R - 4, y + 30, inr(tax_amt), 8.5, "Times-Roman", align="right")
    y += 64
    b = calc["bank"]
    pg.text(300, y, "Company's Bank Details", 8.5, "Times-Bold")
    pg.text(300, y + 13, f"Bank Name : {b['bank_name']}", 8.5, "Times-Roman")
    pg.text(300, y + 25, f"A/c No. : {b['account_number']}", 8.5, "Times-Roman")
    pg.text(300, y + 37, f"Branch & IFS Code : {b['branch']} & {b['ifsc']}", 8.5, "Times-Roman")
    pg.text(L, y, "Declaration", 8.5, "Times-Bold")
    pg.text(L, y + 13, "We declare that this invoice shows the actual price of the", 8, "Times-Roman")
    pg.text(L, y + 24, "goods described and that all particulars are true and correct.", 8, "Times-Roman")
    pg.text(R, y + 66, f"for {v['name']}", 9, "Times-Bold", align="right")
    pg.signature(470, y + 92)
    pg.text(R, y + 110, "Authorised Signatory", 8.5, "Times-Roman", align="right")
    pg.text(297, 805, "This is a Computer Generated Invoice", 8, "Times-Roman", align="center")
    return pg.bytes()


def template_c(spec, calc):
    """Annapurna: typed (Courier) invoice from a small vendor; signed, stamped, often scanned."""
    v, cp = calc["vendor"], COMPANY
    pg = Page(f"Invoice {spec['number']}")
    f, fb = "Courier", "Courier-Bold"
    pg.text(297, 52, "ANNAPURNA OFFICE SUPPLIES", 16, fb, align="center")
    pg.text(297, 68, "22, 9th Main Road, Jayanagar 4th Block, Bengaluru - 560011", 9, f, align="center")
    pg.text(297, 80, f"Ph: 98450 12345   GSTIN: {v['gstin']}", 9, f, align="center")
    pg.text(297, 104, "TAX INVOICE", 13, fb, align="center")
    pg.hline(250, 344, 107, 0.8)
    pg.text(50, 132, f"Inv No: {spec['number']}", 10, fb)
    pg.text(545, 132, f"Date: {spec['inv_date'].strftime('%d/%m/%Y')}", 10, fb, align="right")
    pg.text(50, 154, f"To: {cp['name']}", 10, f)
    pg.text(50, 167, "    29 MG Road, Bengaluru - 560001", 10, f)
    pg.text(50, 180, f"    GSTIN: {cp['gstin']}", 10, f)
    if spec.get("po"):
        pg.text(50, 193, f"    Your PO: {spec['po']}", 10, f)
    t = 214
    pg.hline(50, 545, t, 0.8, dash=(3, 2))
    pg.text(54, t + 14, "S.No", 10, fb)
    pg.text(96, t + 14, "Item", 10, fb)
    pg.text(372, t + 14, "Qty", 10, fb, align="right")
    pg.text(450, t + 14, "Rate", 10, fb, align="right")
    pg.text(541, t + 14, "Amount", 10, fb, align="right")
    pg.hline(50, 545, t + 21, 0.8, dash=(3, 2))
    y = t + 38
    for i, r in enumerate(calc["rows"], start=1):
        pg.text(54, y, str(i), 10, f)
        pg.text(96, y, r["desc"], 9.5, f)
        pg.text(372, y, fmt_qty(r["qty"]), 10, f, align="right")
        pg.text(450, y, inr(r["rate"]), 10, f, align="right")
        pg.text(541, y, inr(r["amount"]), 10, f, align="right")
        y += 18
    pg.hline(50, 545, y - 6, 0.8, dash=(3, 2))
    y += 12
    rows = [("Taxable Value", calc["subtotal"])] + [(f"{n} @ {r}%", a) for n, r, a in calc["taxes"]]
    for k, val in rows:
        pg.text(330, y, k, 10, f)
        pg.text(541, y, inr(val), 10, f, align="right")
        y += 16
    pg.hline(330, 545, y - 8, 0.8)
    y += 6
    pg.text(330, y, "Grand Total", 11, fb)
    pg.text(541, y, inr(calc["total"]), 11, fb, align="right")
    y += 28
    pg.text(50, y, amount_in_words(calc["total"]), 9.5, f)
    y += 30
    b = calc["bank"]
    pg.text(50, y, f"Bank: {b['bank_name']}, {b['branch']}", 9.5, f)
    pg.text(50, y + 14, f"A/c No: {b['account_number']}   IFSC: {b['ifsc']}", 9.5, f)
    pg.text(50, y + 40, "Goods once sold will not be taken back.", 8.5, f)
    pg.text(50, y + 52, "Subject to Bengaluru jurisdiction.", 8.5, f)
    pg.text(545, y + 40, "For ANNAPURNA OFFICE SUPPLIES", 9, fb, align="right")
    pg.signature(450, y + 74)
    pg.text(545, y + 96, "Proprietor", 9, f, align="right")
    pg.stamp(330, y + 70, 30, ["ANNAPURNA", "OFFICE SUPPLIES", "BENGALURU"])
    return pg.bytes()


def template_d(spec, calc):
    """Vertex CloudWorks: SaaS-style services invoice."""
    v, cp = calc["vendor"], COMPANY
    pg = Page(f"Invoice {spec['number']}")
    ink = colors.HexColor("#111827")
    accent = colors.HexColor("#0f766e")
    pg.rect(0, 0, W, 8, fill=accent, stroke=None)
    pg.text(40, 52, "VERTEX", 20, "Helvetica-Bold", accent)
    pg.text(40 + pg.c.stringWidth("VERTEX", "Helvetica-Bold", 20) + 6, 52, "CloudWorks", 20, "Helvetica", ink)
    pg.text(555, 52, "INVOICE", 22, "Helvetica-Bold", ink, "right")
    pg.text(40, 84, v["name"], 9.5, "Helvetica-Bold", ink)
    pg.text(40, 97, "Level 7, Cyber Pearl, HITEC City", 8.5, color=colors.HexColor("#4b5563"))
    pg.text(40, 109, "Hyderabad, Telangana 500081", 8.5, color=colors.HexColor("#4b5563"))
    pg.text(40, 121, f"GSTIN {v['gstin']}", 8.5, color=colors.HexColor("#4b5563"))
    pg.text(40, 133, spec.get("contact_override") or v["email"], 8.5, color=colors.HexColor("#4b5563"))
    meta = [("Invoice Number", spec["number"]), ("Invoice Date", spec["inv_date"].strftime("%B %-d, %Y")),
            ("Due Date", calc["due"].strftime("%B %-d, %Y")), ("Customer PO", spec["po"]),
            ("Billing Period", spec["period"])]
    for i, (k, val) in enumerate(meta):
        pg.text(345, 84 + i * 13, k, 8.5, color=colors.HexColor("#6b7280"))
        pg.text(555, 84 + i * 13, val, 8.5, "Helvetica-Bold", ink, "right")
    pg.text(40, 165, "BILLED TO", 7.5, "Helvetica-Bold", colors.HexColor("#6b7280"))
    pg.text(40, 179, cp["name"], 10, "Helvetica-Bold", ink)
    pg.text(40, 191, cp["address"][1], 8.5)
    pg.text(40, 203, f"GSTIN {cp['gstin']}  |  Place of supply: Karnataka (29)", 8.5)
    t = 228
    pg.hline(40, 555, t, 1.2, ink)
    for x, label, al in [(40, "Description", "left"), (330, "SAC", "left"), (400, "Qty", "right"),
                         (475, "Rate", "right"), (555, "Amount (INR)", "right")]:
        pg.text(x, t + 14, label, 8.5, "Helvetica-Bold", ink, al)
    pg.hline(40, 555, t + 21, 0.5, colors.HexColor("#d1d5db"))
    y = t + 37
    for r in calc["rows"]:
        pg.text(40, y, r["desc"], 9, color=ink)
        pg.text(330, y, r["hsn"], 9)
        pg.text(400, y, fmt_qty(r["qty"]), 9, align="right")
        pg.text(475, y, inr(r["rate"]), 9, align="right")
        pg.text(555, y, inr(r["amount"]), 9, align="right")
        pg.hline(40, 555, y + 8, 0.4, colors.HexColor("#e5e7eb"))
        y += 22
    y += 10
    rows = [("Subtotal", calc["subtotal"])] + [(f"{n} ({r}%)", a) for n, r, a in calc["taxes"]]
    for k, val in rows:
        pg.text(400, y, k, 9, color=colors.HexColor("#374151"))
        pg.text(555, y, inr(val), 9, align="right")
        y += 15
    pg.rect(395, y - 4, 160, 22, fill=colors.HexColor("#ecfdf5"), stroke=None)
    pg.text(400, y + 11, "Total Due (INR)", 10, "Helvetica-Bold", accent)
    pg.text(552, y + 11, inr(calc["total"]), 10, "Helvetica-Bold", accent, "right")
    y += 40
    pg.text(40, y, f"Amount in words: {amount_in_words(calc['total'])}", 8.5, "Helvetica-Oblique")
    y += 28
    b = calc["bank"]
    changed = spec.get("bank_override") is not None
    box_h = 82 if changed else 56
    pg.rect(40, y, 515, box_h, fill=colors.HexColor("#f9fafb"), stroke=colors.HexColor("#e5e7eb"))
    title = "Payment Instructions - UPDATED" if changed else "Payment Instructions"
    pg.text(50, y + 16, title, 9.5, "Helvetica-Bold", colors.HexColor("#b91c1c") if changed else ink)
    yy = y + 30
    if changed:
        pg.text(50, yy, "Please note: our bank account has changed w.e.f. 15 Nov 2026. Kindly update your vendor records",
                8.5, color=colors.HexColor("#b91c1c"))
        pg.text(50, yy + 11, "and remit this and all future payments to the account below.", 8.5,
                color=colors.HexColor("#b91c1c"))
        yy += 26
    pg.text(50, yy, f"Account Name: {b['account_name']}", 8.5)
    pg.text(50, yy + 12, f"Bank: {b['bank_name']}, {b['branch']}", 8.5)
    pg.text(300, yy, f"Account No: {b['account_number']}", 8.5)
    pg.text(300, yy + 12, f"IFSC: {b['ifsc']}", 8.5)
    pg.text(297, 795, f"Thank you for your business. Billing queries: {spec.get('contact_override') or v['email']}",
            8, color=colors.HexColor("#6b7280"), align="center")
    return pg.bytes()


def template_e(spec, calc):
    """Metro Facility Services: services invoice with a single bundled line (no qty/rate)."""
    v, cp = calc["vendor"], COMPANY
    pg = Page(f"Invoice {spec['number'] or 'draft'}")
    green = colors.HexColor("#14532d")
    pg.rect(0, 0, W, 70, fill=green, stroke=None)
    pg.text(40, 34, "METRO FACILITY SERVICES LLP", 16, "Helvetica-Bold", colors.white)
    pg.text(40, 52, "Integrated Facility Management  |  Housekeeping  |  Pest Control", 8.5, color=colors.white)
    pg.text(555, 40, "TAX INVOICE", 14, "Helvetica-Bold", colors.white, "right")
    pg.text(40, 92, v["address"], 8.5)
    pg.text(40, 104, f"GSTIN: {v['gstin']}   Ph: {v['phone']}   {v['email']}", 8.5)
    meta = [("Invoice No.:", spec["number"] or ""), ("Invoice Date:", spec["inv_date"].strftime("%d.%m.%Y")),
            ("PO Ref:", spec["po"]), ("Service Period:", spec["period"])]
    for i, (k, val) in enumerate(meta):
        pg.text(360, 132 + i * 14, k, 9, "Helvetica-Bold")
        pg.text(440, 132 + i * 14, val, 9)
    pg.text(40, 132, "Bill To:", 9, "Helvetica-Bold")
    pg.text(40, 146, cp["name"], 9.5, "Helvetica-Bold")
    pg.text(40, 158, cp["address"][1], 8.5)
    pg.text(40, 170, f"GSTIN: {cp['gstin']}", 8.5)
    t = 205
    pg.rect(40, t, 515, 20, fill=colors.HexColor("#dcfce7"), stroke=green)
    for x, label, al in [(46, "Sl", "left"), (70, "Particulars", "left"), (410, "SAC", "left"),
                         (550, "Amount (Rs.)", "right")]:
        pg.text(x, t + 13, label, 9, "Helvetica-Bold", green, al)
    y = t + 36
    for i, r in enumerate(calc["rows"], start=1):
        pg.text(46, y, str(i), 9)
        lines = pg.wrap(r["desc"], 330, 9)
        for j, ln in enumerate(lines):
            pg.text(70, y + j * 12, ln, 9)
        pg.text(410, y, r["hsn"], 9)
        pg.text(550, y, inr(r["amount"]), 9, align="right")
        y += 12 * len(lines) + 10
    pg.hline(40, 555, y, 0.6, green)
    y += 18
    rows = [("Taxable Amount", calc["subtotal"])] + [(f"{n} @ {r}%", a) for n, r, a in calc["taxes"]]
    for k, val in rows:
        pg.text(380, y, k, 9)
        pg.text(550, y, inr(val), 9, align="right")
        y += 15
    pg.text(380, y + 4, "Invoice Total", 10.5, "Helvetica-Bold", green)
    pg.text(550, y + 4, inr(calc["total"]), 10.5, "Helvetica-Bold", green, "right")
    y += 30
    pg.text(40, y, f"Amount in words: {amount_in_words(calc['total'])}", 8.5, "Helvetica-Oblique")
    y += 30
    b = calc["bank"]
    pg.text(40, y, "Bank details for payment:", 9, "Helvetica-Bold")
    pg.text(40, y + 13, f"{b['bank_name']}, {b['branch']} Branch  |  A/c No: {b['account_number']}  |  IFSC: {b['ifsc']}", 8.5)
    pg.text(555, y + 50, f"For {v['name']}", 9, "Helvetica-Bold", align="right")
    pg.signature(470, y + 78)
    pg.text(555, y + 96, "Partner", 8.5, align="right")
    return pg.bytes()


def template_f(spec, calc):
    """Swift Freight: freight bill with Trips / Rate per trip columns."""
    v, cp = calc["vendor"], COMPANY
    pg = Page(f"Bill {spec['number']}")
    navy = colors.HexColor("#1e3a8a")
    pg.text(297, 46, "SWIFT FREIGHT CARRIERS", 17, "Helvetica-Bold", navy, "center")
    pg.text(297, 61, "Fleet Owners & Transport Contractors", 9, "Helvetica-Oblique", align="center")
    pg.text(297, 74, f"{v['address']}  |  {v['phone']}", 8.5, align="center")
    pg.text(297, 86, f"GSTIN: {v['gstin']}", 8.5, "Helvetica-Bold", align="center")
    pg.hline(40, 555, 95, 1.2, navy)
    pg.text(297, 114, "FREIGHT BILL", 12, "Helvetica-Bold", navy, "center")
    pg.text(40, 140, f"Bill No: {spec['number']}", 9.5, "Helvetica-Bold")
    pg.text(555, 140, f"Bill Date: {spec['inv_date'].strftime('%d-%m-%Y')}", 9.5, "Helvetica-Bold", align="right")
    pg.text(40, 156, f"Your Order Ref: PO {spec['po']}", 9.5)
    pg.text(40, 176, f"Billed to: {cp['name']}, {cp['address'][1]}", 9)
    pg.text(40, 189, f"GSTIN: {cp['gstin']}", 9)
    pg.text(40, 202, "Consignor: Nimbus Retail DC, Hoskote    Consignee: Nimbus Retail Store CHN-01, T. Nagar", 8.5)
    t = 222
    pg.rect(40, t, 515, 20, fill=colors.HexColor("#dbeafe"), stroke=navy)
    for x, label, al in [(46, "Sr", "left"), (70, "Particulars", "left"), (380, "Trips", "right"),
                         (465, "Rate/Trip", "right"), (550, "Amount", "right")]:
        pg.text(x, t + 13, label, 9, "Helvetica-Bold", navy, al)
    y = t + 36
    for i, r in enumerate(calc["rows"], start=1):
        pg.text(46, y, str(i), 9)
        pg.text(70, y, r["desc"], 9)
        pg.text(380, y, fmt_qty(r["qty"]), 9, align="right")
        pg.text(465, y, inr(r["rate"]), 9, align="right")
        pg.text(550, y, inr(r["amount"]), 9, align="right")
        y += 18
    pg.hline(40, 555, y - 5, 0.6, navy)
    y += 12
    rows = [("Freight Total", calc["subtotal"])] + [(f"{n} @ {r}%", a) for n, r, a in calc["taxes"]]
    for k, val in rows:
        pg.text(380, y, k, 9.5)
        pg.text(550, y, inr(val), 9.5, align="right")
        y += 15
    pg.text(380, y + 4, "Net Payable", 11, "Helvetica-Bold", navy)
    pg.text(550, y + 4, inr(calc["total"]), 11, "Helvetica-Bold", navy, "right")
    y += 30
    pg.text(40, y, f"Rupees in words: {amount_in_words(calc['total'], prefix='')}".replace(":  ", ": "), 8.5,
            "Helvetica-Oblique")
    y += 26
    b = calc["bank"]
    pg.text(40, y, f"Bank: {b['bank_name']}, {b['branch']}   A/c No: {b['account_number']}   IFSC: {b['ifsc']}", 8.5)
    pg.text(40, y + 26, "Subject to Chennai jurisdiction. Interest @18% p.a. on payments delayed beyond due date.", 7.5)
    pg.text(555, y + 50, f"For {v['name']}", 9, "Helvetica-Bold", align="right")
    pg.signature(470, y + 78)
    pg.text(555, y + 96, "Authorised Signatory", 8.5, align="right")
    return pg.bytes()


def template_g(spec, calc):
    """Prakash Electricals: plain trader invoice."""
    v, cp = calc["vendor"], COMPANY
    pg = Page(f"Invoice {spec['number']}")
    pg.text(297, 50, "PRAKASH ELECTRICALS", 18, "Times-Bold", align="center")
    pg.text(297, 65, "Dealers in Electrical Goods & Lighting", 9.5, "Times-Italic", align="center")
    pg.text(297, 78, f"{v['address']}   Ph: {v['phone']}", 9, "Times-Roman", align="center")
    pg.text(297, 91, f"GSTIN: {v['gstin']}", 9, "Times-Bold", align="center")
    pg.rect(40, 100, 515, 0.1)
    pg.text(297, 120, "TAX INVOICE", 12, "Times-Bold", align="center")
    pg.text(40, 146, f"Invoice No: {spec['number']}", 10, "Times-Bold")
    pg.text(40, 160, f"Date: {spec['inv_date'].strftime('%d-%m-%Y')}", 10, "Times-Roman")
    pg.text(40, 174, f"P.O. No: {spec['po']}", 10, "Times-Roman")
    pg.text(330, 146, "Bill To:", 10, "Times-Bold")
    pg.text(330, 160, cp["name"], 10, "Times-Roman")
    pg.text(330, 174, f"GSTIN: {cp['gstin']}", 10, "Times-Roman")
    t = 196
    pg.rect(40, t, 515, 18)
    for x, label, al in [(46, "Sl", "left"), (70, "Description", "left"), (330, "HSN", "left"),
                         (420, "Qty", "right"), (480, "Rate", "right"), (550, "Amount", "right")]:
        pg.text(x, t + 12, label, 9.5, "Times-Bold", align=al)
    y = t + 33
    for i, r in enumerate(calc["rows"], start=1):
        pg.text(46, y, str(i), 9.5, "Times-Roman")
        pg.text(70, y, r["desc"], 9.5, "Times-Roman")
        pg.text(330, y, r["hsn"], 9.5, "Times-Roman")
        pg.text(420, y, fmt_qty(r["qty"]), 9.5, "Times-Roman", align="right")
        pg.text(480, y, inr(r["rate"]), 9.5, "Times-Roman", align="right")
        pg.text(550, y, inr(r["amount"]), 9.5, "Times-Roman", align="right")
        y += 17
    pg.hline(40, 555, y - 6)
    y += 12
    rows = [("Sub Total", calc["subtotal"])] + [(f"{n} {r}%", a) for n, r, a in calc["taxes"]]
    for k, val in rows:
        pg.text(400, y, k, 10, "Times-Roman")
        pg.text(550, y, inr(val), 10, "Times-Roman", align="right")
        y += 15
    pg.text(400, y + 3, "Grand Total", 11, "Times-Bold")
    pg.text(550, y + 3, inr(calc["total"]), 11, "Times-Bold", align="right")
    y += 30
    pg.text(40, y, amount_in_words(calc["total"]), 9.5, "Times-Italic")
    b = calc["bank"]
    pg.text(40, y + 26, f"Bank: {b['bank_name']}, {b['branch']}   A/c No: {b['account_number']}   IFSC: {b['ifsc']}",
            9, "Times-Roman")
    pg.text(555, y + 60, "For PRAKASH ELECTRICALS", 9.5, "Times-Bold", align="right")
    pg.signature(470, y + 88)
    pg.text(555, y + 104, "Proprietor", 9, "Times-Roman", align="right")
    return pg.bytes()


TEMPLATES = {
    "A": template_a, "A2": lambda s, c: template_a(s, c, reminder=True), "B": template_b, "C": template_c,
    "D": template_d, "E": template_e, "F": template_f, "G": template_g,
}


def simulate_scan(pdf_bytes: bytes, seed: int = 7, dpi: int = 200) -> bytes:
    """Rasterise a PDF page and degrade it like an office scan (tilt, noise, blur, paper tint, JPEG)."""
    rnd = random.Random(seed)
    doc = pdfium.PdfDocument(pdf_bytes)
    img = doc[0].render(scale=dpi / 72).to_pil().convert("L")
    img = img.rotate(0.55, resample=Image.BICUBIC, expand=False, fillcolor=255)
    noise = Image.effect_noise(img.size, 22).point(lambda p: 128 + (p - 128) * 0.55)
    img = ImageChops.add(ImageChops.subtract(img, noise.point(lambda p: max(0, p - 128))),
                         noise.point(lambda p: max(0, 128 - p)), 1.0, 0)
    img = img.filter(ImageFilter.GaussianBlur(0.55))
    # Slight uneven illumination toward one edge, like a flatbed lid gap.
    w, h = img.size
    grad = Image.linear_gradient("L").rotate(90).resize((w, h)).point(lambda p: int(p * 0.06))
    img = ImageChops.subtract(img, grad)
    rgb = Image.merge("RGB", (img.point(lambda p: int(p * 0.985)), img.point(lambda p: int(p * 0.97)),
                              img.point(lambda p: int(p * 0.93))))
    jpeg = io.BytesIO()
    rgb.save(jpeg, "JPEG", quality=62)
    scanned = Image.open(io.BytesIO(jpeg.getvalue()))
    out = io.BytesIO()
    scanned.save(out, "PDF", resolution=dpi)
    _ = rnd  # deterministic output; kept for future jitter variations
    return out.getvalue()


def to_jsonable(o):
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    raise TypeError(type(o))


def main():
    REF_DIR.mkdir(parents=True, exist_ok=True)
    (SAMPLES_DIR / "demo").mkdir(parents=True, exist_ok=True)
    (SAMPLES_DIR / "history").mkdir(parents=True, exist_ok=True)

    (REF_DIR / "company.json").write_text(json.dumps(COMPANY, indent=2))
    (REF_DIR / "vendors.json").write_text(json.dumps(VENDORS, indent=2))
    (REF_DIR / "purchase_orders.json").write_text(json.dumps(PURCHASE_ORDERS, indent=2))
    (REF_DIR / "goods_receipts.json").write_text(json.dumps(GOODS_RECEIPTS, indent=2))
    (REF_DIR / "policy.json").write_text(json.dumps(POLICY, indent=2))

    manifest = {"demo": [], "history": []}
    for spec in INVOICES:
        calc = compute(spec)
        pdf = TEMPLATES[spec["template"]](spec, calc)
        if spec.get("scan"):
            pdf = simulate_scan(pdf)
        path = SAMPLES_DIR / spec["set"] / spec["file"]
        path.write_bytes(pdf)
        entry = {
            "id": spec["id"], "file": f"{spec['set']}/{spec['file']}", "title": spec.get("title"),
            "group": spec.get("group"), "description": spec.get("description"),
            "vendor": calc["vendor"]["name"], "invoice_number": spec.get("number"),
            "total": float(calc["total"]), "received_at": spec.get("received_at"),
            "review": spec.get("review"), "scanned": bool(spec.get("scan")),
        }
        manifest[spec["set"]].append(entry)
        print(f"  {spec['set']:7s} {spec['id']:28s} {inr(calc['total']):>14s}  {path.name}")
    (SAMPLES_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2, default=to_jsonable))
    print(f"Wrote reference data to {REF_DIR} and {len(INVOICES)} invoices to {SAMPLES_DIR}")


if __name__ == "__main__":
    main()
