"""The citation check: the core of BidBrief.

Every claim carries a quote and a page. Code (never the model) confirms the
quote appears on that page with a fuzzy match of 0.9 or higher, finds the
quote's word boxes for the yellow highlight, and checks that every number in
the claim is printed in its verified quotes (so nothing is computed, rounded
or converted).
"""
from __future__ import annotations

import io
import re
import threading
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import pymupdf
from rapidfuzz import fuzz

from .config import get_settings
from .schemas import Citation, LLMCitation

MIN_FUZZY_LEN = 12  # shorter quotes must match exactly

_QUOTE_MAP = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", "­": "",
})


def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").translate(_QUOTE_MAP).lower()
    s = re.sub(r"(?<=[a-z])- +(?=[a-z])", "", s)  # undo line-end hyphenation
    s = s.replace("…", "...")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _norm_token(s: str) -> str:
    return unicodedata.normalize("NFKC", s).translate(_QUOTE_MAP).lower()


# ------------------------------------------------------------ page index
@dataclass
class PageIndex:
    text: str  # normalized page text built from words
    spans: list[tuple[int, int]]  # per word: span in `text`
    rects: list[tuple[float, float, float, float]]
    lines: list[tuple[int, int]]  # per word: (block, line)
    rotation_matrix: tuple[float, ...]
    width: float
    height: float


def build_page_index(page: pymupdf.Page) -> PageIndex:
    words = page.get_text("words")  # x0, y0, x1, y1, word, block, line, wno
    parts: list[str] = []
    spans: list[tuple[int, int]] = []
    rects: list[tuple[float, float, float, float]] = []
    lines: list[tuple[int, int]] = []
    pos = 0
    join_next = False
    for i, w in enumerate(words):
        tok = _norm_token(w[4])
        if not tok:
            continue
        nxt_line = None
        if i + 1 < len(words):
            nxt_line = (words[i + 1][5], words[i + 1][6])
        hyphen_break = (
            tok.endswith("-") and len(tok) > 2 and tok[-2].isalpha()
            and nxt_line is not None and nxt_line != (w[5], w[6])
            and i + 1 < len(words) and words[i + 1][4][:1].islower()
        )
        if hyphen_break:
            tok = tok[:-1]
        if parts and not join_next:
            parts.append(" ")
            pos += 1
        start = pos
        parts.append(tok)
        pos += len(tok)
        spans.append((start, pos))
        rects.append((w[0], w[1], w[2], w[3]))
        lines.append((w[5], w[6]))
        join_next = hyphen_break
    rm = page.rotation_matrix
    return PageIndex(
        text="".join(parts), spans=spans, rects=rects, lines=lines,
        rotation_matrix=(rm.a, rm.b, rm.c, rm.d, rm.e, rm.f),
        width=page.rect.width, height=page.rect.height,
    )


class PageStore:
    """Opens the working PDFs of one package and caches page indexes."""

    def __init__(self, working: dict[str, Path]):
        self.working = {k: Path(v) for k, v in working.items()}
        self._docs: dict[str, pymupdf.Document] = {}
        self._cache: dict[tuple[str, int], PageIndex] = {}
        self._lock = threading.RLock()

    def resolve_file(self, file: str) -> str | None:
        if file in self.working:
            return file
        if not file and len(self.working) == 1:
            return next(iter(self.working))
        low = (file or "").lower().strip()
        for name in self.working:
            if name.lower() == low or Path(name).stem.lower() == Path(low).stem.lower():
                return name
        if len(self.working) == 1:
            return next(iter(self.working))
        return None

    def doc(self, file: str) -> pymupdf.Document:
        with self._lock:
            if file not in self._docs:
                self._docs[file] = pymupdf.open(self.working[file])
            return self._docs[file]

    def page_count(self, file: str) -> int:
        with self._lock:
            return len(self.doc(file))

    def index(self, file: str, page: int) -> PageIndex | None:
        key = (file, page)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            if file not in self.working:
                return None
            d = self.doc(file)
            if page < 1 or page > len(d):
                return None
            idx = build_page_index(d[page - 1])
            if len(self._cache) > 400:
                self._cache.clear()
            self._cache[key] = idx
            return idx

    def close(self) -> None:
        with self._lock:
            for d in self._docs.values():
                d.close()
            self._docs.clear()
            self._cache.clear()


# ---------------------------------------------------------- verification
@dataclass
class Match:
    ok: bool
    score: float
    start: int = -1
    end: int = -1


def match_quote(quote: str, page_text_norm: str, threshold: float | None = None) -> Match:
    threshold = get_settings().quote_threshold if threshold is None else threshold
    q = normalize_text(quote).strip(" .,;:'\"")
    if len(q) < 4 or not page_text_norm:
        return Match(False, 0.0)
    i = page_text_norm.find(q)
    if i != -1:
        return Match(True, 100.0, i, i + len(q))
    if len(q) < MIN_FUZZY_LEN or len(q) > len(page_text_norm) + 5:
        return Match(False, 0.0)
    res = fuzz.partial_ratio_alignment(q, page_text_norm, score_cutoff=threshold)
    if res is None:
        return Match(False, 0.0)
    return Match(True, float(res.score), res.dest_start, res.dest_end)


def verify_on_page(store: PageStore, file: str, page: int, quote: str) -> Match:
    idx = store.index(file, page)
    if idx is None:
        return Match(False, 0.0)
    return match_quote(quote, idx.text)


def check_citations(
    store: PageStore,
    cites: Iterable[LLMCitation | Citation],
    candidate_pages: Iterable[tuple[str, int]] = (),
) -> list[Citation]:
    """Turn model citations into checked Citations.

    A quote found on the cited page is verified. A quote not on the cited page
    but found on one of the excerpt pages the model was shown is moved to that
    page (code sets the page, not the model). Otherwise it is unverified.
    """
    candidates = list(dict.fromkeys(candidate_pages))
    out: list[Citation] = []
    seen: set[tuple[str, int, str]] = set()
    for c in cites:
        quote = (c.quote or "").strip()
        file = store.resolve_file(c.file) or (candidates[0][0] if candidates else c.file)
        page = int(c.page)
        verified = False
        if file in store.working:
            verified = verify_on_page(store, file, page, quote).ok
        if not verified and quote:
            ordered = sorted(candidates, key=lambda fp: (fp[0] != file, abs(fp[1] - page)))
            for f2, p2 in ordered:
                if (f2, p2) == (file, page):
                    continue
                if verify_on_page(store, f2, p2, quote).ok:
                    file, page, verified = f2, p2, True
                    break
        key = (file, page, normalize_text(quote))
        if key in seen:
            continue
        seen.add(key)
        out.append(Citation(file=file, page=page, quote=quote, verified=verified))
    return out


# ----------------------------------------------------------- numbers guard
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _canon_num(s: str) -> str:
    s = s.replace(",", "")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s.lstrip("0") or "0"


def numbers_in(text: str) -> set[str]:
    return {_canon_num(m.group(0).rstrip(",.")) for m in _NUM.finditer(text or "") if m.group(0).strip(",.")}


def unsupported_numbers(claim: str, quotes: Iterable[str]) -> list[str]:
    """Numbers in `claim` that are not printed in any of `quotes`."""
    have: set[str] = set()
    for q in quotes:
        have |= numbers_in(unicodedata.normalize("NFKC", q))
    return sorted(n for n in numbers_in(unicodedata.normalize("NFKC", claim)) if n not in have)


def claim_is_verified(claim: str | None, citations: list[Citation]) -> bool:
    good = [c for c in citations if c.verified]
    if not good:
        return False
    if claim and unsupported_numbers(claim, [c.quote for c in good]):
        return False
    return True


# ---------------------------------------------------------- highlighting
def quote_rects(store: PageStore, file: str, page: int, quote: str) -> list[tuple[float, float, float, float]]:
    """Word boxes (unrotated page coordinates) covering the quote, merged per line."""
    idx = store.index(file, page)
    if idx is None:
        return []
    m = match_quote(quote, idx.text)
    if not m.ok:
        return []
    boxes: dict[tuple[int, int], list[float]] = {}
    order: list[tuple[int, int]] = []
    for (a, b), r, ln in zip(idx.spans, idx.rects, idx.lines):
        if b <= m.start or a >= m.end:
            continue
        if ln not in boxes:
            boxes[ln] = list(r)
            order.append(ln)
        else:
            bx = boxes[ln]
            bx[0], bx[1] = min(bx[0], r[0]), min(bx[1], r[1])
            bx[2], bx[3] = max(bx[2], r[2]), max(bx[3], r[3])
    return [tuple(boxes[k]) for k in order]  # type: ignore[misc]


def render_page(
    store: PageStore,
    file: str,
    page: int,
    quotes: Iterable[str] = (),
    zoom: float = 1.8,
    close_up: bool = False,
) -> tuple[bytes, int]:
    """Render a page to PNG with yellow boxes over the quotes.
    With close_up=True, crop to a band around the highlighted passage (easier to
    read on a phone). Returns (png bytes, number of highlight boxes drawn)."""
    from PIL import Image, ImageDraw

    with store._lock:
        d = store.doc(file)
        pg = d[page - 1]
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        rot = pg.rotation_matrix
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    n = 0
    scale = pymupdf.Matrix(zoom, zoom)
    y_min, y_max = float("inf"), float("-inf")
    x_min, x_max = float("inf"), float("-inf")
    for q in quotes:
        for r in quote_rects(store, file, page, q):
            rect = pymupdf.Rect(r) * rot * scale
            rect.normalize()
            pad = 2 * zoom
            box = [rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad]
            draw.rectangle(box, fill=(255, 221, 0, 95), outline=(214, 160, 0, 255), width=max(1, int(zoom)))
            y_min, y_max = min(y_min, box[1]), max(y_max, box[3])
            x_min, x_max = min(x_min, box[0]), max(x_max, box[2])
            n += 1
    out = Image.alpha_composite(img, overlay).convert("RGB")
    if close_up and n:
        margin = 45 * zoom
        top = int(max(0, y_min - margin))
        bottom = int(min(out.height, y_max + margin))
        left = max(0.0, x_min - margin)
        right = min(float(out.width), x_max + margin)
        min_w = 0.45 * out.width  # keep enough context around short quotes
        if right - left < min_w:
            mid = (left + right) / 2
            left, right = max(0.0, mid - min_w / 2), min(float(out.width), mid + min_w / 2)
        out = out.crop((int(left), top, int(right), bottom))
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), n


@lru_cache(maxsize=8)
def _store_cache(key: tuple[tuple[str, str], ...]) -> PageStore:
    return PageStore({k: Path(v) for k, v in key})


def shared_store(working: dict[str, Path]) -> PageStore:
    """One cached PageStore per package (used by the app)."""
    return _store_cache(tuple(sorted((k, str(v)) for k, v in working.items())))
