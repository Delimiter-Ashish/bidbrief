"""F4 Risks: flags in nine fixed categories. A flag is shown only if a
verified passage supports it. Nothing is invented to fill a category."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from .citations import PageStore, check_citations, claim_is_verified
from .index import SearchIndex, excerpt_pages, format_excerpts
from .llm import BaseLLM, LLMError
from .schemas import RISK_CATEGORIES, LLMRisks, RiskFlag, RisksResult

log = logging.getLogger(__name__)

CATEGORY_QUERIES: dict[str, list[str]] = {
    "Schedule": ["contract time completion calendar days substantial completion milestone",
                 "working hours limited weekends holidays", "seasonal restriction winter shutdown paving season"],
    "Liquidated damages": ["liquidated damages per day late completion", "damages for delay per calendar day"],
    "Payment": ["retainage retained percent of payment", "progress payments application for payment days"],
    "Bonds and insurance": ["insurance limits umbrella excess liability additional insured",
                            "performance bond payment bond percent", "builder's risk pollution liability insurance"],
    "Site conditions": ["rock excavation ledge boulders", "groundwater dewatering",
                        "contaminated soil hazardous materials disposal", "existing utilities test pits unknown utilities"],
    "Traffic": ["police details traffic control", "night work lane closure detour",
                "maintain traffic access for residents"],
    "Testing": ["pressure test leakage test", "disinfection bacteriological testing chlorination",
                "CCTV television inspection", "compaction testing density"],
    "Responsibility shifts": ["contractor responsible for all conditions no claim for extra",
                              "contractor shall verify quantities and existing conditions at own expense",
                              "indemnify hold harmless no damages for delay"],
    "Labor": ["prevailing wage rates", "apprentice local hiring workforce", "certified payroll OSHA 10 hour training"],
}

GROUPS = [RISK_CATEGORIES[0:3], RISK_CATEGORIES[3:6], RISK_CATEGORIES[6:9]]

SEVERITY_GUIDE = """Severity:
- High: could cost serious money or time on this bid if missed (for example a daily penalty for late finish, the contractor carrying the risk of unknown conditions, a very tight completion time).
- Medium: an unusual cost, effort or requirement to plan and price for.
- Low: worth knowing, normal for this kind of job."""

CATEGORY_HINTS = {
    "Schedule": "completion time, milestones, limits on work hours or season",
    "Liquidated damages": "daily penalty for late completion",
    "Payment": "retainage, payment terms",
    "Bonds and insurance": "unusual limits or extra coverage",
    "Site conditions": "rock, groundwater, contaminated soil, unknown utilities, dewatering",
    "Traffic": "police details, detours, night work, lane closure limits",
    "Testing": "pressure tests, disinfection, CCTV inspection, compaction tests",
    "Responsibility shifts": "the contractor bears the risk for conditions or design",
    "Labor": "prevailing wage, local hiring, apprentice rules",
}

_SEV_ORDER = {"High": 0, "Medium": 1, "Low": 2}


def _canon_category(name: str) -> str | None:
    low = (name or "").strip().lower()
    for c in RISK_CATEGORIES:
        if c.lower() == low:
            return c
    for c in RISK_CATEGORIES:
        if low and (low in c.lower() or c.lower() in low):
            return c
    return None


def _run_group(llm: BaseLLM, idx: SearchIndex, store: PageStore, cats: list[str]) -> tuple[list[RiskFlag], int, int, int]:
    chunks = []
    seen = set()
    for cat in cats:
        for ch in idx.multi_search(CATEGORY_QUERIES[cat], per_query=3, limit=8):
            if ch.chunk_id not in seen:
                seen.add(ch.chunk_id)
                chunks.append(ch)
    excerpts = format_excerpts(chunks, max_chars=45000)
    cand = excerpt_pages(chunks)
    cat_lines = "\n".join(f"- {c}: {CATEGORY_HINTS[c]}" for c in cats)
    prompt = (
        "Find bidding risks in this public construction bid package, using only the excerpts below.\n"
        f"Categories to check (use these exact names):\n{cat_lines}\n\n{SEVERITY_GUIDE}\n\n"
        "For each real risk return: category, reason (one plain-English line of at most 20 words saying what it is "
        "and why it matters to the bid), severity, and 1 or 2 citations (file, page, quote copied exactly).\n"
        "Only return a risk when an excerpt clearly supports it. If a category has nothing, return nothing for it. "
        "Do not repeat the same risk.\n\n"
        f"EXCERPTS:\n{excerpts}"
    )
    try:
        out = llm.generate(prompt, LLMRisks, purpose="risks")
    except LLMError as exc:
        log.error("Risks failed for %s: %s", cats, exc)
        return [], 0, 0, 0
    flags: list[RiskFlag] = []
    dropped = n_cit = n_ver = 0
    for r in out.risks:
        cat = _canon_category(r.category)
        cits = check_citations(store, r.citations, cand)
        n_cit += len(cits)
        n_ver += sum(c.verified for c in cits)
        if cat is None or not claim_is_verified(r.reason, cits):
            dropped += 1
            continue
        flags.append(RiskFlag(category=cat, reason=r.reason.strip(), severity=r.severity,
                              citations=[c for c in cits if c.verified]))
    return flags, dropped, n_cit, n_ver


def build_risks(llm: BaseLLM, idx: SearchIndex, store: PageStore) -> tuple[RisksResult, dict[str, int]]:
    flags: list[RiskFlag] = []
    dropped = n_cit = n_ver = 0
    with ThreadPoolExecutor(max_workers=3) as pool:
        for f, d, c, v in pool.map(lambda g: _run_group(llm, idx, store, g), GROUPS):
            flags.extend(f)
            dropped += d
            n_cit += c
            n_ver += v
    # de-duplicate: same category and same first page
    uniq: dict[tuple[str, str, int], RiskFlag] = {}
    for fl in flags:
        c0 = fl.citations[0]
        key = (fl.category, c0.file, c0.page)
        if key not in uniq or _SEV_ORDER[fl.severity] < _SEV_ORDER[uniq[key].severity]:
            uniq[key] = fl
    ordered = sorted(uniq.values(), key=lambda r: (_SEV_ORDER[r.severity], RISK_CATEGORIES.index(r.category)))
    return RisksResult(flags=ordered, dropped_unverified=dropped), {"model_citations": n_cit, "verified_citations": n_ver}
