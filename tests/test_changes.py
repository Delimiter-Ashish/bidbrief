"""Addenda: detection, model-listed changes, and exact bid-form comparisons."""
from fake_llm import FakeLLM

from bidbrief.changes import addendum_page_set, bid_item_changes, find_addenda, find_changes
from bidbrief.citations import PageStore
from bidbrief.index import SearchIndex
from bidbrief.schemas import AddendumInfo, BidItem


def test_addenda_are_found_with_their_numbers(ingested):
    adds = find_addenda(ingested)
    labels = [a.label for a in adds]
    assert labels[:2] == ["Addendum No. 1", "Addendum No. 2"]
    assert adds[0].file == "02_Addendum_1.pdf" and adds[0].pages == [1]
    main = ingested.files[0]
    assert not any(f == main for f, _ in addendum_page_set(adds))


def test_changes_are_listed_and_cited(ingested):
    store = PageStore(ingested.working)
    idx = SearchIndex(ingested.chunks, build_embeddings=False)
    res = find_changes(FakeLLM(), ingested, idx, store, [])
    kinds = {c.kind for c in res.changes}
    assert "Bid date or time" in kinds
    date = next(c for c in res.changes if c.kind == "Bid date or time")
    assert date.addendum == "Addendum No. 1" and date.affects == "bid_due"
    assert all(c.verified for c in date.new_citations)
    for c in res.changes:
        assert c.new_citations and all(x.file != ingested.files[0] for x in c.new_citations)
    store.close()


def test_no_model_still_finds_addenda(ingested):
    store = PageStore(ingested.working)
    idx = SearchIndex(ingested.chunks, build_embeddings=False)
    res = find_changes(None, ingested, idx, store, [])
    assert len(res.addenda) >= 2 and res.changes == []
    store.close()


def test_revised_bid_row_becomes_an_exact_change(ingested):
    store = PageStore(ingested.working)
    main, add = ingested.files[0], "02_Addendum_1.pdf"
    items = [
        BidItem(item_no="7", description="12-inch Gate Valve and Box", quantity="9", unit="EA", page=6, confidence=1, file=main),
        BidItem(item_no="7", description="12-inch Gate Valve and Box", quantity="11", unit="EA", page=1, confidence=1, file=add),
    ]
    adds = [AddendumInfo(label="Addendum No. 1", file=add, pages=[1])]
    [c] = bid_item_changes(items, adds, store)
    assert c.source == "bid_form" and "from 9 EA to 11 EA" in c.summary and c.affects == "bid_items"
    assert c.old_citations[0].verified  # the original row is on page 6
    store.close()
