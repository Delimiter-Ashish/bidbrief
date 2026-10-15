import shutil

import pytest

from bidbrief.ingest import chunk_page, detect_section, top_section


def test_every_page_is_read_with_its_number(ingested):
    main = [p for p in ingested.pages if p.file.startswith("01_")]
    assert [p.page for p in main] == list(range(1, len(main) + 1))
    assert len(main) == 22
    assert ingested.page_count == 24


def test_sections_are_detected(ingested):
    sec = {(p.file, p.page): top_section(p.section) for p in ingested.pages}
    f = ingested.files[0]
    assert sec[(f, 3)] == "Invitation for Bids"
    assert sec[(f, 6)] == "Bid Form"
    assert sec[(f, 7)] == "Bid Form"  # continuation page carries the section forward
    assert sec[(f, 12)] == "Supplementary Conditions"
    assert sec[(f, 2)] == "Table of Contents"
    full = {(p.file, p.page): p.section for p in ingested.pages}
    assert full[(f, 18)].startswith("Specifications — Section 33 11 00")


def test_chunks_keep_file_page_section_and_offsets(ingested):
    assert ingested.chunks
    for c in ingested.chunks:
        page_text = ingested.page_text[(c.file, c.page)]
        assert page_text[c.char_start:c.char_end] == c.text
        assert c.page >= 1 and c.file in ingested.files


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_scanned_addendum_is_ocrd_and_marked(ingested):
    add = [p for p in ingested.pages if p.file.startswith("03_")]
    assert len(add) == 1 and add[0].is_ocr and add[0].problem is None
    assert "saturday work" in ingested.page_text[(add[0].file, 1)].lower()
    assert all(c.is_ocr for c in ingested.chunks if c.file == add[0].file)


def test_chunking_overlaps_and_covers_text():
    text = "\n".join(f"Line {i} " + "word " * 12 for i in range(200))
    spans = chunk_page(text, size=500, overlap=100)
    assert spans[0][0] == 0 and spans[-1][1] == len(text)
    for (a1, b1), (a2, b2) in zip(spans, spans[1:]):
        assert a2 < b1 and a2 > a1  # overlap and progress


def test_detect_section_samples():
    assert detect_section("SECTION 31 23 00 - EXCAVATION AND FILL\nPart 1") .startswith("Specifications")
    assert detect_section("INSTRUCTIONS TO BIDDERS\n1. General") == "Instructions to Bidders"
    assert detect_section("The contractor shall comply with the general conditions of the contract and all.") is None
