"""Command line.

    python -m bidbrief doctor [--download] [--ping]   check the setup (and pre-download search models)
    python -m bidbrief sample                          build the synthetic test package + answer key
    python -m bidbrief process data/packages/<folder>  process one package (saves results for the app)
    python -m bidbrief process-all                     process every folder in data/packages
    python -m bidbrief list                            list processed packages
    python -m bidbrief ask <package_id> "question"     ask a question from the terminal
    python -m bidbrief serve [--port 8501]             start the web app
    python -m bidbrief new-code --prefix SEAN --uses 3 make a trial access code
    python -m bidbrief trials                          show trial code usage
    python -m bidbrief.eval                            run the evaluation
"""
from __future__ import annotations

import argparse
import logging
import platform
import sys
from pathlib import Path


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")


def cmd_doctor(args) -> int:
    from .config import get_settings
    from .ingest import ocrmypdf_available, tesseract_available

    s = get_settings()
    ok = True

    def line(name: str, good: bool, detail: str = "", required: bool = True) -> None:
        nonlocal ok
        mark = "OK  " if good else ("FAIL" if required else "WARN")
        if not good and required:
            ok = False
        print(f"[{mark}] {name}{(': ' + detail) if detail else ''}")

    v = sys.version_info
    line("Python", v >= (3, 10), platform.python_version() + ("" if v >= (3, 10) else " (need 3.10+)"))
    for mod in ["pymupdf", "pdfplumber", "rapidfuzz", "rank_bm25", "numpy", "pandas", "openpyxl", "pydantic",
                "fastapi", "uvicorn", "PIL"]:
        try:
            __import__(mod)
            line(f"package {mod}", True)
        except Exception as exc:
            line(f"package {mod}", False, str(exc))
    for mod, why in [("fastembed", "embeddings + reranker (search falls back to keywords without it)"),
                     ("faiss", "vector index (numpy is used without it)"),
                     ("google.genai", "Gemini model calls")]:
        try:
            __import__(mod)
            line(f"package {mod}", True)
        except Exception:
            line(f"package {mod}", False, why, required=False)
    line("tesseract (OCR)", tesseract_available(), "scanned pages are listed for a manual look without it", required=False)
    line("ocrmypdf (OCR)", ocrmypdf_available(), "falls back to PyMuPDF + tesseract", required=False)
    line("data folder", s.data_dir.exists(), str(s.data_dir))
    line("model cache folder", True, str(s.model_cache_dir))
    if s.llm_provider == "gemini":
        key = s.gemini_api_key
        line("GEMINI_API_KEY", bool(key), (key[:4] + "…" + key[-3:]) if key else "not set: brief, risks and Ask are off",
             required=False)
        line("model", True, s.gemini_model)
    elif s.llm_provider == "openai":
        line("OpenAI-compatible server", True, f"{s.openai_base_url} model={s.openai_model}")
    else:
        line("model", False, "BIDBRIEF_LLM=none: brief, risks and Ask are off", required=False)

    if args.download:
        from .index import get_embedder, get_reranker

        print("Downloading / loading search models (once) ...")
        line("embedding model", get_embedder() is not None, s.embed_model, required=False)
        line("reranker model", get_reranker() is not None, s.rerank_model, required=False)
    if args.ping:
        from .llm import get_llm
        from .schemas import LLMAnswer

        llm = get_llm()
        if llm is None:
            line("model ping", False, "no model configured", required=False)
        else:
            try:
                r = llm.generate('Excerpt: <excerpt file="t.pdf" page="1">Bids are due at noon.</excerpt>\n'
                                 "QUESTION: When are bids due?", LLMAnswer, purpose="ping")
                line("model ping", True, r.text[:80])
            except Exception as exc:
                line("model ping", False, str(exc)[:200], required=False)
            if args.list_models and s.llm_provider == "gemini":
                try:
                    from google import genai

                    client = genai.Client(api_key=s.gemini_api_key)
                    names = [m.name for m in client.models.list() if "flash" in (m.name or "")]
                    print("Flash models on this key:", ", ".join(sorted(names)))
                except Exception as exc:
                    print("Could not list models:", exc)
    print("All required checks passed." if ok else "Some required checks failed (see FAIL above).")
    return 0 if ok else 1


def cmd_sample(args) -> int:
    from .sample import main

    main()
    return 0


def _progress(msg: str, frac: float) -> None:
    bar = "#" * int(frac * 30)
    print(f"\r[{bar:<30}] {frac * 100:5.1f}%  {msg[:60]:<60}", end="", flush=True)
    if frac >= 1.0:
        print()


def _print_summary(res) -> None:
    from .brief import field_status
    from .schemas import BRIEF_FIELDS

    print(f"\n{res.title}  ({res.package_id})")
    print(f"  pages: {res.page_count}   files: {len(res.files)}   model: {res.model}")
    print(f"  time: {res.timings}")
    print(f"  bid items: {len(res.bid_items.items)} from pages {[p for _, p in res.bid_items.bid_form_pages]}"
          f"   low-confidence: {sum(i.confidence < 0.8 for i in res.bid_items.items)}"
          f"   numbering gaps: {res.bid_items.numbering_gaps or 'none'}")
    print(f"  risks: {len(res.risks.flags)} shown, {res.risks.dropped_unverified} dropped (no verified quote)")
    labels = dict(BRIEF_FIELDS)
    for f in res.brief.fields:
        st = field_status(f)
        pages = ",".join(f"p.{c.page}" for c in f.citations if c.verified)
        print(f"  {labels.get(f.name, f.name):16} [{st:10}] {(f.value or 'Not found in the documents')[:70]} {pages}")
    probs = [p for p in res.pages if p.problem]
    if probs:
        print(f"  pages that need a manual look: {[(p.file, p.page) for p in probs]}")
    if res.brief.note:
        print(f"  note: {res.brief.note}")


def cmd_process(args) -> int:
    from .pipeline import process_package

    res = process_package(Path(args.folder), args.id, progress=_progress, use_model=not args.no_model)
    _print_summary(res)
    return 0


def cmd_process_all(args) -> int:
    from .config import get_settings
    from .pipeline import process_package

    root = get_settings().packages_dir
    folders = [p for p in sorted(root.iterdir()) if p.is_dir() and any(p.glob("*.pdf"))] if root.exists() else []
    if not folders:
        print(f"No package folders with PDFs in {root}")
        return 1
    for f in folders:
        print(f"\n=== {f.name}")
        try:
            res = process_package(f, progress=_progress, use_model=not args.no_model)
            _print_summary(res)
        except Exception as exc:
            print(f"FAILED: {exc}")
    return 0


def cmd_list(args) -> int:
    from .pipeline import list_packages

    pkgs = list_packages()
    if not pkgs:
        print("No processed packages yet.")
    for r in pkgs:
        print(f"{r.package_id:40} {r.page_count:5} pages  {len(r.bid_items.items):4} items  "
              f"{len(r.risks.flags):3} risks  {r.title}")
    return 0


def cmd_ask(args) -> int:
    from .ask import ask
    from .pipeline import load_package

    pkg = load_package(args.package_id)
    res = ask(args.question, pkg.index, pkg.store)
    print(f"[{res.status}] {res.answer.text}")
    for c in res.answer.citations:
        print(f"  {'✓' if c.verified else '✗'} {c.file} p.{c.page}: \"{c.quote[:100]}\"")
    if res.status in {"not_found", "no_model"}:
        print("  pages checked:", ", ".join(f"{f} p.{p}" for f, p in res.checked_pages))
    return 0


def cmd_serve(args) -> int:
    from .server import serve

    print(f"BidBrief is running on port {args.port}. Press Ctrl+C to stop.")
    serve(host=args.host, port=args.port)
    return 0


def cmd_new_code(args) -> int:
    from .trials import new_code

    code = new_code(args.prefix)
    print(code)
    print(f"\nAdd it to .env (comma-separate several codes), then restart the app:\n"
          f"BIDBRIEF_TRIAL_CODES={code}:{args.uses}")
    return 0


def cmd_trials(args) -> int:
    from .trials import usage_report

    rows = usage_report()
    if not rows:
        print("No trial codes set. Uploads are open to anyone who can reach the app.")
    for code, used, allowed in rows:
        print(f"{code:24} used {used} of {allowed}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bidbrief", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("doctor", help="check the setup")
    p.add_argument("--download", action="store_true", help="download the search models now (run on a login node)")
    p.add_argument("--ping", action="store_true", help="make one tiny model call")
    p.add_argument("--list-models", action="store_true", help="with --ping: list Flash models this key can use")
    p.set_defaults(fn=cmd_doctor)
    p = sub.add_parser("sample", help="build the synthetic sample package")
    p.set_defaults(fn=cmd_sample)
    p = sub.add_parser("process", help="process one package folder")
    p.add_argument("folder")
    p.add_argument("--id", default=None, help="package id (default: folder name)")
    p.add_argument("--no-model", action="store_true", help="skip model calls (bid items only)")
    p.set_defaults(fn=cmd_process)
    p = sub.add_parser("process-all", help="process every folder in data/packages")
    p.add_argument("--no-model", action="store_true")
    p.set_defaults(fn=cmd_process_all)
    p = sub.add_parser("list", help="list processed packages")
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("ask", help="ask a question")
    p.add_argument("package_id")
    p.add_argument("question")
    p.set_defaults(fn=cmd_ask)
    p = sub.add_parser("serve", help="start the web app")
    p.add_argument("--port", type=int, default=8501)
    p.add_argument("--host", default="0.0.0.0")
    p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("new-code", help="make a new trial access code")
    p.add_argument("--prefix", default="", help="e.g. SEAN")
    p.add_argument("--uses", type=int, default=3, help="packages this code may add")
    p.set_defaults(fn=cmd_new_code)
    p = sub.add_parser("trials", help="show how many packages each trial code has used")
    p.set_defaults(fn=cmd_trials)
    p = sub.add_parser("eval", help="run the evaluation (same as python -m bidbrief.eval)")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.set_defaults(fn=lambda a: __import__("bidbrief.eval", fromlist=["main"]).main(a.rest))
    args = ap.parse_args(argv)
    _setup_logging(args.verbose)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
