"""F2 Brief: fixed fields, each cited or "Not found in the documents"."""
from __future__ import annotations

import logging

from .citations import PageStore, check_citations, claim_is_verified
from .index import SearchIndex, excerpt_pages, format_excerpts
from .ingest import IngestedPackage, top_section
from .llm import BaseLLM, LLMError
from .schemas import (
    BRIEF_FIELDS, BidItemsResult, Brief, BriefField, Citation, LLMBrief, LLMCitation, ReadFirstItem, RiskFlag,
)

log = logging.getLogger(__name__)

FIELD_QUERIES: dict[str, list[str]] = {
    "project_name": ["project name contract title invitation for bids", "contract no. project"],
    "owner": ["owner town city awarding authority water department board of public works"],
    "location": ["location of the work streets site town", "work is located"],
    "scope": ["scope of work the work consists of furnish install", "work includes water main sewer drainage"],
    "bid_due": ["bids received until date time opened publicly read", "sealed bids due"],
    "pre_bid_meeting": ["pre-bid conference meeting site visit", "prebid meeting mandatory"],
    "completion_time": ["contract time completion calendar days substantially complete", "completion date"],
    "bid_security": ["bid security bid deposit bid bond five percent", "bid guaranty certified check"],
    "bonds": ["performance bond payment bond labor materials one hundred percent", "bonds required contract price"],
}

FIELD_GUIDE = """Fields (use these exact names):
- project_name: the project or contract name, as printed.
- owner: the town, city, district or agency that owns the project (the awarding authority).
- location: where the work is (town, streets or site), as printed.
- scope: 3 to 5 plain-English sentences on what the work is. Each fact must be backed by a citation.
- bid_due: when bids are due (date and time) and where or how they are submitted, as printed.
- pre_bid_meeting: date, time and place of any pre-bid meeting or site visit, and whether it is mandatory.
- completion_time: the contract time or completion date, as printed.
- bid_security: the bid security required (amount and form), as printed.
- bonds: the performance and payment bonds required, as printed.
If an addendum excerpt changes a value, give the changed value and cite the addendum."""


def build_brief_fields(llm: BaseLLM, idx: SearchIndex, store: PageStore, ing: IngestedPackage) -> tuple[list[BriefField], dict[str, int]]:
    queries = [q for qs in FIELD_QUERIES.values() for q in qs] + ["addendum changed bid opening date time"]
    chunks = idx.multi_search(queries, per_query=3, limit=34)
    # The first pages usually hold the invitation for bids: always include them.
    first_pages = [(ing.files[0], p) for p in (1, 2, 3)] if ing.files else []
    extra = [c for c in idx.chunks_on_pages(first_pages) if c.chunk_id not in {x.chunk_id for x in chunks}]
    chunks = extra[:4] + chunks
    excerpts = format_excerpts(chunks, max_chars=55000)
    cand = excerpt_pages(chunks)

    prompt = (
        "Fill in the bid brief for this public construction bid package, using only the excerpts below.\n\n"
        f"{FIELD_GUIDE}\n\n"
        "Return one entry per field, in the order listed. For each: value (null if the excerpts do not say), and 1 to 3 "
        "citations (file, page, quote copied exactly from that excerpt). A field with a value must have a citation.\n\n"
        f"EXCERPTS:\n{excerpts}"
    )
    stats = {"model_citations": 0, "verified_citations": 0}
    try:
        out = llm.generate(prompt, LLMBrief, purpose="brief")
    except LLMError as exc:
        log.error("Brief failed: %s", exc)
        return [BriefField(name=n, value=None, citations=[]) for n, _ in BRIEF_FIELDS], stats

    by_name = {f.name.strip().lower(): f for f in out.fields}
    fields: list[BriefField] = []
    for name, _label in BRIEF_FIELDS:
        f = by_name.get(name)
        if f is None or not (f.value or "").strip():
            fields.append(BriefField(name=name, value=None, citations=[]))
            continue
        cits = check_citations(store, f.citations, cand)
        stats["model_citations"] += len(cits)
        stats["verified_citations"] += sum(c.verified for c in cits)
        fields.append(BriefField(name=name, value=f.value.strip(), citations=cits))
    return fields, stats


def _first_line_quote(ing: IngestedPackage, file: str, page: int) -> str | None:
    text = ing.page_text.get((file, page), "")
    for line in text.splitlines():
        line = line.strip()
        if len(line) >= 12 and any(ch.isalpha() for ch in line):
            return line[:120]
    return None


def _page_item(ing: IngestedPackage, store: PageStore, file: str, page: int, reason: str) -> ReadFirstItem | None:
    q = _first_line_quote(ing, file, page)
    if not q:
        return None
    cits = check_citations(store, [LLMCitation(file=file, page=page, quote=q)])
    if not any(c.verified for c in cits):
        return None
    return ReadFirstItem(reason=reason, citations=[c for c in cits if c.verified])


def build_read_first(ing: IngestedPackage, store: PageStore, items: BidItemsResult, flags: list[RiskFlag],
                     limit: int = 5) -> list[ReadFirstItem]:
    """The five pages to read first: the bid form, then the biggest risks, then the
    pages that change the standard contract. Built by code from verified citations."""
    out: list[ReadFirstItem] = []
    used: set[tuple[str, int]] = set()

    def add(item: ReadFirstItem | None) -> None:
        if item is None or len(out) >= limit:
            return
        c = item.citations[0]
        if (c.file, c.page) in used:
            return
        used.add((c.file, c.page))
        out.append(item)

    if items.bid_form_pages:
        f, p = items.bid_form_pages[0]
        add(_page_item(ing, store, f, p, "The bid form: the items and quantities you will price."))

    order = {"High": 0, "Medium": 1, "Low": 2}
    for flag in sorted(flags, key=lambda r: order.get(r.severity, 3)):
        good = [c for c in flag.citations if c.verified]
        if good:
            add(ReadFirstItem(reason=f"{flag.category}: {flag.reason}", citations=[good[0]]))

    for section, reason in [
        ("Supplementary Conditions", "Changes to the standard contract terms for this job."),
        ("Instructions to Bidders", "How, when and where the bid must be submitted."),
        ("Invitation for Bids", "The basic facts: what, where, when bids are due."),
        ("Wage Rates", "Wage rates that apply to this job."),
    ]:
        for pinfo in ing.pages:
            if top_section(pinfo.section) == section and pinfo.char_count > 0:
                add(_page_item(ing, store, pinfo.file, pinfo.page, reason))
                break
    return out


def empty_brief(note: str) -> Brief:
    return Brief(fields=[BriefField(name=n, value=None, citations=[]) for n, _ in BRIEF_FIELDS], note=note)


def field_status(field: BriefField) -> str:
    """'found' (verified), 'unverified' or 'missing'."""
    if field.value is None:
        return "missing"
    return "found" if claim_is_verified(field.value, field.citations) else "unverified"


def verified_citations(cits: list[Citation]) -> list[Citation]:
    return [c for c in cits if c.verified]
