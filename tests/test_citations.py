from bidbrief.citations import (
    check_citations, claim_is_verified, match_quote, normalize_text, quote_rects, render_page, unsupported_numbers,
)
from bidbrief.schemas import Citation, LLMCitation

LD = "liquidated damages of One Thousand Five Hundred Dollars ($1,500.00) per calendar day"


def test_exact_quote_on_page_is_verified(ingested, store):
    f = ingested.files[0]
    [c] = check_citations(store, [LLMCitation(file=f, page=12, quote=LD)])
    assert c.verified and c.page == 12


def test_small_differences_still_match(ingested, store):
    f = ingested.files[0]
    q = "liquidated damages of One Thousand Five Hundred Dollars ($1,500) per calendar day"  # ".00" dropped
    [c] = check_citations(store, [LLMCitation(file=f, page=12, quote=q)])
    assert c.verified


def test_wrong_page_is_corrected_by_code(ingested, store):
    f = ingested.files[0]
    [c] = check_citations(store, [LLMCitation(file=f, page=3, quote=LD)], candidate_pages=[(f, 3), (f, 12)])
    assert c.verified and c.page == 12


def test_invented_quote_is_rejected(ingested, store):
    f = ingested.files[0]
    [c] = check_citations(store, [LLMCitation(file=f, page=12, quote="liquidated damages of Five Thousand Dollars per hour")],
                          candidate_pages=[(f, p) for p in range(1, 23)])
    assert not c.verified


def test_short_quotes_must_match_exactly():
    page = normalize_text("Retainage shall be 5% of each payment.")
    assert match_quote("5% of each", page).ok
    assert not match_quote("6% of each", page).ok


def test_numbers_guard_blocks_computed_numbers():
    quotes = ["pay the Owner liquidated damages of ($1,500.00) per calendar day"]
    assert unsupported_numbers("$1,500 per day", quotes) == []
    assert unsupported_numbers("$3,000 for two days", quotes) == ["3000"]
    good = [Citation(file="a", page=1, quote=quotes[0], verified=True)]
    assert claim_is_verified("$1,500 per day", good)
    assert not claim_is_verified("$10,500 per week", good)
    assert not claim_is_verified("$1,500 per day", [Citation(file="a", page=1, quote=quotes[0], verified=False)])


def test_highlight_boxes_and_render(ingested, store):
    f = ingested.files[0]
    rects = quote_rects(store, f, 12, LD)
    assert 1 <= len(rects) <= 3
    png, n = render_page(store, f, 12, [LD])
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and n == len(rects)


def test_model_cannot_set_verified():
    assert "verified" not in LLMCitation.model_fields
