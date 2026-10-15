"""F3 Bid Items: find the bid form pages and pull the item list.

Order of attempts per page:
  1. pdfplumber table extraction (ruled tables), with header-based column mapping
  2. text-line parsing from word positions (unruled tables)
  3. model reading of the page image (only if 1 and 2 fail and a vision model is set)

Quantities and units are copied exactly as printed. Nothing is computed.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import pdfplumber
import pymupdf

from .citations import normalize_text
from .ingest import IngestedPackage, top_section
from .schemas import BidItem, BidItemsResult

log = logging.getLogger(__name__)

UNITS = {
    "LF", "L.F.", "L.F", "LIN FT", "LIN. FT.", "LINEAR FEET", "LFT", "VLF", "VF", "V.F.", "V.L.F.", "VERT FT", "VFT",
    "SF", "S.F.", "SQ FT", "SQ. FT.", "SY", "S.Y.", "SQ YD", "SQ. YD.", "SQYD", "CY", "C.Y.", "CU YD", "CU. YD.", "CYD",
    "CF", "C.F.", "EA", "EA.", "EACH", "LS", "L.S.", "LUMP SUM", "LUMP", "TON", "TONS", "TN", "GAL", "GALS", "GALLON",
    "GALLONS", "MGAL", "HR", "HRS", "HOUR", "HOURS", "DAY", "DAYS", "WK", "WEEK", "MO", "MONTH", "MONTHS", "LB", "LBS",
    "POUND", "POUNDS", "AC", "ACRE", "ACRES", "MSF", "MFBM", "FBM", "UNIT", "UNITS", "ALLOW", "ALLOW.", "ALLOWANCE",
    "$", "DOLLARS", "EST", "FT", "FOOT", "FEET", "YD", "SQ", "M", "M2", "M3", "KG", "LOAD", "LOADS", "TRIP", "SET",
    "SETS", "PAIR", "LOT", "JOB", "MH", "CWT", "STA", "STATION", "MILE", "MI", "LIN. FT", "S.Y", "C.Y", "L.S",
}
_UNIT_KEYS = {u.replace(".", "").replace(" ", "").upper() for u in UNITS}

ITEM_RE = re.compile(r"^(?:[A-Z]{1,3}[-.\s]?)?\d{1,4}(?:[.-]\d{1,3}){0,2}[A-Za-z]?\.?$")
QTY_RE = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
QTY_UNIT_RE = re.compile(r"^((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*([A-Za-z][A-Za-z.\s]{0,12})$")
MONEY_WORDS = re.compile(r"(dollars|cents|_{3,}|\$\s*_|\bper\b.*\bin words\b|\(words\)|\(figures\))", re.I)


def is_unit(token: str) -> bool:
    t = (token or "").replace(".", "").replace(" ", "").upper()
    return t in _UNIT_KEYS


def is_item_no(token: str) -> bool:
    return bool(ITEM_RE.match((token or "").strip()))


def clean_item_no(token: str) -> str:
    return (token or "").strip().rstrip(".").strip()


def _cell(v: object) -> str:
    if v is None:
        return ""
    return re.sub(r"\s+", " ", str(v)).strip()


# ------------------------------------------------------------ page finding
_ROW_LINE = re.compile(
    r"^\s*(?:[A-Z]{1,3}[-.\s]?)?\d{1,4}(?:[.-]\d{1,3})?[A-Za-z]?\.?\s+.*?\b(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*"
    r"(?:L\.?F\.?|V\.?L?\.?F\.?|S\.?F\.?|S\.?Y\.?|C\.?Y\.?|EA\.?|EACH|L\.?S\.?|LUMP SUM|TONS?|GAL|HRS?|DAYS?|ALLOW\w*|CF|AC|MO)\b",
    re.I | re.M,
)


def bid_form_score(text: str, section: str | None) -> float:
    low = text.lower()
    score = 0.0
    if top_section(section) == "Bid Form":
        score += 3.5
    if "item" in low and ("quantity" in low or "qty" in low or "est." in low) and "unit" in low:
        score += 2
    if "unit price" in low or "total price" in low or "in words" in low or "in figures" in low:
        score += 1
    rows = len(_ROW_LINE.findall(text))
    score += min(rows / 2.0, 5)
    if rows < 2:
        score = min(score, 3.0)
    return score


def find_bid_form_pages(ing: IngestedPackage, threshold: float = 4.5) -> list[tuple[str, int]]:
    by_file: dict[str, list[int]] = {}
    sec = {(p.file, p.page): p.section for p in ing.pages}
    for (f, p), text in ing.page_text.items():
        if bid_form_score(text, sec.get((f, p))) >= threshold:
            by_file.setdefault(f, []).append(p)
    out: list[tuple[str, int]] = []
    for f in ing.files:
        pages = sorted(by_file.get(f, []))
        out.extend((f, p) for p in pages)
    return out


# -------------------------------------------------------- table parsing
@dataclass
class ColMap:
    item: int
    desc: int | None
    qty: int | None
    unit: int | None
    ncols: int


def _map_header(row: list[str]) -> ColMap | None:
    low = [c.lower() for c in row]
    item = desc = qty = unit = None
    for i, c in enumerate(low):
        if not c:
            continue
        if item is None and re.search(r"\bitem\b\s*(no|#|number|num)?\.?$|^no\.?$|^item\s*no|^bid item$", c) and "desc" not in c:
            item = i
        elif qty is None and re.search(r"quan|qty|est(imated)?\.?\s*(quan|qty)?$|^est", c):
            qty = i
        elif unit is None and re.search(r"^units?\b|^unit of|^u/?m$|^uom$", c) and "price" not in c and "cost" not in c:
            unit = i
        elif desc is None and re.search(r"desc|work|item with|^item$|^bid item description", c):
            desc = i
    if item is None and desc is not None and re.search(r"^item", low[desc]):
        # "Item" header used for the description column; numbers in col 0
        item = 0 if desc != 0 else None
    if item is None or (qty is None and unit is None):
        return None
    return ColMap(item=item, desc=desc, qty=qty, unit=unit, ncols=len(row))


def _guess_desc_col(rows: list[list[str]], cm: ColMap) -> int | None:
    if cm.desc is not None:
        return cm.desc
    used = {cm.item, cm.qty, cm.unit}
    best, best_len = None, 0
    for j in range(cm.ncols):
        if j in used:
            continue
        total = sum(len(r[j]) for r in rows if j < len(r))
        if total > best_len:
            best, best_len = j, total
    return best


def _row_from_cells(cells: list[str], cm: ColMap, desc_col: int | None) -> tuple[str, str, str, str] | None:
    def g(j: int | None) -> str:
        return cells[j] if j is not None and j < len(cells) else ""

    item = clean_item_no(g(cm.item))
    qty = g(cm.qty)
    unit = g(cm.unit)
    desc = g(desc_col)
    if cm.unit is None or not unit:
        m = QTY_UNIT_RE.match(qty)
        if m:
            qty, unit = m.group(1), m.group(2).strip()
    if cm.qty is None and unit:
        m = QTY_UNIT_RE.match(unit)
        if m:
            qty, unit = m.group(1), m.group(2).strip()
    # Some forms put "1,200 LF" in the unit column and quantity in another.
    desc = MONEY_WORDS.split(desc)[0].strip(" -:,") if MONEY_WORDS.search(desc) else desc
    return item, desc, qty, unit


def parse_tables_on_page(plumber_page: "pdfplumber.page.Page", last_map: ColMap | None) -> tuple[list[dict], ColMap | None]:
    rows_out: list[dict] = []
    try:
        tables = plumber_page.extract_tables()
    except Exception as exc:  # pragma: no cover - malformed pages
        log.debug("pdfplumber failed: %s", exc)
        return [], last_map
    for table in tables or []:
        cells_rows = [[_cell(c) for c in row] for row in table if row]
        if not cells_rows:
            continue
        cm = None
        start = 0
        for hi, row in enumerate(cells_rows[:4]):
            cm = _map_header(row)
            if cm:
                start = hi + 1
                break
        if cm is None and last_map is not None and len(cells_rows[0]) == last_map.ncols:
            cm, start = last_map, 0
        if cm is None:
            continue
        last_map = cm
        body = cells_rows[start:]
        desc_col = _guess_desc_col(body, cm)
        for cells in body:
            parsed = _row_from_cells(cells, cm, desc_col)
            if not parsed:
                continue
            item, desc, qty, unit = parsed
            if not item and desc and not qty and rows_out:
                if not MONEY_WORDS.search(desc) and "total" not in desc.lower():
                    rows_out[-1]["description"] = (rows_out[-1]["description"] + " " + desc).strip()
                continue
            if not is_item_no(item):
                continue
            if not qty and not unit:
                continue
            rows_out.append({"item_no": item, "description": desc, "quantity": qty, "unit": unit, "method": "table"})
    return rows_out, last_map


# ---------------------------------------------------- text-line parsing
_Q = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_PAT_QTY_UNIT_DESC = re.compile(rf"^(?P<item>\S+)\s+(?P<qty>{_Q})\s+(?P<unit>[A-Za-z$][A-Za-z.]*(?:\s(?:SUM|FT\.?|YD\.?))?)\s+(?P<desc>.+)$")
_PAT_DESC_QTY_UNIT = re.compile(rf"^(?P<item>\S+)\s+(?P<desc>.+?)\s+(?P<qty>{_Q})\s+(?P<unit>[A-Za-z$][A-Za-z.]*(?:\s(?:SUM|FT\.?|YD\.?))?)(?:\s+.*)?$")
_PAT_QTY_UNIT_GLUED = re.compile(rf"^(?P<item>\S+)\s+(?P<qty>{_Q})\s*(?P<unit>[A-Za-z][A-Za-z.]*)\s+(?P<desc>.+)$")


def _page_lines(page: pymupdf.Page) -> list[str]:
    words = page.get_text("words")
    if not words:
        return []
    rm = page.rotation_matrix
    items = []
    for w in words:
        r = pymupdf.Rect(w[:4]) * rm
        items.append(((r.y0 + r.y1) / 2, r.x0, w[4], r.y1 - r.y0))
    items.sort(key=lambda t: (t[0], t[1]))
    lines: list[list[tuple[float, float, str, float]]] = []
    for it in items:
        if lines:
            ly = sum(t[0] for t in lines[-1]) / len(lines[-1])
            tol = max(2.5, 0.45 * it[3])
            if abs(it[0] - ly) <= tol:
                lines[-1].append(it)
                continue
        lines.append([it])
    return [" ".join(t[2] for t in sorted(ln, key=lambda t: t[1])) for ln in lines]


def parse_text_lines(page: pymupdf.Page) -> list[dict]:
    rows: list[dict] = []
    for line in _page_lines(page):
        line = line.strip()
        if not line:
            continue
        first = line.split(" ", 1)[0]
        if not is_item_no(first):
            if rows and not re.search(r"\d", line) and not MONEY_WORDS.search(line) and len(line) < 120 \
                    and not line.isupper() and "total" not in line.lower():
                rows[-1]["description"] += " " + line
            continue
        hit = None
        for pat in (_PAT_QTY_UNIT_DESC, _PAT_DESC_QTY_UNIT, _PAT_QTY_UNIT_GLUED):
            m = pat.match(line)
            if m and is_unit(m.group("unit")):
                hit = m
                break
        if not hit:
            continue
        desc = hit.group("desc")
        desc = MONEY_WORDS.split(desc)[0].strip(" -:,$") if MONEY_WORDS.search(desc) else desc.strip()
        desc = re.sub(r"\s+\$?\s*[\d,]+\.\d{2}.*$", "", desc).strip()  # drop trailing price figures
        rows.append({
            "item_no": clean_item_no(hit.group("item")), "description": desc,
            "quantity": hit.group("qty"), "unit": hit.group("unit").strip(), "method": "text",
        })
    return rows


# ------------------------------------------------------ model fallback
def parse_with_model(page: pymupdf.Page, page_text: str) -> list[dict]:
    from .llm import get_llm  # local import: tables work without any model

    llm = get_llm()
    if llm is None or not llm.supports_images:
        return []
    from .schemas import LLMTable

    png = page.get_pixmap(dpi=150).tobytes("png")
    prompt = (
        "This image is one page of a construction bid form. Copy every numbered bid item row.\n"
        "For each row return item_no, description, quantity and unit exactly as printed.\n"
        "Rules: copy characters exactly; never compute, round or convert a quantity; leave out prices, "
        "totals and words like 'dollars'. If there are no bid item rows, return an empty list."
    )
    try:
        table = llm.generate(prompt, LLMTable, images=[png], purpose="table")
    except Exception as exc:
        log.warning("Model table reading failed: %s", exc)
        return []
    norm_page = normalize_text(page_text)
    rows = []
    for r in table.rows:
        item = clean_item_no(r.item_no)
        if not is_item_no(item):
            continue
        on_page = normalize_text(r.quantity) in norm_page and normalize_text(item) in norm_page
        rows.append({"item_no": item, "description": r.description.strip(), "quantity": r.quantity.strip(),
                     "unit": r.unit.strip(), "method": "model", "on_page": on_page})
    return rows


# ------------------------------------------------------------ scoring
def score_row(row: dict) -> tuple[float, str]:
    conf = 1.0
    notes = []
    if not is_unit(row["unit"]):
        conf -= 0.25
        notes.append("unit not recognised")
    if not QTY_RE.match(row["quantity"] or ""):
        conf -= 0.3
        notes.append("quantity is not a plain number")
    if len(row["description"]) < 4:
        conf -= 0.3
        notes.append("description missing or short")
    if row.get("method") == "text":
        conf -= 0.1
    if row.get("method") == "model":
        conf -= 0.2
        notes.append("read from the page image")
        if not row.get("on_page", False):
            conf -= 0.4
            notes.append("item number or quantity not found in page text")
    return max(0.0, round(conf, 2)), "; ".join(notes)


def _item_key(item_no: str) -> tuple[str, int, str] | None:
    m = re.match(r"^([A-Za-z]*)[-.\s]?(\d+)(.*)$", item_no)
    if not m:
        return None
    return m.group(1).upper(), int(m.group(2)), m.group(3)


def numbering_gaps(items: list[BidItem]) -> list[str]:
    groups: dict[str, list[int]] = {}
    for it in items:
        k = _item_key(it.item_no)
        if k and not k[2]:  # only simple numbers like 12 or A-12
            groups.setdefault(k[0], []).append(k[1])
    gaps: list[str] = []
    for prefix, nums in groups.items():
        nums = sorted(set(nums))
        for a, b in zip(nums, nums[1:]):
            if 1 < b - a <= 4:
                for n in range(a + 1, b):
                    gaps.append(f"{prefix + '-' if prefix else ''}{n}")
    return gaps


# --------------------------------------------------------------- main
def extract_bid_items(ing: IngestedPackage, use_model: bool = True,
                      addendum_pages: set[tuple[str, int]] | None = None) -> BidItemsResult:
    pages = find_bid_form_pages(ing)
    items: list[BidItem] = []
    methods: set[str] = set()
    by_file: dict[str, list[int]] = {}
    for f, p in pages:
        by_file.setdefault(f, []).append(p)

    for f, plist in by_file.items():
        path = ing.working[f]
        doc = pymupdf.open(path)
        last_map: ColMap | None = None
        try:
            with pdfplumber.open(path) as pdf:
                for p in plist:
                    rows, last_map = parse_tables_on_page(pdf.pages[p - 1], last_map)
                    text_rows = parse_text_lines(doc[p - 1])
                    if len(text_rows) > len(rows) + 1:
                        rows = text_rows
                    if not rows and use_model:
                        rows = parse_with_model(doc[p - 1], ing.page_text.get((f, p), ""))
                    for r in rows:
                        conf, note = score_row(r)
                        methods.add(r["method"])
                        items.append(BidItem(
                            item_no=r["item_no"], description=r["description"], quantity=r["quantity"],
                            unit=r["unit"], page=p, confidence=conf, file=f, note=note,
                        ))
        finally:
            doc.close()

    # Duplicates. A revised row printed in an addendum is expected: keep both and
    # say which replaces which. Any other repeat is flagged for a person to check.
    add_pages = addendum_pages or set()
    groups: dict[str, list[BidItem]] = {}
    for it in items:
        groups.setdefault(it.item_no.upper(), []).append(it)
    dups: list[str] = []
    for no, group in groups.items():
        if len(group) < 2:
            continue
        originals = [i for i in group if (i.file, i.page) not in add_pages]
        revised = [i for i in group if (i.file, i.page) in add_pages]
        if len(originals) == 1 and revised:
            originals[0].note = "; ".join(x for x in [originals[0].note, "replaced by a revised row in an addendum"] if x)
            for r in revised:
                r.note = "; ".join(x for x in [r.note, "revised in an addendum"] if x)
            continue
        dups.append(group[0].item_no)
        for it in group:
            it.confidence = max(0.0, round(it.confidence - 0.3, 2))
            it.note = "; ".join(x for x in [it.note, "item number appears more than once"] if x)
    dups.sort()

    return BidItemsResult(
        items=items, bid_form_pages=pages, numbering_gaps=numbering_gaps(items),
        duplicates=dups, method="+".join(sorted(methods)),
    )


def items_to_frame(items: list[BidItem]):
    import pandas as pd

    return pd.DataFrame([{
        "Item": it.item_no, "Description": it.description, "Quantity": it.quantity, "Unit": it.unit,
        "File": it.file, "Page": it.page, "Check": "Please check" if it.confidence < 0.8 else "",
        "Why": it.note,
    } for it in items], columns=["Item", "Description", "Quantity", "Unit", "File", "Page", "Check", "Why"])


def items_to_excel(items: list[BidItem], title: str = "Bid Items") -> bytes:
    """Excel file with quantities stored as text, exactly as printed."""
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Bid Items"
    ws.append([title[:200]])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(["Quantities are copied exactly as printed in the bid form. Rows marked 'Please check' need a person to confirm them against the page."])
    ws.append([])
    headers = ["Item", "Description", "Quantity", "Unit", "File", "Page", "Check", "Why"]
    ws.append(headers)
    for c in ws[4]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
    yellow = PatternFill("solid", fgColor="FFF2B3")
    for it in items:
        ws.append([it.item_no, it.description, it.quantity, it.unit, it.file, it.page,
                   "Please check" if it.confidence < 0.8 else "", it.note])
        row = ws.max_row
        for col in (1, 3, 4):
            ws.cell(row=row, column=col).number_format = "@"  # keep as text
        if it.confidence < 0.8:
            for c in ws[row]:
                c.fill = yellow
        ws.cell(row=row, column=2).alignment = Alignment(wrap_text=True, vertical="top")
    widths = [9, 70, 12, 9, 28, 7, 13, 40]
    for i, w in enumerate(widths):
        ws.column_dimensions[chr(65 + i)].width = w
    ws.freeze_panes = "A5"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def items_to_csv(items: list[BidItem]) -> bytes:
    return items_to_frame(items).to_csv(index=False).encode("utf-8")
