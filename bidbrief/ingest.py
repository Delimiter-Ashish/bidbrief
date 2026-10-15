"""F1 Ingest: read every PDF page by page, OCR pages with no text layer,
detect sections from headings, and cut each page into chunks that keep
file, page, section, OCR flag and character offsets.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pymupdf

from .config import get_settings
from .schemas import Chunk, PageInfo

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, float], None]

MIN_TEXT_CHARS = 25  # a page with fewer characters than this has "no text layer"
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200


# ------------------------------------------------------------------ results
@dataclass
class IngestedPackage:
    package_id: str
    files: list[str]  # original file names, in order
    working: dict[str, Path]  # original name -> PDF used for text and highlights
    pages: list[PageInfo]
    page_text: dict[tuple[str, int], str]
    chunks: list[Chunk]
    ocr_engine: str = "none"
    notes: list[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)


# ------------------------------------------------------------------ helpers
def list_pdfs(package_dir: Path) -> list[Path]:
    pdfs = sorted(p for p in package_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
    if not pdfs:
        raise FileNotFoundError(f"No PDF files found in {package_dir}")
    return pdfs


def ocrmypdf_available() -> bool:
    return shutil.which("ocrmypdf") is not None and shutil.which("tesseract") is not None


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def _pages_without_text(doc: pymupdf.Document) -> list[int]:
    """0-based indexes of pages that have (almost) no extractable text."""
    out = []
    for i, page in enumerate(doc):
        if len(page.get_text("text").strip()) < MIN_TEXT_CHARS:
            out.append(i)
    return out


def _page_has_images(page: pymupdf.Page) -> bool:
    try:
        if page.get_images(full=False):
            return True
        # vector-drawn scans are rare; count big drawings as "something there"
        return len(page.get_drawings()) > 50
    except Exception:
        return False


def _run_ocrmypdf(src: Path, dst: Path, jobs: int) -> bool:
    cmd = [
        "ocrmypdf",
        "--skip-text",  # only OCR pages that have no text layer
        "--output-type", "pdf",  # no PDF/A conversion (no Ghostscript needed for output)
        "--optimize", "0",
        "--rotate-pages",
        "--deskew",
        "-j", str(max(1, jobs)),
        "-q",
        str(src),
        str(dst),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60 * 30)
    except Exception as exc:  # pragma: no cover - environment specific
        log.warning("ocrmypdf could not run: %s", exc)
        return False
    if proc.returncode != 0 or not dst.exists():
        # --deskew/--rotate-pages need extra tools on some systems; retry plain
        cmd2 = [c for c in cmd if c not in {"--rotate-pages", "--deskew"}]
        try:
            proc = subprocess.run(cmd2, capture_output=True, text=True, timeout=60 * 30)
        except Exception as exc:  # pragma: no cover
            log.warning("ocrmypdf could not run: %s", exc)
            return False
        if proc.returncode != 0 or not dst.exists():
            log.warning("ocrmypdf failed on %s: %s", src.name, (proc.stderr or "")[-800:])
            return False
    return True


def _pymupdf_ocr(src: Path, dst: Path, page_indexes: list[int]) -> list[int]:
    """Fallback OCR with PyMuPDF + Tesseract: replace each scanned page with an
    OCR'd copy (same image, invisible text layer, same page size)."""
    done: list[int] = []
    doc = pymupdf.open(src)
    try:
        for idx in sorted(page_indexes, reverse=True):
            page = doc[idx]
            if page.rotation:
                page.remove_rotation()
            rect = page.rect
            pix = page.get_pixmap(dpi=300)
            pix.set_dpi(300, 300)
            try:
                ocr_bytes = pix.pdfocr_tobytes(language="eng")
            except Exception as exc:
                log.warning("PyMuPDF OCR failed on page %d: %s", idx + 1, exc)
                continue
            ocr_doc = pymupdf.open("pdf", ocr_bytes)
            new_page_doc = pymupdf.open()
            new_page = new_page_doc.new_page(width=rect.width, height=rect.height)
            new_page.show_pdf_page(new_page.rect, ocr_doc, 0)
            doc.delete_page(idx)
            doc.insert_pdf(new_page_doc, start_at=idx)
            done.append(idx)
        doc.save(dst, garbage=3, deflate=True)
    finally:
        doc.close()
    return sorted(done)


def prepare_working_copy(src: Path, work_pdf_dir: Path, ocr: bool = True) -> tuple[Path, set[int], str]:
    """Copy (and OCR if needed) one PDF. Returns (working path, OCR'd page
    numbers 1-based, engine used)."""
    work_pdf_dir.mkdir(parents=True, exist_ok=True)
    dst = work_pdf_dir / src.name
    with pymupdf.open(src) as doc:
        no_text = [i for i in _pages_without_text(doc) if _page_has_images(doc[i])]

    if not no_text or not ocr:
        shutil.copy2(src, dst)
        return dst, set(), "none"

    settings = get_settings()
    engine = "none"
    if ocrmypdf_available():
        tmp = dst.with_suffix(".ocr.tmp.pdf")
        if _run_ocrmypdf(src, tmp, settings.ocr_jobs):
            tmp.replace(dst)
            engine = "ocrmypdf"
    if engine == "none" and tesseract_available():
        try:
            _pymupdf_ocr(src, dst, no_text)
            engine = "pymupdf-tesseract"
        except Exception as exc:  # pragma: no cover
            log.warning("Fallback OCR failed for %s: %s", src.name, exc)
    if engine == "none":
        shutil.copy2(src, dst)
        return dst, set(), "none"

    # Which of the scanned pages really have text now?
    ocr_pages: set[int] = set()
    with pymupdf.open(dst) as doc:
        for i in no_text:
            if i < len(doc) and len(doc[i].get_text("text").strip()) >= MIN_TEXT_CHARS:
                ocr_pages.add(i + 1)
    return dst, ocr_pages, engine


# ------------------------------------------------------------ sections
_SECTION_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("Table of Contents", re.compile(r"\btable of contents\b", re.I)),
    ("Addendum", re.compile(r"^\s*addendum\s+(no\.?|number|#)?\s*\d+", re.I)),
    ("Invitation for Bids", re.compile(
        r"\b(invitation (for|to) bid(s|ders)?|advertisement for bids|notice to (contractors|bidders)|request for bids|invitation for bids)\b",
        re.I)),
    ("Instructions to Bidders", re.compile(r"\binstructions to bidders\b", re.I)),
    ("Bid Form", re.compile(
        r"\b((bid|proposal) form|form of (general )?(bid|proposal)|bid schedule|schedule of (bid )?(items|prices|quantities)|unit price (bid )?schedule|bid proposal)\b",
        re.I)),
    ("Bonds", re.compile(r"^\s*(bid bond|performance bond|payment bond|labor and materials? (payment )?bond)\b", re.I)),
    ("Agreement", re.compile(r"^\s*((standard )?form of )?(agreement|contract agreement)\b(?! (is|shall|to))", re.I)),
    ("Supplementary Conditions", re.compile(
        r"\b(supplementary (general )?conditions|supplemental (general )?conditions|special (provisions|conditions))\b", re.I)),
    ("General Conditions", re.compile(r"\bgeneral conditions\b", re.I)),
    ("Wage Rates", re.compile(r"\b(prevailing wage|wage rates?|wage schedule|minimum wage rates)\b", re.I)),
    ("Drawings List", re.compile(r"\b(list of drawings|drawing index|index of drawings|sheet index|list of sheets)\b", re.I)),
]

_SPEC_SECTION = re.compile(r"^\s*section\s+(\d{2}\s?\d{2}\s?\d{2}(?:\.\d{2})?)\b\s*[-–—:.]?\s*(.*)$", re.I)
_SPEC_FOOTER = re.compile(r"^\s*(\d{2}\s\d{2}\s\d{2}(?:\.\d{2})?)\s*[-–—]\s*\d+\s*$")
_HEADING_PREFIX = re.compile(r"^(?:(?:document|section|article|part)\s+)?[\d\s.\-–—:]*", re.I)
_DIVISION = re.compile(r"^\s*division\s+(\d{1,2})\b\s*[-–—:.]?\s*(.*)$", re.I)


def _heading_lines(text: str) -> list[str]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    candidates = lines[:6] + lines[-3:]
    return [ln for ln in candidates if len(ln) <= 100]


def _looks_like_toc(text: str) -> bool:
    return len(re.findall(r"\.{6,}\s*\d+", text)) >= 5


def detect_section(text: str) -> str | None:
    """Return a section name if this page starts (or is footed by) a known heading."""
    if _looks_like_toc(text):
        return "Table of Contents"
    heads = _heading_lines(text)
    for line in heads:
        m = _SPEC_SECTION.match(line)
        if m:
            num = re.sub(r"\s+", " ", m.group(1)).strip()
            title = re.sub(r"\(?\bcontinued\b\)?", "", m.group(2), flags=re.I).strip(" -–—:.()")
            title = re.sub(r"\s+", " ", title)[:60]
            return f"Specifications — Section {num}" + (f" {title.title()}" if title else "")
    for name, pattern in _SECTION_RULES:
        for line in heads:
            if pattern.search(line):
                # Heading lines are short and mostly capitals, or the pattern starts the line
                letters = [c for c in line if c.isalpha()]
                upper_ratio = (sum(c.isupper() for c in letters) / len(letters)) if letters else 0
                stripped = _HEADING_PREFIX.sub("", line)
                starts = pattern.match(stripped) is not None
                short_title = len(line) < 45 and line[:1].isupper() and not line.endswith((".", ","))
                if upper_ratio > 0.6 or starts or short_title:
                    return name
    for line in heads:
        m = _DIVISION.match(line)
        if m:
            return f"Specifications — Division {m.group(1)}"
        m = _SPEC_FOOTER.match(line)
        if m:
            return f"Specifications — Section {m.group(1)}"
    return None


def top_section(section: str | None) -> str:
    if not section:
        return "Other"
    return section.split(" — ")[0]


# ------------------------------------------------------------- chunking
def chunk_page(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[tuple[int, int]]:
    """Split page text into (start, end) spans, breaking at line ends."""
    n = len(text)
    if n == 0:
        return []
    if n <= size:
        return [(0, n)]
    spans = []
    start = 0
    while start < n:
        end = min(n, start + size)
        if end < n:
            cut = text.rfind("\n", start + size // 2, end)
            if cut == -1:
                cut = text.rfind(" ", start + size // 2, end)
            if cut != -1:
                end = cut + 1
        spans.append((start, end))
        if end >= n:
            break
        nxt = end - overlap
        # start the overlap at a line/word boundary
        b = text.find("\n", nxt, end)
        if b == -1:
            b = text.find(" ", nxt, end)
        start = (b + 1) if b != -1 else nxt
        if start <= spans[-1][0]:
            start = end
    return spans


def clean_page_text(text: str) -> str:
    text = text.replace("\x00", "").replace("­", "")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------- main
def ingest_package(
    package_id: str,
    package_dir: Path,
    work_dir: Path,
    progress: ProgressFn | None = None,
) -> IngestedPackage:
    settings = get_settings()
    progress = progress or (lambda msg, frac: None)
    pdfs = list_pdfs(package_dir)
    work_pdf_dir = work_dir / "pdf"
    work_pdf_dir.mkdir(parents=True, exist_ok=True)

    files: list[str] = []
    working: dict[str, Path] = {}
    pages: list[PageInfo] = []
    page_text: dict[tuple[str, int], str] = {}
    chunks: list[Chunk] = []
    notes: list[str] = []
    engines: set[str] = set()

    for fi, src in enumerate(pdfs):
        progress(f"Reading pages: {src.name}", fi / max(1, len(pdfs)))
        try:
            wpath, ocr_pages, engine = prepare_working_copy(src, work_pdf_dir, ocr=settings.ocr_enabled)
        except Exception as exc:
            notes.append(f"Could not open {src.name}: {exc}")
            pages.append(PageInfo(file=src.name, page=0, problem=f"File could not be opened: {exc}"))
            continue
        engines.add(engine)
        files.append(src.name)
        working[src.name] = wpath

        doc = pymupdf.open(wpath)
        try:
            current_section: str | None = None
            for i, page in enumerate(doc):
                pno = i + 1
                problem = None
                try:
                    raw = page.get_text("text")
                except Exception as exc:
                    raw = ""
                    problem = f"Page could not be read: {exc}"
                text = clean_page_text(raw)
                sec = detect_section(text) if text else None
                if sec:
                    current_section = sec
                is_ocr = pno in ocr_pages
                if not text and problem is None and _page_has_images(page):
                    problem = "No readable text (scanned page and OCR was not available or failed)"
                elif 0 < len(text) < MIN_TEXT_CHARS and _page_has_images(page):
                    problem = "Very little readable text on a scanned page"
                pages.append(PageInfo(file=src.name, page=pno, section=current_section,
                                      is_ocr=is_ocr, char_count=len(text), problem=problem))
                page_text[(src.name, pno)] = text
                for ci, (a, b) in enumerate(chunk_page(text)):
                    chunks.append(Chunk(
                        chunk_id=f"{fi:02d}-{pno:04d}-{ci:02d}",
                        file=src.name, page=pno, section=current_section,
                        text=text[a:b], is_ocr=is_ocr, char_start=a, char_end=b,
                    ))
        finally:
            doc.close()

    progress("Pages read", 1.0)
    used = sorted(e for e in engines if e != "none")
    return IngestedPackage(
        package_id=package_id, files=files, working=working, pages=pages,
        page_text=page_text, chunks=chunks, ocr_engine=",".join(used) or "none", notes=notes,
    )


# ------------------------------------------------------------- storage
def save_ingested(ing: IngestedPackage, work_dir: Path) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    with open(work_dir / "chunks.jsonl", "w", encoding="utf-8") as fh:
        for c in ing.chunks:
            fh.write(c.model_dump_json() + "\n")
    meta = {
        "package_id": ing.package_id,
        "files": ing.files,
        "working": {k: v.name for k, v in ing.working.items()},
        "pages": [p.model_dump() for p in ing.pages],
        "ocr_engine": ing.ocr_engine,
        "notes": ing.notes,
    }
    (work_dir / "ingest.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    with open(work_dir / "page_text.jsonl", "w", encoding="utf-8") as fh:
        for (f, p), t in ing.page_text.items():
            fh.write(json.dumps({"file": f, "page": p, "text": t}) + "\n")


def load_ingested(work_dir: Path) -> IngestedPackage:
    meta = json.loads((work_dir / "ingest.json").read_text(encoding="utf-8"))
    chunks = [Chunk.model_validate_json(line) for line in
              (work_dir / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    page_text: dict[tuple[str, int], str] = {}
    pt = work_dir / "page_text.jsonl"
    if pt.exists():
        for line in pt.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                page_text[(row["file"], int(row["page"]))] = row["text"]
    return IngestedPackage(
        package_id=meta["package_id"], files=meta["files"],
        working={k: work_dir / "pdf" / v for k, v in meta["working"].items()},
        pages=[PageInfo.model_validate(p) for p in meta["pages"]],
        page_text=page_text, chunks=chunks,
        ocr_engine=meta.get("ocr_engine", "none"), notes=meta.get("notes", []),
    )
