"""Money helpers: parsing amounts from invoice text, Indian formatting, amount-in-words."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

TWO = Decimal("0.01")

_AMOUNT_RE = re.compile(r"(?<![\w/.])-?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d{1,2})?(?![\w/%]|\.\d)")


def q2(value) -> Decimal:
    return Decimal(str(value)).quantize(TWO, rounding=ROUND_HALF_UP)


def parse_amount(token: str | None) -> Decimal | None:
    """Parse '4,97,665.00', 'Rs. 42,185', '₹1,51,158.00', 'INR 2,47,800' -> Decimal."""
    if token is None:
        return None
    s = str(token)
    s = re.sub(r"(?i)(rs\.?|inr|₹)", "", s).strip()
    s = s.replace(" ", "")
    m = re.fullmatch(r"-?[\d,]*\d(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return Decimal(s.replace(",", ""))
    except InvalidOperation:
        return None


def amounts_in(text: str) -> list[Decimal]:
    """All amount-like numbers in a line of text, in order (percentages and dates excluded)."""
    text = re.sub(r"(?i)\b(rs|inr)\b\.?|₹", " ", text)
    out = []
    for m in _AMOUNT_RE.finditer(text):
        v = parse_amount(m.group(0))
        if v is not None:
            out.append(v)
    return out


def inr(value, decimals: int = 2) -> str:
    """Format with Indian digit grouping: 497665 -> '4,97,665.00'."""
    d = Decimal(str(value))
    s = f"{abs(d):.{decimals}f}"
    whole, _, frac = s.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return ("-" if d < 0 else "") + whole + (f".{frac}" if frac else "")


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
         "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen",
         "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return _TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")


def _three(n: int) -> str:
    h, r = divmod(n, 100)
    parts = []
    if h:
        parts.append(_ONES[h] + " Hundred")
    if r:
        parts.append(_two(r))
    return " ".join(parts)


def int_to_words(n: int) -> str:
    if n == 0:
        return "Zero"
    parts = []
    crore, n = divmod(n, 10**7)
    lakh, n = divmod(n, 10**5)
    thousand, n = divmod(n, 1000)
    if crore:
        parts.append(int_to_words(crore) + " Crore")
    if lakh:
        parts.append(_two(lakh) + " Lakh")
    if thousand:
        parts.append(_two(thousand) + " Thousand")
    if n:
        parts.append(_three(n))
    return " ".join(parts)


def amount_in_words(value, prefix: str = "Rupees") -> str:
    d = q2(value)
    rupees = int(d)
    paise = int((d - rupees) * 100)
    text = f"{prefix} {int_to_words(rupees)}"
    if paise:
        text += f" and {_two(paise)} Paise"
    return text + " Only"


_WORD_VALUES = {w.lower(): i for i, w in enumerate(_ONES) if w}
_WORD_VALUES.update({w.lower(): i * 10 for i, w in enumerate(_TENS) if w})
_SCALES = {"hundred": 100, "thousand": 1000, "lakh": 10**5, "lakhs": 10**5, "lac": 10**5,
           "crore": 10**7, "crores": 10**7, "million": 10**6}


def _words_to_int(tokens: list[str]) -> int | None:
    total, current, seen = 0, 0, False
    for t in tokens:
        if t in _WORD_VALUES:
            current += _WORD_VALUES[t]
            seen = True
        elif t == "hundred":
            current = max(current, 1) * 100
            seen = True
        elif t in _SCALES:
            scale = _SCALES[t]
            total += max(current, 1) * scale
            current = 0
            seen = True
        elif t in ("and", "only", "rupees", "rupee", "inr", "rs"):
            continue
        else:
            return None
    return total + current if seen else None


def words_to_amount(text: str | None) -> Decimal | None:
    """'Rupees Four Lakh Ninety Seven Thousand Six Hundred Sixty Five and Fifty Paise Only' -> 497665.50"""
    if not text:
        return None
    s = re.sub(r"[^a-z ]", " ", text.lower().replace("-", " "))
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return None
    rupee_part, paise_part = s, ""
    m = re.search(r"^(.*)\band\b(.*?)\bpaise\b", s)
    if m:
        rupee_part = m.group(1)
        paise_part = m.group(2)
    rupees = _words_to_int(rupee_part.split())
    if rupees is None:
        return None
    paise = _words_to_int(paise_part.split()) if paise_part.strip() else 0
    if paise is None:
        paise = 0
    return q2(Decimal(rupees) + Decimal(paise) / 100)
