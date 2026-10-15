"""Web app API: packages, pages, downloads, ask, trial codes and privacy."""
import time

import pytest
from fastapi.testclient import TestClient



@pytest.fixture(scope="module")
def client(processed, data_dir):
    from bidbrief.server import app

    return TestClient(app)


def _sample_pdf(data_dir):
    return data_dir / "packages" / "sample-mapleford" / "01_Contract_Documents.pdf"


def test_index_and_static(client):
    r = client.get("/")
    assert r.status_code == 200 and 'src="static/app.js"' in r.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/styles.css").status_code == 200
    assert client.get("/static/fonts/archivo-latin-standard-normal.woff2").status_code == 200


def test_open_mode_lists_and_views_package(client, monkeypatch):
    monkeypatch.delenv("BIDBRIEF_TRIAL_CODES", raising=False)
    assert client.get("/api/config").json()["trial_required"] is False
    pkgs = client.get("/api/packages").json()
    assert any(p["id"] == "sample-mapleford" for p in pkgs)
    v = client.get("/api/packages/sample-mapleford").json()
    assert len(v["items"]["rows"]) == 26
    assert {f["name"] for f in v["brief"]} >= {"bid_due", "completion_time", "bonds"}
    assert v["risks"]["count"] >= 1
    assert v["stats"]["pages"] == 24 and v["stats"]["files"] == 3
    assert [a["label"] for a in v["changes"]["addenda"]][:2] == ["Addendum No. 1", "Addendum No. 2"]
    assert any(c["affects"] == "bid_due" for c in v["changes"]["items"])
    bid_due = next(f for f in v["brief"] if f["name"] == "bid_due")
    assert "Addendum No. 1" in bid_due["changed_by"]
    assert any(r["key"] == "changed:bid_due" for r in v["review"])


def test_page_image_and_downloads(client):
    r = client.get("/api/packages/sample-mapleford/page", params={
        "file": "01_Contract_Documents.pdf", "page": 12,
        "q": "liquidated damages of One Thousand Five Hundred Dollars ($1,500.00) per calendar day"})
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG" and int(r.headers["X-Highlights"]) >= 1
    close = client.get("/api/packages/sample-mapleford/page", params={
        "file": "01_Contract_Documents.pdf", "page": 12, "close": 1,
        "q": "liquidated damages of One Thousand Five Hundred Dollars ($1,500.00) per calendar day"})
    assert close.status_code == 200 and len(close.content) < len(r.content) * 2
    assert client.get("/api/packages/sample-mapleford/page", params={"file": "nope.pdf", "page": 1}).status_code == 404
    assert client.get("/api/packages/sample-mapleford/page", params={"file": "01_Contract_Documents.pdf", "page": 999}).status_code == 404
    x = client.get("/api/packages/sample-mapleford/items.xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert "3,120" in client.get("/api/packages/sample-mapleford/items.csv").text
    assert client.get("/api/packages/..%2F..%2Fetc").status_code == 404


def test_ask_endpoint(client, processed):
    from bidbrief.llm import set_llm

    _, fake = processed
    set_llm(fake)
    r = client.post("/api/packages/sample-mapleford/ask", json={"question": "What are the liquidated damages?"}).json()
    assert r["status"] == "verified" and any(c["page"] == 12 for c in r["citations"])
    nf = client.post("/api/packages/sample-mapleford/ask", json={"question": "Is there an early completion bonus for the contractor?"}).json()
    assert nf["status"] == "not_found" and nf["checked"]
    assert client.post("/api/packages/sample-mapleford/ask", json={"question": "  "}).status_code == 400


def _wait(client, job_id, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["state"] in {"done", "error"}:
            return j
        time.sleep(0.3)
    raise AssertionError("job did not finish")


def test_trial_codes_limit_and_privacy(client, data_dir, monkeypatch, processed):
    from bidbrief.llm import set_llm

    set_llm(processed[1])
    monkeypatch.setenv("BIDBRIEF_TRIAL_CODES", "SEAN-AAAA:1,OTHER-BBBB:2")
    pdf = _sample_pdf(data_dir).read_bytes()
    files = {"files": ("bid.pdf", pdf, "application/pdf")}

    assert client.get("/api/config").json()["trial_required"] is True
    assert client.post("/api/trial", json={"code": "nope"}).json()["ok"] is False
    assert client.post("/api/trial", json={"code": "sean-aaaa"}).json()["remaining"] == 1

    # no code -> refused
    assert client.post("/api/upload", files=files).status_code == 403
    # with code -> processed, private to that code
    r = client.post("/api/upload", files=files, data={"name": "Private job"}, headers={"X-Trial-Code": "sean-aaaa"})
    assert r.status_code == 200, r.text
    job = _wait(client, r.json()["job_id"])
    assert job["state"] == "done", job
    pid = job["package_id"]
    mine = client.get("/api/packages", headers={"X-Trial-Code": "SEAN-AAAA"}).json()
    assert any(p["id"] == pid and p["mine"] for p in mine)
    assert not any(p["id"] == pid for p in client.get("/api/packages").json())
    assert not any(p["id"] == pid for p in client.get("/api/packages", headers={"X-Trial-Code": "OTHER-BBBB"}).json())
    assert client.get(f"/api/packages/{pid}").status_code == 404
    assert client.get(f"/api/packages/{pid}", headers={"X-Trial-Code": "SEAN-AAAA"}).json()["title"] == "Private job"
    # used up
    assert client.post("/api/trial", json={"code": "SEAN-AAAA"}).json()["remaining"] == 0
    again = client.post("/api/upload", files=files, headers={"X-Trial-Code": "SEAN-AAAA"})
    assert again.status_code == 403 and "no packages left" in again.json()["detail"]
    # only the owner can delete
    assert client.delete(f"/api/packages/{pid}", headers={"X-Trial-Code": "OTHER-BBBB"}).status_code == 404
    assert client.delete(f"/api/packages/{pid}", headers={"X-Trial-Code": "SEAN-AAAA"}).json()["ok"]
    assert not (data_dir / "packages" / pid).exists()


def test_bad_upload_does_not_use_a_trial(client, monkeypatch):
    monkeypatch.setenv("BIDBRIEF_TRIAL_CODES", "TEST-CCCC:1")
    bad = {"files": ("bid.pdf", b"not a pdf at all", "application/pdf")}
    r = client.post("/api/upload", files=bad, headers={"X-Trial-Code": "TEST-CCCC"})
    assert r.status_code == 400 and "not a readable PDF" in r.json()["detail"]
    assert client.post("/api/trial", json={"code": "TEST-CCCC"}).json()["remaining"] == 1
    none = client.post("/api/upload", files={"files": ("notes.txt", b"hello", "text/plain")}, headers={"X-Trial-Code": "TEST-CCCC"})
    assert none.status_code == 400
