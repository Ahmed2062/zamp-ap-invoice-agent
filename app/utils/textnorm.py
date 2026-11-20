"""Normalisation helpers for identifiers and dates found on invoices."""
from __future__ import annotations

import re
from datetime import date, datetime

PO_RE = re.compile(r"(?<!\d)(45\d{8})(?!\d)")


def norm_invoice_number(value: str | None) -> str | None:
    """Canonical form used for duplicate detection.

    'TS/26-27/0418', 'TS-2627-418' and 'ts 26 27 418' all map to 'TS2627418':
    separators are dropped and leading zeros are stripped from every numeric group.
    """
    if not value:
        return None
    groups = re.findall(r"[A-Za-z]+|\d+", value.upper())
    if not groups:
        return None
    out = []
    for g in groups:
        out.append(str(int(g)) if g.isdigit() else g)
    return "".join(out)


def norm_po_number(value: str | None) -> str | None:
    if not value:
        return None
    m = PO_RE.search(re.sub(r"[\s\-/]", "", value))
    return m.group(1) if m else None


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

_DATE_PATTERNS = [
    # 2026-11-18
    (re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b"), "ymd"),
    # 18/11/2026, 18-11-2026, 18.11.2026 (Indian convention: day first)
    (re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})\b"), "dmy"),
    # 18 Nov 2026, 18-Nov-2026, 18 November, 2026
    (re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s\-]+([A-Za-z]{3,9})[\s\-,]+(\d{4})\b"), "dMy"),
    # November 18, 2026
    (re.compile(r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b"), "Mdy"),
]


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_dates(text: str) -> list[tuple[date, int, int]]:
    """All dates in text as (date, start, end) sorted by position."""
    found = []
    for rx, kind in _DATE_PATTERNS:
        for m in rx.finditer(text):
            a, b, c = m.groups()
            dt = None
            if kind == "ymd":
                dt = _mk(int(a), int(b), int(c))
            elif kind == "dmy":
                dt = _mk(int(c), int(b), int(a))
            elif kind == "dMy":
                mon = _MONTHS.get(b[:3].lower())
                dt = _mk(int(c), mon, int(a)) if mon else None
            elif kind == "Mdy":
                mon = _MONTHS.get(a[:3].lower())
                dt = _mk(int(c), mon, int(b)) if mon else None
            if dt:
                found.append((dt, m.start(), m.end()))
    found.sort(key=lambda x: x[1])
    # Drop overlapping matches (keep the earliest/longest).
    result, last_end = [], -1
    for item in found:
        if item[1] >= last_end:
            result.append(item)
            last_end = item[2]
    return result


def parse_date(text: str | None) -> date | None:
    if not text:
        return None
    if isinstance(text, date):
        return text
    s = str(text).strip()
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        pass
    ds = find_dates(s)
    return ds[0][0] if ds else None


def norm_text(s: str | None) -> str:
    """Lowercase alphanumerics for fuzzy comparisons."""
    if not s:
        return ""
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9.]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_company(s: str | None) -> str:
    """Company name without legal suffixes, for vendor matching."""
    t = norm_text(s)
    t = re.sub(r"\b(pvt|private|ltd|limited|llp|inc|co|company|the)\b\.?", " ", t)
    t = t.replace(".", " ")
    return re.sub(r"\s+", " ", t).strip()
