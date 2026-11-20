"""Stage 2 - read the document.

Produces positioned words grouped into lines and segments (column-ish chunks of a
line separated by wide gaps) for both digital PDFs (text layer) and scanned PDFs (OCR).
Coordinates are always in PDF points so downstream heuristics are path-independent.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image, ImageOps

try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None

MAX_PAGES = 3
MIN_TEXT_CHARS = 40
OCR_DPI = int(os.environ.get("OCR_DPI", "300"))  # 200 is ~2.4x faster with the same results on typical scans

# PDFium (via pypdfium2) is not thread-safe: every call must hold this lock.
PDFIUM_LOCK = threading.RLock()


def page_count(pdf_bytes: bytes) -> int:
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(pdf_bytes)
        try:
            return len(doc)
        finally:
            doc.close()


MAX_RENDER_PX = 3600  # longest side; caps memory for oversized pages on small hosts
OCR_TIMEOUT_S = 90


def _render_pages(pdf_bytes: bytes, scale: float, max_pages: int, mode: str = "L"):
    """Render pages to PIL images (copied out of PDFium memory) under the global lock."""
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(pdf_bytes)
        try:
            n_pages = len(doc)
            width = float(doc[0].get_width()) if n_pages else 595.0
            images, heights, scales = [], [], []
            for pno in range(min(n_pages, max_pages)):
                page = doc[pno]
                w, h = float(page.get_width()), float(page.get_height())
                s = min(scale, MAX_RENDER_PX / max(w, h, 1.0))
                images.append(page.render(scale=s).to_pil().convert(mode))
                heights.append(h)
                scales.append(s)
                page.close()
            return n_pages, width, images, heights, scales
        finally:
            doc.close()


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    conf: float | None = None
    size: float | None = None

    @property
    def height(self) -> float:
        return self.bottom - self.top


@dataclass
class Segment:
    words: list[Word]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def x0(self) -> float:
        return self.words[0].x0

    @property
    def x1(self) -> float:
        return self.words[-1].x1


@dataclass
class Line:
    words: list[Word]
    page: int
    segments: list[Segment] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def top(self) -> float:
        return min(w.top for w in self.words)

    @property
    def bottom(self) -> float:
        return max(w.bottom for w in self.words)

    @property
    def x0(self) -> float:
        return self.words[0].x0

    @property
    def max_size(self) -> float:
        return max((w.size or w.height) for w in self.words)


@dataclass
class DocText:
    method: str                      # "text-layer" | "ocr" | "none"
    pages: int
    lines: list[Line]
    page_width: float
    ocr_confidence: float | None = None
    deskew_angle: float | None = None
    ocr_engine: str | None = None
    char_count: int = 0
    warnings: list[str] = field(default_factory=list)
    timings_ms: dict = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return "\n".join(l.text for l in self.lines)


class ReadError(Exception):
    pass


_NUM_HEAD = re.compile(r"^[\d,]*\d[;:,]$")
_NUM_TAIL = re.compile(r"^\d{2,3}(?:[.,]\d{1,2})?$")


def _merge_split_numbers(words: list[Word]) -> list[Word]:
    """OCR sometimes splits '3,217.50' into '3;' + '217.50'. Re-join tightly spaced numeric fragments."""
    out: list[Word] = []
    for w in words:
        prev = out[-1] if out else None
        if (prev is not None and _NUM_HEAD.match(prev.text) and _NUM_TAIL.match(w.text)
                and w.x0 - prev.x1 < 0.9 * max(prev.height, w.height, 1.0)):
            out[-1] = Word(text=prev.text[:-1] + "," + w.text, x0=prev.x0, x1=w.x1, top=min(prev.top, w.top),
                           bottom=max(prev.bottom, w.bottom), conf=min(prev.conf or 0, w.conf or 0), size=None)
        else:
            out.append(w)
    return out


def tesseract_cmd() -> str | None:
    cmd = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract")
    if not cmd:
        candidate = os.path.join(sys.prefix, "bin", "tesseract")
        cmd = candidate if os.path.exists(candidate) else None
    return cmd


def ocr_available() -> bool:
    return pytesseract is not None and tesseract_cmd() is not None


def _group_lines(words: list[Word], page: int, y_tol_ratio: float = 0.45) -> list[Line]:
    if not words:
        return []
    words = sorted(words, key=lambda w: (round(w.bottom, 1), w.x0))
    lines: list[list[Word]] = []
    for w in words:
        placed = False
        mid = (w.top + w.bottom) / 2
        for ln in reversed(lines[-4:]):
            ref = statistics.median([(x.top + x.bottom) / 2 for x in ln])
            h = statistics.median([x.height for x in ln] + [w.height])
            if abs(mid - ref) <= max(2.0, h * y_tol_ratio):
                ln.append(w)
                placed = True
                break
        if not placed:
            lines.append([w])
    out = []
    for ln in lines:
        ln.sort(key=lambda w: w.x0)
        out.append(Line(words=ln, page=page))
    out.sort(key=lambda l: l.top)
    return out


def _segment(line: Line, ocr: bool = False) -> None:
    """Split a line into segments where the horizontal gap is much wider than a space."""
    hs = [w.size or w.height for w in line.words]
    unit = statistics.median(hs) if hs else 9.0
    gap_limit = max(9.0, unit * 1.6) if ocr else max(6.0, unit * 1.15)
    segs, cur = [], [line.words[0]]
    for prev, w in zip(line.words, line.words[1:]):
        if w.x0 - prev.x1 > gap_limit:
            segs.append(Segment(cur))
            cur = [w]
        else:
            cur.append(w)
    segs.append(Segment(cur))
    line.segments = segs


def _read_text_layer(pdf_bytes: bytes) -> tuple[list[Line], int, float, int]:
    lines: list[Line] = []
    chars = 0
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        n_pages = len(pdf.pages)
        width = float(pdf.pages[0].width) if pdf.pages else 595.0
        offset = 0.0
        for pno, page in enumerate(pdf.pages[:MAX_PAGES], start=1):
            raw = page.extract_words(keep_blank_chars=False, use_text_flow=False, extra_attrs=["size"])
            words = [Word(text=r["text"], x0=float(r["x0"]), x1=float(r["x1"]), top=float(r["top"]) + offset,
                          bottom=float(r["bottom"]) + offset, size=float(r.get("size") or 0) or None)
                     for r in raw if r["text"].strip()]
            chars += sum(len(w.text) for w in words)
            lines.extend(_group_lines(words, pno))
            offset += float(page.height)
    return lines, n_pages, width, chars


def estimate_skew(gray: Image.Image) -> float:
    """Projection-profile deskew: find the rotation that makes text rows sharpest."""
    small = gray.copy()
    small.thumbnail((900, 900))
    ink = small.point(lambda p: 255 if p < 150 else 0)

    def score(angle: float) -> float:
        r = ink.rotate(angle, resample=Image.NEAREST, fillcolor=0).resize((1, ink.height), Image.BOX)
        prof = list(r.get_flattened_data() if hasattr(r, "get_flattened_data") else r.getdata())
        return sum((a - b) ** 2 for a, b in zip(prof, prof[1:]))

    best = max((a / 10 for a in range(-30, 31, 3)), key=score)
    fine = [best + d / 100 for d in range(-15, 16, 3)]
    return round(max(fine, key=score), 2)


def _ocr_pages(pdf_bytes: bytes) -> tuple[list[Line], int, float, float, float, int]:
    if not ocr_available():
        raise ReadError("OCR engine (Tesseract) is not installed on this server")
    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd()
    try:
        n_pages, width_pt, images, heights, scales = _render_pages(pdf_bytes, OCR_DPI / 72.0, MAX_PAGES)
    except Exception as exc:
        raise ReadError(f"Could not render the page for OCR: {exc}") from exc
    lines: list[Line] = []
    confs: list[tuple[float, int]] = []
    angles = []
    offset = 0.0
    chars = 0
    for pno, img in enumerate(images):
        scale = scales[pno]
        angle = estimate_skew(img)
        angles.append(angle)
        if abs(angle) >= 0.1:
            img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=255)
        img = ImageOps.autocontrast(img, cutoff=1)
        try:
            data = pytesseract.image_to_data(img, config="--oem 1 --psm 6", output_type=pytesseract.Output.DICT,
                                             timeout=OCR_TIMEOUT_S)
        except Exception as exc:  # timeout, engine crash, missing language data
            raise ReadError(f"OCR failed: {exc}") from exc
        groups: dict[tuple, list[Word]] = {}
        for i, txt in enumerate(data["text"]):
            t = (txt or "").strip()
            conf = float(data["conf"][i])
            if not t or conf < 0:
                continue
            x, y, w, h = (data[k][i] / scale for k in ("left", "top", "width", "height"))
            word = Word(text=t, x0=x, x1=x + w, top=y + offset, bottom=y + h + offset, conf=conf, size=None)
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            groups.setdefault(key, []).append(word)
            confs.append((conf, len(t)))
            chars += len(t)
        page_lines = []
        for ws in groups.values():
            ws.sort(key=lambda w: w.x0)
            page_lines.append(Line(words=_merge_split_numbers(ws), page=pno + 1))
        page_lines.sort(key=lambda l: l.top)
        lines.extend(page_lines)
        offset += heights[pno]
    total_w = sum(n for _, n in confs) or 1
    mean_conf = sum(c * n for c, n in confs) / total_w
    return lines, n_pages, width_pt, round(mean_conf, 1), (angles[0] if angles else 0.0), chars


def read_document(pdf_bytes: bytes, force_ocr: bool = False) -> DocText:
    t0 = time.perf_counter()
    warnings: list[str] = []
    try:
        lines, n_pages, width, chars = _read_text_layer(pdf_bytes)
    except Exception as exc:  # corrupt or encrypted PDF
        raise ReadError(f"Could not open PDF: {exc}") from exc
    t1 = time.perf_counter()
    timings = {"text_layer": round((t1 - t0) * 1000)}
    if n_pages > MAX_PAGES:
        warnings.append(f"Document has {n_pages} pages; only the first {MAX_PAGES} were read.")

    if chars >= MIN_TEXT_CHARS and not force_ocr:
        for ln in lines:
            _segment(ln)
        return DocText(method="text-layer", pages=n_pages, lines=lines, page_width=width, char_count=chars,
                       warnings=warnings, timings_ms=timings)

    if chars:
        warnings.append(f"Text layer has only {chars} characters - treating the document as a scan.")
    try:
        lines, n_pages, width, conf, angle, chars = _ocr_pages(pdf_bytes)
    except ReadError as exc:
        warnings.append(str(exc))
        return DocText(method="none", pages=n_pages, lines=[], page_width=width, warnings=warnings,
                       timings_ms=timings)
    timings["ocr"] = round((time.perf_counter() - t1) * 1000)
    for ln in lines:
        _segment(ln, ocr=True)
    return DocText(method="ocr", pages=n_pages, lines=lines, page_width=width, ocr_confidence=conf,
                   deskew_angle=angle, ocr_engine="Tesseract 5 (LSTM)", char_count=chars, warnings=warnings,
                   timings_ms=timings)


def render_preview(pdf_bytes: bytes, page: int = 0, width_px: int = 900) -> bytes:
    with PDFIUM_LOCK:
        doc = pdfium.PdfDocument(pdf_bytes)
        try:
            if len(doc) == 0:
                raise ReadError("PDF has no pages")
            pg = doc[min(page, len(doc) - 1)]
            scale = width_px / float(pg.get_width())
            img = pg.render(scale=scale).to_pil().convert("RGB")
            pg.close()
        finally:
            doc.close()
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
