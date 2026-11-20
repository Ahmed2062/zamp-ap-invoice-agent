"""GSTIN (Indian GST registration number) validation and OCR repair.

Format (15 chars): 2-digit state code + 10-char PAN + entity digit + 'Z' + check char.
The check char is a mod-36 checksum over the first 14 characters.
"""
from __future__ import annotations

import re

_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
GSTIN_RE = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")

STATE_CODES = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh",
    "05": "Uttarakhand", "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "19": "West Bengal", "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh",
    "23": "Madhya Pradesh", "24": "Gujarat", "27": "Maharashtra", "29": "Karnataka", "30": "Goa",
    "32": "Kerala", "33": "Tamil Nadu", "36": "Telangana", "37": "Andhra Pradesh",
}

_DIGIT_FIX = {"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2", "G": "6"}
_ALPHA_FIX = {"0": "O", "1": "I", "5": "S", "8": "B", "2": "Z", "6": "G"}


def check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        product = _CHARS.index(ch) * (2 if i % 2 else 1)
        total += product // 36 + product % 36
    return _CHARS[(36 - total % 36) % 36]


def make_gstin(state_code: str, pan: str, entity: str = "1") -> str:
    body = f"{state_code}{pan}{entity}Z"
    return body + check_char(body)


def is_valid(gstin: str | None) -> bool:
    if not gstin or not GSTIN_RE.fullmatch(gstin):
        return False
    return check_char(gstin[:14]) == gstin[14]


def state_of(gstin: str | None) -> str | None:
    if not gstin or len(gstin) < 2:
        return None
    return gstin[:2]


def _position_fix(c: str) -> str:
    digit_slots = {0, 1, 7, 8, 9, 10}
    alpha_slots = {2, 3, 4, 5, 6, 11}
    fixed = []
    for i, ch in enumerate(c):
        if i in digit_slots and not ch.isdigit():
            ch = _DIGIT_FIX.get(ch, ch)
        elif i in alpha_slots and not ch.isalpha():
            ch = _ALPHA_FIX.get(ch, ch)
        elif i == 13 and ch == "2":
            ch = "Z"
        fixed.append(ch)
    return "".join(fixed)


def repair(candidate: str) -> str | None:
    """Fix common OCR errors using the positional structure of a GSTIN plus its checksum.

    1. 15 chars: fix letter/digit confusions by slot type ('O' in a digit slot -> '0').
    2. 16 chars: OCR inserted a spurious character - try each single deletion.
    A repair is only accepted if exactly one candidate passes the checksum.
    """
    c = re.sub(r"[^0-9A-Z]", "", candidate.upper())
    if len(c) == 15:
        result = _position_fix(c)
        return result if is_valid(result) else None
    if len(c) == 16:
        # Deletions are combined with the strict pattern only (no substitutions): letting both
        # vary produces false checksum matches.
        valid = {r for r in (c[:i] + c[i + 1:] for i in range(16)) if is_valid(r)}
        return valid.pop() if len(valid) == 1 else None
    return None


_CANDIDATE_RE = re.compile(r"\b[0-9OILSBZDQG]{2}[A-Z0-9]{12,15}\b")


def _edit_distance_le1(a: str, b: str) -> bool:
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(x != y for x, y in zip(a, b)) == 1
    if la > lb:
        a, b = b, a
    return any(b[:i] + b[i + 1:] == a for i in range(len(b)))


def find_all(text: str, known: set[str] | None = None, allow_repair: bool = True) -> list[tuple[str, str, str]]:
    """GSTINs in text as (gstin, raw_text_seen, how) with how in {exact, master, checksum}.

    OCR errors are fixed in two guarded ways (only when allow_repair - i.e. for OCR text):
      * master  - the raw token is within one edit of exactly one GSTIN we already know
                  (our own or a vendor-master GSTIN);
      * checksum - a structural repair that passes the checksum; deletion repairs are only
                  trusted when they land on a known GSTIN, because a random token has a
                  ~1/36 chance of passing the checksum by accident.
    On digital text a checksum failure is a real error on the invoice and is never repaired.
    """
    found: list[tuple[str, str, str]] = []
    seen = set()
    for m in _CANDIDATE_RE.finditer(text.upper()):
        raw = m.group(0)
        g, how = (raw, "exact") if is_valid(raw) else (None, None)
        if g is None and allow_repair and known:
            near = [k for k in known if _edit_distance_le1(raw, k)]
            if len(near) == 1:
                g, how = near[0], "master"
        if g is None and allow_repair:
            r = repair(raw)
            if r and (len(raw) == 15 or not known or r in known):
                g, how = r, "checksum"
        if g and g not in seen:
            seen.add(g)
            found.append((g, raw, how))
    return found


def invalid_candidates(text: str) -> list[str]:
    """Tokens shaped exactly like a GSTIN whose checksum fails (e.g. a typo on a digital invoice)."""
    return [m.group(0) for m in GSTIN_RE.finditer(text.upper()) if not is_valid(m.group(0))]
