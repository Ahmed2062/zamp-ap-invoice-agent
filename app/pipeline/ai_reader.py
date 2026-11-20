"""Stage 3b - optional AI reader (Google Gemini) used as an independent second reader.

The AI never makes the decision. It reads the document (works on scans too - the PDF is
sent as-is) and its output is cross-checked field by field against the rule-based reader
("maker-checker"). If no API key is configured the pipeline runs without it.
"""
from __future__ import annotations

import base64
import json
import os
import time

import httpx

from app.utils import gstin as G
from app.utils.textnorm import norm_po_number, parse_date

INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
API_REVISION = "2026-05-20"
LEGACY_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Free-tier models tried in order when the configured model is overloaded.
FALLBACK_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_FALLBACK_MODELS", "gemini-3.1-flash-lite,gemini-2.5-flash-lite,gemini-3.8-flash,gemini-4-hydrogen").split(",") if m.strip()]

NUM = {"type": ["number", "null"]}
STR = {"type": ["string", "null"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "vendor_name": {**STR, "description": "Legal name of the seller/supplier issuing the invoice."},
        "vendor_gstin": {**STR, "description": "Seller's 15-character GSTIN exactly as printed."},
        "buyer_gstin": {**STR, "description": "Buyer's (bill-to) GSTIN exactly as printed."},
        "invoice_number": {**STR, "description": "Invoice/bill number exactly as printed. null if blank or absent."},
        "invoice_date": {**STR, "description": "Invoice date as YYYY-MM-DD. Indian invoices use day-first dates."},
        "due_date": {**STR, "description": "Payment due date as YYYY-MM-DD if printed."},
        "po_number": {**STR, "description": "Buyer's purchase order number if referenced anywhere, else null."},
        "currency": STR,
        "line_items": {"type": "array", "items": {"type": "object", "properties": {
            "description": {"type": "string"}, "hsn": STR, "quantity": NUM, "unit_price": NUM, "amount": NUM},
            "required": ["description"]}},
        "subtotal": {**NUM, "description": "Taxable value before GST."},
        "cgst": NUM, "sgst": NUM, "igst": NUM,
        "gst_rate_percent": {**NUM, "description": "Total GST rate, e.g. 18."},
        "total": {**NUM, "description": "Final invoice total payable including tax."},
        "amount_in_words": STR,
        "bank_account_number": STR, "bank_ifsc": STR, "bank_name": STR, "account_name": STR,
        "contact_emails": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["vendor_name", "invoice_number", "invoice_date", "total", "line_items"],
}

PROMPT = (
    "You are an accounts-payable data extraction engine. Extract the fields defined by the JSON schema from this "
    "Indian GST invoice. Copy identifiers (invoice number, GSTINs, PO number, account number, IFSC) exactly as printed. "
    "Amounts are plain numbers without commas or currency symbols. Use null for anything not present - never guess "
    "or compute a value that is not printed on the document."
)


class AIError(Exception):
    pass


def _extract_text(payload: dict) -> str | None:
    for step in payload.get("steps", []) or []:
        if step.get("type") in ("model_output", None):
            for part in step.get("content", []) or []:
                if part.get("type") == "text" and part.get("text"):
                    return part["text"]
    for out in payload.get("outputs", []) or []:
        if out.get("type") == "text" and out.get("text"):
            return out["text"]
    for cand in payload.get("candidates", []) or []:
        for part in (cand.get("content") or {}).get("parts", []) or []:
            if part.get("text"):
                return part["text"]
    return None


def _call_interactions(client: httpx.Client, key: str, model: str, pdf_b64: str, timeout: float) -> str:
    # PDF ("document") input is only available on the v1beta Interactions API.
    body = {
        "model": model,
        "input": [{"type": "text", "text": PROMPT},
                  {"type": "document", "mime_type": "application/pdf", "data": pdf_b64}],
        "response_format": {"type": "text", "mime_type": "application/json", "schema": SCHEMA},
        "generation_config": {"thinking_level": "low", "max_output_tokens": 4096},
        "store": False,
    }
    r = client.post(INTERACTIONS_URL, headers={"x-goog-api-key": key, "Api-Revision": API_REVISION}, json=body,
                    timeout=timeout)
    if r.status_code >= 400:
        raise AIError(f"Interactions API HTTP {r.status_code}: {r.text[:220]}")
    text = _extract_text(r.json())
    if not text:
        raise AIError("Interactions API returned no text output")
    return text


def _call_legacy(client: httpx.Client, key: str, model: str, pdf_b64: str, timeout: float) -> str:
    body = {
        "contents": [{"role": "user", "parts": [{"text": PROMPT},
                                                {"inline_data": {"mime_type": "application/pdf", "data": pdf_b64}}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": SCHEMA, "temperature": 0},
    }
    r = client.post(LEGACY_URL.format(model=model), headers={"x-goog-api-key": key}, json=body, timeout=timeout)
    if r.status_code >= 400:
        raise AIError(f"generateContent HTTP {r.status_code}: {r.text[:220]}")
    text = _extract_text(r.json())
    if not text:
        raise AIError("generateContent returned no text output")
    return text


def _parse_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"):]
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end < 0:
        raise AIError("AI output was not JSON")
    return json.loads(t[start:end + 1])


def _num(v):
    if v is None or v == "":
        return None
    try:
        return round(float(str(v).replace(",", "").replace("₹", "").strip()), 2)
    except ValueError:
        return None


def normalise(raw: dict) -> dict:
    """Map the AI's JSON onto the same shape the rule-based parser produces."""
    taxes = []
    rate = _num(raw.get("gst_rate_percent"))
    for t in ("cgst", "sgst", "igst"):
        amt = _num(raw.get(t))
        if amt:
            r = (rate / 2 if t != "igst" and rate else rate) if rate else None
            taxes.append({"type": t.upper(), "rate": r, "amount": amt})
    vg = (raw.get("vendor_gstin") or "").replace(" ", "").upper() or None
    bg = (raw.get("buyer_gstin") or "").replace(" ", "").upper() or None
    d = parse_date(raw.get("invoice_date"))
    due = parse_date(raw.get("due_date"))
    items = []
    for it in raw.get("line_items") or []:
        items.append({"description": (it.get("description") or "").strip(), "hsn": it.get("hsn"),
                      "quantity": _num(it.get("quantity")), "unit_price": _num(it.get("unit_price")),
                      "amount": _num(it.get("amount")), "confidence": 0.9})
    acct = "".join(ch for ch in str(raw.get("bank_account_number") or "") if ch.isdigit()) or None
    return {
        "vendor_name": raw.get("vendor_name"),
        "vendor_gstin": vg if vg and (G.is_valid(vg) or len(vg) == 15) else vg,
        "buyer_gstin": bg,
        "invoice_number": (raw.get("invoice_number") or "").strip() or None,
        "invoice_date": d.isoformat() if d else None,
        "due_date": due.isoformat() if due else None,
        "po_number": norm_po_number(raw.get("po_number")) or ((raw.get("po_number") or "").strip() or None),
        "currency": raw.get("currency") or "INR",
        "line_items": items,
        "subtotal": _num(raw.get("subtotal")),
        "taxes": taxes,
        "tax_total": round(sum(t["amount"] for t in taxes), 2) if taxes else None,
        "total": _num(raw.get("total")),
        "amount_in_words": raw.get("amount_in_words"),
        "bank": {"account_number": acct, "ifsc": (raw.get("bank_ifsc") or "").upper() or None,
                 "bank_name": raw.get("bank_name"), "account_name": raw.get("account_name")},
        "emails": raw.get("contact_emails") or [],
    }


def _overloaded(msg: str) -> bool:
    return any(s in msg for s in (" 503", " 429", " 500", "UNAVAILABLE", "high demand", "RESOURCE_EXHAUSTED"))


def ai_extract(pdf_bytes: bytes, api_key: str, model: str, budget_s: float = 25.0,
               fallback_models: list[str] | None = None) -> dict:
    """Returns {'fields': {...}, 'model': ..., 'api': ..., 'latency_ms': ...}; raises AIError on failure.

    If a model is overloaded (503/429) the next free-tier model is tried immediately. The whole call is bounded
    by `budget_s`, so a slow or unavailable AI service never stalls a run.
    """
    pdf_b64 = base64.b64encode(pdf_bytes).decode()
    t0 = time.perf_counter()
    errors = []
    models = [model] + [m for m in (fallback_models if fallback_models is not None else FALLBACK_MODELS) if m != model]
    with httpx.Client() as client:
        for m in models:
            for api, fn in (("interactions v1beta", _call_interactions), ("generateContent v1beta", _call_legacy)):
                left = budget_s - (time.perf_counter() - t0)
                if left < 4:
                    errors.append("time budget used up")
                    raise AIError(" | ".join(errors)[:900])
                try:
                    text = fn(client, api_key, m, pdf_b64, timeout=min(9.0, left))
                    try:
                        fields = normalise(_parse_json(text))
                    except (AttributeError, TypeError, ValueError, KeyError) as exc:
                        raise AIError(f"AI output did not match the expected schema ({exc})") from exc
                    return {"fields": fields, "model": m, "api": api,
                            "latency_ms": round((time.perf_counter() - t0) * 1000), "attempts": errors}
                except AIError as exc:
                    msg = str(exc)
                    errors.append(f"{m} / {api}: {msg[:200]}")
                    if " 401" in msg or " 403" in msg:
                        raise AIError(f"Gemini rejected the API key ({msg[:160]})") from exc
                    if _overloaded(msg):
                        break  # same capacity pool on the other endpoint - move to the next model
                except (httpx.HTTPError, json.JSONDecodeError) as exc:
                    errors.append(f"{m} / {api}: {exc.__class__.__name__}: {str(exc)[:160]}")
                    break
    raise AIError(" | ".join(errors)[:900])
