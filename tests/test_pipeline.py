from fake_llm import FakeLLM

from bidbrief.ask import NOT_FOUND_TEXT, ask
from bidbrief.brief import field_status
from bidbrief.citations import PageStore
from bidbrief.index import SearchIndex
from bidbrief.pipeline import list_packages, load_package
from bidbrief.risks import build_risks


def test_full_run_produces_cited_outputs(processed):
    res, fake = processed
    assert len(res.bid_items.items) == 26
    statuses = {f.name: field_status(f) for f in res.brief.fields}
    assert statuses["completion_time"] == "found"
    assert statuses["bonds"] == "found"
    for f in res.brief.fields:
        for c in f.citations:
            if field_status(f) == "found":
                assert c.page >= 1
    assert res.risks.flags, "risks expected"
    for fl in res.risks.flags:
        assert fl.citations and all(c.verified for c in fl.citations)
    assert 1 <= len(res.brief.read_first) <= 5
    assert res.brief.read_first[0].citations[0].page == 6  # bid form first
    assert any(p.package_id == res.package_id for p in list_packages())


def test_ask_answers_with_citation_and_says_not_found(processed):
    res, fake = processed
    pkg = load_package(res.package_id)
    r = ask("What are the liquidated damages?", pkg.index, pkg.store, llm=fake)
    assert r.status == "verified" and r.answer.found
    assert any(c.page == 12 and c.verified for c in r.answer.citations)
    r2 = ask("Is there an early completion bonus for the contractor?", pkg.index, pkg.store, llm=fake)
    assert r2.status == "not_found" and r2.answer.text == NOT_FOUND_TEXT and r2.checked_pages


def test_invented_quotes_never_reach_the_user(processed, ingested):
    bad = FakeLLM(hallucinate=True)
    idx = SearchIndex(ingested.chunks, build_embeddings=False)
    store = PageStore(ingested.working)
    risks, _ = build_risks(bad, idx, store)
    assert risks.dropped_unverified >= 1
    assert all("90 days" not in f.reason for f in risks.flags)
    r = ask("What are the liquidated damages?", idx, store, llm=bad)
    assert r.status == "unverified"
    assert not any(c.verified for c in r.answer.citations)
    store.close()


def test_eval_runs_end_to_end(processed, data_dir):
    from bidbrief import eval as ev
    from bidbrief.llm import set_llm

    res, fake = processed
    set_llm(fake)
    out = ev.run([res.package_id], with_ask=True, label="test")
    m = out["packages"][res.package_id + " (synthetic)"]
    assert m["Bid items: rows matched exactly"]["pct"] == 100.0
    assert m["Ask: unanswerable marked 'not found'"]["den"] == 5
    assert (data_dir / "eval_runs").exists()
