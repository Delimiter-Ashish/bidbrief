"""F5 Ask: a cited answer of 1-4 sentences, or a plain "not in the documents"
with the closest pages that were checked."""
from __future__ import annotations

import logging

from .citations import PageStore, check_citations, claim_is_verified
from .index import SearchIndex, excerpt_pages, format_excerpts
from .llm import BaseLLM, LLMError, get_llm
from .schemas import Answer, AskResult, LLMAnswer

log = logging.getLogger(__name__)

EXAMPLE_QUESTIONS = [
    "What are the liquidated damages?",
    "What testing is required for the new water main?",
    "Are there night work restrictions?",
]

NOT_FOUND_TEXT = "The documents don't answer this."


def ask(question: str, idx: SearchIndex, store: PageStore, llm: BaseLLM | None = None, k: int = 10) -> AskResult:
    question = (question or "").strip()
    if not question:
        raise ValueError("Empty question")
    hits = idx.search(question, k=k)
    chunks = [h.chunk for h in hits]
    checked = excerpt_pages(chunks)[:6]
    llm = llm if llm is not None else get_llm()
    if llm is None:
        return AskResult(question=question, status="no_model", checked_pages=checked,
                         answer=Answer(text="Answers need a model key. These are the pages most likely to have it.",
                                       found=False, citations=[]))

    prompt = (
        f"QUESTION: {question}\n\n"
        "Answer the question using only the excerpts below, in 1 to 4 plain sentences, with citations "
        "(file, page, quote copied exactly from that excerpt) for every fact.\n"
        "If two excerpts disagree, give both, each with its citation, and say plainly that they conflict.\n"
        f"If the excerpts do not answer the question, return found: false and text: \"{NOT_FOUND_TEXT}\" "
        "with no citations.\n\n"
        f"EXCERPTS:\n{format_excerpts(chunks, max_chars=40000)}"
    )
    try:
        out = llm.generate(prompt, LLMAnswer, purpose="ask")
    except LLMError as exc:
        log.error("Ask failed: %s", exc)
        return AskResult(question=question, status="no_model", checked_pages=checked,
                         answer=Answer(text=f"Could not get an answer right now: {exc}", found=False, citations=[]))

    cits = check_citations(store, out.citations, excerpt_pages(chunks))
    if not out.found:
        return AskResult(question=question, status="not_found", checked_pages=checked, raw_citations=len(cits),
                         answer=Answer(text=NOT_FOUND_TEXT, found=False, citations=[]))
    verified = claim_is_verified(out.text, cits)
    return AskResult(
        question=question, status="verified" if verified else "unverified", checked_pages=checked,
        raw_citations=len(cits),
        answer=Answer(text=out.text.strip(), found=True, citations=cits),
    )
