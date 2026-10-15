"""BidBrief web app: a FastAPI backend plus a hand-built frontend (bidbrief/web).

    python -m bidbrief serve --port 8501

All URLs used by the frontend are relative, so the app works behind the SCC
OnDemand proxy and on Hugging Face Spaces without changes.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import threading
import time
import traceback
from functools import lru_cache
from pathlib import Path

import pymupdf
from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import trials
from .ask import EXAMPLE_QUESTIONS, ask
from .brief import field_status
from .citations import normalize_text, render_page
from .config import get_settings
from .llm import get_llm
from .pipeline import LoadedPackage, list_packages, load_package, process_package, result_path, slugify
from .schemas import AskResult, Citation, PackageResult
from .tables import items_to_csv, items_to_excel

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_PAGES = int(os.environ.get("BIDBRIEF_MAX_PAGES", "600"))
MAX_MB = int(os.environ.get("BIDBRIEF_MAX_MB", "200"))
CONTACT = os.environ.get("BIDBRIEF_CONTACT", "Ashish Joshi")

LABELS = {
    "project_name": "Project", "owner": "Owner", "location": "Where", "scope": "The work",
    "bid_due": "Bids due", "pre_bid_meeting": "Pre-bid meeting", "completion_time": "Time to finish",
    "bid_security": "Bid security", "bonds": "Bonds",
}
SEVERITY_TITLES = {
    "High": "Could cost real money if missed",
    "Medium": "Plan and price for it",
    "Low": "Good to know",
}

app = FastAPI(title="BidBrief", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


# ------------------------------------------------------------ helpers
def short_file(file: str, files: list[str]) -> str:
    """'' for the main file; a short name such as 'Addendum 1' for the others."""
    if not files or file == files[0]:
        return ""
    stem = Path(file).stem.replace("_", " ").replace("-", " ")
    m = re.search(r"addend\w*\s*(no\.?\s*)?(\d+)", stem, re.I)
    if m:
        return f"Addendum {m.group(2)}"
    words = [w for w in stem.split() if not w.isdigit()]
    return " ".join(words[:3])[:24] or stem[:24]


def cite_view(c: Citation, files: list[str]) -> dict:
    return {"file": c.file, "short": short_file(c.file, files), "page": c.page, "quote": c.quote,
            "verified": c.verified}


_PKG_CACHE: dict[str, tuple[float, LoadedPackage]] = {}
_PKG_LOCK = threading.Lock()


def _mtime(pid: str) -> float:
    p = result_path(pid)
    return p.stat().st_mtime if p.exists() else 0.0


def get_pkg(pid: str, code: str | None) -> LoadedPackage:
    if not re.fullmatch(r"[a-z0-9-]{1,80}", pid or ""):
        raise HTTPException(404, "Package not found.")
    if not result_path(pid).exists() or not trials.can_view(pid, code):
        raise HTTPException(404, "Package not found.")
    m = _mtime(pid)
    with _PKG_LOCK:
        hit = _PKG_CACHE.get(pid)
        if hit and hit[0] == m:
            return hit[1]
        pkg = load_package(pid)
        _PKG_CACHE[pid] = (m, pkg)
        return pkg


def package_summary(r: PackageResult, code: str | None) -> dict:
    owner = r.brief.get("owner")
    return {
        "id": r.package_id, "title": r.title, "synthetic": r.synthetic, "pages": r.page_count,
        "files": len(r.files), "items": len(r.bid_items.items), "risks": len(r.risks.flags),
        "high": sum(1 for f in r.risks.flags if f.severity == "High"),
        "owner": owner.value if owner and owner.value and field_status(owner) == "found" else "",
        "mine": trials.is_mine(r.package_id, code), "created": r.created_at,
        "addenda": len(r.changes.addenda), "changes": len(r.changes.changes),
    }


def package_view(pkg: LoadedPackage, code: str | None) -> dict:
    r = pkg.result
    files = r.files
    brief = []
    for f in r.brief.fields:
        brief.append({
            "name": f.name, "label": LABELS.get(f.name, f.name), "status": field_status(f),
            "value": f.value, "citations": [cite_view(c, files) for c in f.citations],
        })
    read_first = [{"reason": it.reason, "citations": [cite_view(c, files) for c in it.citations]}
                  for it in r.brief.read_first]
    items = [{
        "item": it.item_no, "description": it.description, "quantity": it.quantity, "unit": it.unit,
        "file": it.file, "short": short_file(it.file, files), "page": it.page,
        "check": it.confidence < 0.8, "why": it.note,
    } for it in r.bid_items.items]
    groups = []
    for sev in ("High", "Medium", "Low"):
        flags = [f for f in r.risks.flags if f.severity == sev]
        if flags:
            groups.append({"severity": sev, "title": SEVERITY_TITLES[sev], "flags": [
                {"category": f.category, "reason": f.reason, "citations": [cite_view(c, files) for c in f.citations]}
                for f in flags]})
    # ---- addenda and changes
    ch = r.changes
    changes = [{
        "addendum": c.addendum, "kind": c.kind, "summary": c.summary, "affects": c.affects, "source": c.source,
        "new": [cite_view(x, files) for x in c.new_citations], "old": [cite_view(x, files) for x in c.old_citations],
    } for c in ch.changes]
    changed_fields: dict[str, list[str]] = {}
    for c in ch.changes:
        if c.affects in LABELS:
            changed_fields.setdefault(c.affects, [])
            if c.addendum not in changed_fields[c.affects]:
                changed_fields[c.affects].append(c.addendum)
    for b in brief:
        b["changed_by"] = changed_fields.get(b["name"], [])

    # ---- reading stats
    real_pages = [p for p in r.pages if p.page > 0]
    stats = {"pages": len(real_pages), "files": len(files),
             "unreadable": sum(1 for p in real_pages if p.problem),
             "ocr": sum(1 for p in real_pages if p.is_ocr)}

    # ---- what a person should check before relying on it
    review: list[dict] = []
    for p in real_pages:
        if p.problem:
            review.append({"key": f"page:{p.file}:{p.page}", "level": "high",
                           "title": f"Page {p.page}{' of ' + short_file(p.file, files) if short_file(p.file, files) else ''} could not be read",
                           "detail": "Nothing on this page is used. Read it yourself.",
                           "action": {"type": "page", "file": p.file, "short": short_file(p.file, files), "page": p.page}})
    for b in brief:
        if b["status"] == "unverified":
            c = b["citations"][0] if b["citations"] else None
            review.append({"key": f"field:{b['name']}", "level": "high",
                           "title": f"{b['label']}: could not be confirmed on the page",
                           "detail": "The answer is shown in grey. Check the page before using it.",
                           "action": {"type": "page", **c} if c else None})
    for name, adds in changed_fields.items():
        review.append({"key": f"changed:{name}", "level": "high",
                       "title": f"{LABELS[name]} is changed by {', '.join(adds)}",
                       "detail": "Use the addendum's version.", "action": {"type": "tab", "tab": "changes"}})
    n_check = sum(1 for i in items if i["check"])
    if n_check:
        review.append({"key": "items:check", "level": "medium",
                       "title": f"{n_check} bid item row{'s' if n_check != 1 else ''} need checking",
                       "detail": "The table could not read these rows with full confidence.",
                       "action": {"type": "tab", "tab": "items", "filter": "check"}})
    if r.bid_items.numbering_gaps:
        f0, p0 = r.bid_items.bid_form_pages[0] if r.bid_items.bid_form_pages else (files[0], 1)
        review.append({"key": "items:gaps", "level": "medium",
                       "title": f"Item number{'s' if len(r.bid_items.numbering_gaps) != 1 else ''} "
                                f"{', '.join(r.bid_items.numbering_gaps)} not found in the bid form",
                       "detail": "A row may be missing, or the form may skip numbers on purpose.",
                       "action": {"type": "page", "file": f0, "short": short_file(f0, files), "page": p0}})
    if r.bid_items.duplicates:
        review.append({"key": "items:dups", "level": "medium",
                       "title": f"Item number{'s' if len(r.bid_items.duplicates) != 1 else ''} "
                                f"{', '.join(r.bid_items.duplicates)} appear{'s' if len(r.bid_items.duplicates) == 1 else ''} twice",
                       "detail": "Check which row is right.", "action": {"type": "tab", "tab": "items", "filter": "check"}})
    if r.model != "none":
        missing = [b["label"] for b in brief if b["status"] == "missing"]
        if missing:
            review.append({"key": "fields:missing", "level": "low",
                           "title": f"Not found in the documents: {', '.join(missing)}",
                           "detail": "BidBrief does not guess. If it matters, look for it yourself.", "action": None})
    if r.risks.dropped_unverified:
        review.append({"key": "risks:dropped", "level": "low",
                       "title": f"{r.risks.dropped_unverified} possible risk{'s were' if r.risks.dropped_unverified != 1 else ' was'} left out",
                       "detail": "Their supporting passage could not be confirmed on the page.", "action": None})
    if ch.note:
        review.append({"key": "changes:note", "level": "high", "title": "Some addendum pages could not be read",
                       "detail": ch.note, "action": {"type": "tab", "tab": "changes"}})

    return {
        "id": r.package_id, "title": r.title, "synthetic": r.synthetic, "files": files,
        "stats": stats, "review": review,
        "changes": {"addenda": [{"label": a.label, "file": a.file, "short": short_file(a.file, files), "pages": a.pages,
                                 "unreadable": a.unreadable_pages} for a in ch.addenda],
                    "items": changes, "note": ch.note, "dropped": ch.dropped_unverified},
        "pages": r.page_count, "model_on": r.model != "none", "note": r.brief.note,
        "mine": trials.is_mine(r.package_id, code), "timings": r.timings,
        "brief": brief, "read_first": read_first,
        "items": {"rows": items, "pages": sorted({p for _, p in r.bid_items.bid_form_pages}),
                  "gaps": r.bid_items.numbering_gaps, "duplicates": r.bid_items.duplicates,
                  "check": sum(1 for i in items if i["check"])},
        "risks": {"groups": groups, "count": len(r.risks.flags), "dropped": r.risks.dropped_unverified},
        "manual": [{"file": p.file, "short": short_file(p.file, files), "page": p.page, "problem": p.problem}
                   for p in r.pages if p.problem],
        "examples": EXAMPLE_QUESTIONS,
    }


def ask_view(res: AskResult, files: list[str]) -> dict:
    return {
        "question": res.question, "status": res.status, "text": res.answer.text,
        "citations": [cite_view(c, files) for c in res.answer.citations],
        "checked": [{"file": f, "short": short_file(f, files), "page": p} for f, p in res.checked_pages],
    }


# ------------------------------------------------------------ jobs
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()  # one package at a time keeps the machine responsive


def _set_job(job_id: str, **kw) -> None:
    with _JOBS_LOCK:
        _JOBS[job_id].update(kw)


def _run_job(job_id: str, pdir: Path, pid: str, code: str | None) -> None:
    _set_job(job_id, state="queued", message="Waiting for the previous package to finish", fraction=0.0)
    with _RUN_LOCK:
        _set_job(job_id, state="running", message="Reading pages", fraction=0.02, started=time.time())
        try:
            process_package(pdir, pid, progress=lambda m, f: _set_job(job_id, message=m, fraction=round(f, 3)))
            _set_job(job_id, state="done", message="Done", fraction=1.0, finished=time.time())
        except Exception as exc:  # report, refund, clean up
            log.error("Processing failed: %s\n%s", exc, traceback.format_exc())
            if trials.trial_required():
                trials.refund(code)
            trials.delete_package(pid)
            _set_job(job_id, state="error", message="This package could not be read.",
                     error=str(exc)[:300], fraction=1.0)


# ------------------------------------------------------------ routes
@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/config")
def api_config() -> dict:
    return {"trial_required": trials.trial_required(), "contact": CONTACT, "model_on": get_llm() is not None,
            "max_pages": MAX_PAGES, "max_mb": MAX_MB}


class TrialIn(BaseModel):
    code: str


@app.post("/api/trial")
def api_trial(body: TrialIn) -> dict:
    if not trials.trial_required():
        return {"ok": True, "remaining": None}
    if not trials.is_valid(body.code):
        return {"ok": False, "remaining": 0, "message": "That access code isn't recognised. Check it and try again."}
    return {"ok": True, "remaining": trials.remaining(body.code), "code": trials.normalize_code(body.code)}


@app.get("/api/packages")
def api_packages(x_trial_code: str | None = Header(default=None)) -> list[dict]:
    out = [package_summary(r, x_trial_code) for r in list_packages() if trials.can_view(r.package_id, x_trial_code)]
    out.sort(key=lambda p: (not p["mine"], p["synthetic"], p["title"].lower()))
    return out


@app.get("/api/packages/{pid}")
def api_package(pid: str, x_trial_code: str | None = Header(default=None)) -> dict:
    return package_view(get_pkg(pid, x_trial_code), x_trial_code)


@lru_cache(maxsize=128)
def _page_png(pid: str, mtime: float, file: str, page: int, quotes: tuple[str, ...], close: bool) -> tuple[bytes, int]:
    pkg = load_package(pid) if pid not in _PKG_CACHE else _PKG_CACHE[pid][1]
    return render_page(pkg.store, file, page, quotes, zoom=2.4 if close else 1.7, close_up=close)


@app.get("/api/packages/{pid}/page")
def api_page(pid: str, file: str, page: int, close: int = 0, q: list[str] = Query(default=[]),
             x_trial_code: str | None = Header(default=None), code: str | None = None) -> Response:
    pkg = get_pkg(pid, x_trial_code or code)
    if file not in pkg.result.files:
        raise HTTPException(404, "File not in this package.")
    try:
        n_pages = pkg.store.page_count(file)
    except Exception:
        raise HTTPException(404, "Page not found.")
    if page < 1 or page > n_pages:
        raise HTTPException(404, "Page not found.")
    quotes = tuple(x[:600] for x in q[:4] if x)
    png, n = _page_png(pid, _mtime(pid), file, page, quotes, bool(close))
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=3600", "X-Highlights": str(n)})


@app.get("/api/packages/{pid}/items.xlsx")
def api_items_xlsx(pid: str, x_trial_code: str | None = Header(default=None), code: str | None = None) -> Response:
    pkg = get_pkg(pid, x_trial_code or code)
    data = items_to_excel(pkg.result.bid_items.items, f"{pkg.result.title} - Bid Items")
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{pid}-bid-items.xlsx"'})


@app.get("/api/packages/{pid}/items.csv")
def api_items_csv(pid: str, x_trial_code: str | None = Header(default=None), code: str | None = None) -> Response:
    pkg = get_pkg(pid, x_trial_code or code)
    return Response(items_to_csv(pkg.result.bid_items.items), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{pid}-bid-items.csv"'})


class AskIn(BaseModel):
    question: str


@app.post("/api/packages/{pid}/ask")
def api_ask(pid: str, body: AskIn, x_trial_code: str | None = Header(default=None)) -> dict:
    pkg = get_pkg(pid, x_trial_code)
    q = (body.question or "").strip()[:500]
    if not q:
        raise HTTPException(400, "Type a question first.")
    ready = {normalize_text(r.question): r for r in pkg.result.example_answers if r.status != "no_model"}
    res = ready.get(normalize_text(q))
    if res is None:
        res = ask(q, pkg.index, pkg.store)
    return ask_view(res, pkg.result.files)


@app.delete("/api/packages/{pid}")
def api_delete(pid: str, x_trial_code: str | None = Header(default=None)) -> dict:
    get_pkg(pid, x_trial_code)
    if not trials.is_mine(pid, x_trial_code):
        raise HTTPException(403, "Only the person who added this package can delete it.")
    trials.delete_package(pid)
    with _PKG_LOCK:
        _PKG_CACHE.pop(pid, None)
    return {"ok": True}


@app.post("/api/upload")
async def api_upload(files: list[UploadFile] = File(...), name: str = Form(""),
                     x_trial_code: str | None = Header(default=None)) -> JSONResponse:
    code = trials.normalize_code(x_trial_code) or None
    if trials.trial_required():
        if not trials.is_valid(code):
            raise HTTPException(403, "Enter your access code to add a package.")
        if trials.remaining(code) <= 0:
            raise HTTPException(403, f"This access code has no packages left. Contact {CONTACT} to continue.")
    pdfs = [f for f in files if (f.filename or "").lower().endswith(".pdf")]
    if not pdfs:
        raise HTTPException(400, "Add at least one PDF file.")

    s = get_settings()
    base = slugify(name or Path(pdfs[0].filename or "package").stem)[:40]
    pid = f"{base}-{secrets.token_hex(3)}"
    pdir = s.packages_dir / pid
    pdir.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        for f in pdfs:
            safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", Path(f.filename or "file.pdf").name)[:120] or "file.pdf"
            dest = pdir / safe
            with open(dest, "wb") as out:
                while True:
                    chunk = await f.read(1 << 20)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_MB * (1 << 20):
                        raise HTTPException(400, f"The files are larger than {MAX_MB} MB in total.")
                    out.write(chunk)
        pages = 0
        for p in pdir.glob("*.pdf"):
            try:
                with pymupdf.open(p) as d:
                    pages += len(d)
            except Exception:
                raise HTTPException(400, f"{p.name} is not a readable PDF.")
        if pages > MAX_PAGES:
            raise HTTPException(400, f"This package has {pages} pages. The limit is {MAX_PAGES}.")
        if pages == 0:
            raise HTTPException(400, "The PDF has no pages.")
    except HTTPException:
        trials.delete_package(pid)
        raise

    if trials.trial_required() and not trials.consume(code):
        trials.delete_package(pid)
        raise HTTPException(403, f"This access code has no packages left. Contact {CONTACT} to continue.")
    if name.strip():
        (pdir / "package.json").write_text(json.dumps({"title": name.strip()[:120]}))
    trials.set_owner(pid, code if trials.trial_required() else None)

    job_id = secrets.token_hex(8)
    with _JOBS_LOCK:
        _JOBS[job_id] = {"state": "queued", "message": "Starting", "fraction": 0.0, "package_id": pid,
                         "pages": pages, "created": time.time()}
    threading.Thread(target=_run_job, args=(job_id, pdir, pid, code), daemon=True).start()
    return JSONResponse({"job_id": job_id, "package_id": pid, "pages": pages,
                         "remaining": trials.remaining(code) if trials.trial_required() else None})


@app.get("/api/jobs/{job_id}")
def api_job(job_id: str) -> dict:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise HTTPException(404, "Job not found.")
        out = dict(job)
    if out.get("started"):
        out["elapsed"] = round((out.get("finished") or time.time()) - out["started"], 1)
    return out


def serve(host: str = "0.0.0.0", port: int = 8501) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info", proxy_headers=True, forwarded_allow_ips="*")
