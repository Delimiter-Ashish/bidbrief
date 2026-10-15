"""Addenda: find them, list what each one changes, and point to both the new
passage (in the addendum) and the original passage it changes (when found).

Two sources of changes:
  * the model reads the addendum pages (every change must quote the addendum,
    verified by the citation check like everything else);
  * code compares bid items: when an addendum carries a revised bid form, any
    item whose quantity, unit or description differs is listed, copied exactly.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

from .citations import PageStore, check_citations, claim_is_verified
from .index import SearchIndex, excerpt_pages, format_excerpts
from .ingest import IngestedPackage, top_section
from .llm import BaseLLM, LLMError
from .schemas import (
    BRIEF_FIELDS, CHANGE_KINDS, AddendumInfo, BidItem, ChangeItem, ChangesResult, LLMChanges, LLMCitation,
)

log = logging.getLogger(__name__)

_ADD_NO = re.compile(r"addendum\s*(?:no\.?|number|#)?\s*(\d{1,3})", re.I)
_FIELD_NAMES = {n for n, _ in BRIEF_FIELDS}


def find_addenda(ing: IngestedPackage) -> list[AddendumInfo]:
    """Addenda are separate files named like an addendum, or pages headed 'ADDENDUM NO. n'."""
    found: dict[str, AddendumInfo] = {}
    pages_by_file: dict[str, list] = {}
    for p in ing.pages:
        if p.page > 0:
            pages_by_file.setdefault(p.file, []).append(p)
    for file, pages in pages_by_file.items():
        file_is_addendum = bool(re.search(r"addend", Path(file).stem, re.I))
        for p in pages:
            in_add = file_is_addendum or top_section(p.section) == "Addendum"
            if not in_add:
                continue
            text = ing.page_text.get((file, p.page), "")
            m = _ADD_NO.search(text[:400]) or _ADD_NO.search(Path(file).stem.replace("_", " "))
            label = f"Addendum No. {int(m.group(1))}" if m else f"Addendum ({Path(file).stem})"
            info = found.setdefault(label, AddendumInfo(label=label, file=file))
            if info.file != file:  # same number in two files: keep them apart
                info = found.setdefault(f"{label} ({file})", AddendumInfo(label=f"{label} ({file})", file=file))
            info.pages.append(p.page)
            if p.problem or p.char_count == 0:
                info.unreadable_pages.append(p.page)
    return sorted(found.values(), key=lambda a: (int(m.group(1)) if (m := _ADD_NO.search(a.label)) else 999, a.file))


def addendum_page_set(addenda: list[AddendumInfo]) -> set[tuple[str, int]]:
    return {(a.file, p) for a in addenda for p in a.pages}


def bid_item_changes(items: list[BidItem], addenda: list[AddendumInfo], store: PageStore) -> list[ChangeItem]:
    """Compare original bid items with revised ones printed in an addendum."""
    add_pages = addendum_page_set(addenda)
    label_of = {(a.file, p): a.label for a in addenda for p in a.pages}
    by_no: dict[str, list[BidItem]] = {}
    for it in items:
        by_no.setdefault(it.item_no.upper(), []).append(it)
    out: list[ChangeItem] = []
    for no, group in by_no.items():
        originals = [i for i in group if (i.file, i.page) not in add_pages]
        revised = [i for i in group if (i.file, i.page) in add_pages]
        if not originals or not revised:
            continue
        old, new = originals[0], revised[-1]
        diffs = []
        if old.quantity.strip() != new.quantity.strip() or old.unit.strip().upper() != new.unit.strip().upper():
            diffs.append(f"quantity changed from {old.quantity} {old.unit} to {new.quantity} {new.unit}")
        if re.sub(r"\W+", " ", old.description).strip().lower() != re.sub(r"\W+", " ", new.description).strip().lower():
            diffs.append("description changed")
        if not diffs:
            continue
        old_c = check_citations(store, [LLMCitation(file=old.file, page=old.page, quote=old.description[:120])])
        new_c = check_citations(store, [LLMCitation(file=new.file, page=new.page, quote=new.description[:120])])
        out.append(ChangeItem(
            addendum=label_of.get((new.file, new.page), "Addendum"), kind="Bid items or quantities",
            summary=f"Item {new.item_no} ({new.description[:60]}): {'; '.join(diffs)}.",
            affects="bid_items", new_citations=new_c, old_citations=old_c, source="bid_form",
        ))
    return out


def _canon_kind(kind: str) -> str:
    low = (kind or "").lower()
    for k in CHANGE_KINDS:
        if k.lower() == low:
            return k
    for k in CHANGE_KINDS:
        if any(w in low for w in k.lower().split() if len(w) > 4):
            return k
    return "Other"


def find_changes(llm: BaseLLM | None, ing: IngestedPackage, idx: SearchIndex, store: PageStore,
                 items: list[BidItem]) -> ChangesResult:
    addenda = find_addenda(ing)
    if not addenda:
        return ChangesResult()
    result = ChangesResult(addenda=addenda)
    unreadable = [f"{a.label} page {p}" for a in addenda for p in a.unreadable_pages]
    if unreadable:
        result.note = ("Some addendum pages could not be read, so changes on them are not listed: "
                       + ", ".join(unreadable) + ". Check them by hand.")

    code_changes = bid_item_changes(items, addenda, store)

    model_changes: list[ChangeItem] = []
    add_pages = addendum_page_set(addenda)
    add_chunks = [c for c in ing.chunks if (c.file, c.page) in add_pages]
    if llm is not None and add_chunks:
        base: dict[str, object] = {}
        for c in add_chunks[:40]:
            for h in idx.search(c.text[:400], k=4, rerank=False):
                if (h.chunk.file, h.chunk.page) not in add_pages:
                    base.setdefault(h.chunk.chunk_id, h.chunk)
            if len(base) >= 24:
                break
        base_chunks = list(base.values())[:24]
        field_list = ", ".join(n for n, _ in BRIEF_FIELDS)
        prompt = (
            "List every change the addenda make to the bid documents, using only the excerpts below.\n"
            "ADDENDUM excerpts are the addenda. ORIGINAL excerpts are the documents they may change.\n"
            "For each change return:\n"
            "- addendum: its name as printed, e.g. 'Addendum No. 1'\n"
            f"- kind: one of {', '.join(CHANGE_KINDS)}\n"
            "- summary: one plain-English line saying what changed (old to new when both are printed)\n"
            f"- affects: one of {field_list}, bid_items, or other\n"
            "- new_citations: the addendum passage (file, page, exact quote)\n"
            "- old_citations: the original passage it changes, only if it is in the ORIGINAL excerpts; otherwise empty\n"
            "Answers to bidders' questions count as changes of kind 'Answers to questions'. "
            "Do not list routine statements such as 'all other terms remain'.\n\n"
            f"ADDENDUM EXCERPTS:\n{format_excerpts(add_chunks, max_chars=40000)}\n\n"
            f"ORIGINAL EXCERPTS:\n{format_excerpts(base_chunks, max_chars=25000)}"
        )
        try:
            out = llm.generate(prompt, LLMChanges, purpose="changes")
        except LLMError as exc:
            log.error("Changes failed: %s", exc)
            out = None
            result.note = (result.note + " " if result.note else "") + "The changes could not be read right now."
        if out is not None:
            add_cand = excerpt_pages(add_chunks)
            base_cand = excerpt_pages(base_chunks)
            labels = {a.label.lower(): a.label for a in addenda}
            for ch in out.changes:
                new_c = [c for c in check_citations(store, ch.new_citations, add_cand) if (c.file, c.page) in add_pages]
                old_c = [c for c in check_citations(store, ch.old_citations, base_cand) if c.verified]
                quotes_ok = claim_is_verified(ch.summary, new_c + old_c)
                if not any(c.verified for c in new_c) or not quotes_ok:
                    result.dropped_unverified += 1
                    continue
                # The page the quote was verified on decides which addendum it is.
                page_label = {(a.file, p): a.label for a in addenda for p in a.pages}
                first = next(c for c in new_c if c.verified)
                label = page_label.get((first.file, first.page))
                if label is None:
                    m = _ADD_NO.search(ch.addendum or "")
                    label = (labels.get(f"addendum no. {int(m.group(1))}") if m else None) or ch.addendum or "Addendum"
                affects = ch.affects if ch.affects in _FIELD_NAMES or ch.affects == "bid_items" else "other"
                model_changes.append(ChangeItem(
                    addendum=label, kind=_canon_kind(ch.kind), summary=ch.summary.strip(), affects=affects,
                    new_citations=[c for c in new_c if c.verified], old_citations=old_c,
                ))

    # Prefer the exact bid-form comparison over a model line about the same item.
    code_items = {re.search(r"Item (\S+)", c.summary).group(1).upper() for c in code_changes
                  if re.search(r"Item (\S+)", c.summary)}
    kept = []
    for c in model_changes:
        m = re.search(r"\bitem\s+([A-Z]?-?\d+[A-Z]?)\b", c.summary, re.I)
        if m and m.group(1).upper() in code_items and c.kind == "Bid items or quantities":
            continue
        kept.append(c)
    order = {a.label: i for i, a in enumerate(addenda)}
    result.changes = sorted(code_changes + kept, key=lambda c: (order.get(c.addendum, 99), CHANGE_KINDS.index(c.kind)))
    return result
