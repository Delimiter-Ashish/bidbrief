"""Evaluation: compare BidBrief's output with hand-made answer keys.

    python -m bidbrief.eval                    # every package that has an answer key
    python -m bidbrief.eval --packages my-pkg  # one package
    python -m bidbrief.eval --label "after OCR fix" --compare   # before/after table

Answer keys live in data/answer_keys/<package_id>/ (see the README there):
  bid_items.csv  item_no,description,quantity,unit,page
  brief.csv      field,value,file,pages      (value = short text a correct answer must contain)
  questions.csv  id,question,answerable,expected_contains,pages
  risks.csv      category,pages,note
Pages lists use ';'. Every run is saved to data/eval_runs/ and the latest table to EVAL_RESULTS.md.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from rapidfuzz import fuzz

from .ask import ask
from .brief import FIELD_QUERIES, field_status
from .citations import normalize_text
from .config import PROJECT_ROOT, get_settings
from .llm import get_llm
from .pipeline import load_package, process_package, result_path
from .tables import is_unit

log = logging.getLogger(__name__)


def _pages(s: str) -> set[int]:
    out = set()
    for p in str(s or "").replace(",", ";").split(";"):
        p = p.strip()
        if p.isdigit():
            out.add(int(p))
    return out


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [{k.strip(): (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(fh)]


def _unit_key(u: str) -> str:
    return (u or "").replace(".", "").replace(" ", "").upper()


def _desc_key(d: str) -> str:
    return normalize_text(d).strip(" .,;:")


def _contains(expected: str, text: str) -> bool:
    e, t = normalize_text(expected), normalize_text(text)
    if not e:
        return True
    if e in t or e.replace(",", "") in t.replace(",", ""):
        return True
    return len(e) >= 8 and fuzz.partial_ratio(e, t) >= 90


@dataclass
class Metric:
    name: str
    num: int = 0
    den: int = 0
    target: str = ""
    extra: str = ""

    @property
    def pct(self) -> float | None:
        return (100.0 * self.num / self.den) if self.den else None

    def cell(self) -> str:
        if self.den == 0 and self.extra:
            return self.extra
        if self.den == 0:
            return "n/a"
        return f"{self.pct:.1f}% ({self.num}/{self.den})" + (f" · {self.extra}" if self.extra else "")


@dataclass
class PackageEval:
    package_id: str
    metrics: list[Metric] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


# ------------------------------------------------------------- bid items
def eval_items(pkg, key_dir: Path, ev: PackageEval) -> None:
    key = _read_csv(key_dir / "bid_items.csv")
    if not key:
        return
    pred = pkg.result.bid_items
    by_no: dict[str, list] = {}
    for it in pred.items:
        by_no.setdefault(it.item_no.strip().upper(), []).append(it)
    form_pages = {p for _, p in pred.bid_form_pages}
    ocr_pages = {(p.file, p.page) for p in pkg.result.pages if p.is_ocr}
    exact = missed = 0
    key_nos = set()
    for row in key:
        no = row["item_no"].strip().upper()
        key_nos.add(no)
        cands = by_no.get(no, [])
        if not cands:
            missed += 1
            kp = _pages(row.get("page"))
            if kp and not (kp & form_pages):
                why = "bid form page was not detected"
            elif any(p in {pg for _, pg in ocr_pages} for p in kp):
                why = "bad OCR on a scanned page"
            else:
                why = "table parse missed the row (check for a table split across pages)"
            ev.failures.append(f"Bid item {row['item_no']}: missing — {why}")
            continue
        it = cands[0]
        problems = []
        if _desc_key(it.description) != _desc_key(row["description"]):
            problems.append("description")
        if it.quantity.strip() != row["quantity"].strip():
            problems.append("quantity")
        if _unit_key(it.unit) != _unit_key(row["unit"]):
            problems.append("unit")
        if not problems:
            exact += 1
        else:
            if "description" in problems and len(it.description) < len(row["description"]):
                why = "description cut short (wrapped cell or table split across pages)"
            elif "unit" in problems and not is_unit(it.unit):
                why = "unit column misread"
            else:
                why = "model or parser misread the row"
            ev.failures.append(f"Bid item {row['item_no']}: wrong {', '.join(problems)} — {why} "
                               f"(got {it.quantity!r} {it.unit!r} {it.description[:50]!r})")
    invented = [it.item_no for it in pred.items if it.item_no.strip().upper() not in key_nos]
    for no in invented:
        ev.failures.append(f"Bid item {no}: extracted but not in the answer key (invented or extra row)")
    ev.metrics.append(Metric("Bid items: rows matched exactly", exact, len(key), "95%+ on clean forms"))
    ev.metrics.append(Metric("Bid items: missed / invented", 0, 0, "invented = 0",
                             extra=f"{missed} missed, {len(invented)} invented, {len(pred.items)} extracted vs {len(key)} in form"))


# ------------------------------------------------------------- brief
def eval_brief(pkg, key_dir: Path, ev: PackageEval) -> None:
    key = _read_csv(key_dir / "brief.csv")
    if not key:
        return
    ok = 0
    idx = None
    for row in key:
        name = row["field"]
        exp_pages = _pages(row.get("pages"))
        f = pkg.result.brief.get(name)
        status = field_status(f) if f else "missing"
        if status != "found":
            if status == "missing":
                if idx is None:
                    idx = pkg.index
                hit_pages = {h.chunk.page for q in FIELD_QUERIES.get(name, [name]) for h in idx.search(q, k=6)}
                why = "model skipped it" if exp_pages & hit_pages else "retrieval missed the page"
            else:
                why = "quote failed the citation check (shown grey)"
            ev.failures.append(f"Brief {name}: {status} — {why}")
            continue
        val_ok = _contains(row.get("value", ""), f.value or "")
        page_ok = not exp_pages or any(c.verified and c.page in exp_pages for c in f.citations)
        if val_ok and page_ok:
            ok += 1
        else:
            why = "value differs from the answer key" if not val_ok else "cited a different page"
            ev.failures.append(f"Brief {name}: wrong — {why} (got {(f.value or '')[:70]!r})")
    ev.metrics.append(Metric("Brief: fields correct with a correct page", ok, len(key), "90%+"))


# ------------------------------------------------------------- ask
def eval_ask(pkg, key_dir: Path, ev: PackageEval, cit_counter: dict) -> None:
    key = _read_csv(key_dir / "questions.csv")
    if not key:
        return
    if get_llm() is None:
        ev.metrics.append(Metric("Ask", extra="skipped: no model key set"))
        return
    ans_ok = ans_n = nf_ok = nf_n = 0
    idx, store = pkg.index, pkg.store
    for row in key:
        q = row["question"]
        answerable = row.get("answerable", "1").strip() in {"1", "yes", "true", "y"}
        res = ask(q, idx, store)
        cit_counter["model"] += res.raw_citations
        cit_counter["verified"] += sum(c.verified for c in res.answer.citations)
        if answerable:
            ans_n += 1
            exp_pages = _pages(row.get("pages"))
            text_ok = _contains(row.get("expected_contains", ""), res.answer.text)
            page_ok = not exp_pages or any(c.verified and c.page in exp_pages for c in res.answer.citations)
            if res.status == "verified" and text_ok and page_ok:
                ans_ok += 1
                continue
            retrieved = {h.chunk.page for h in idx.search(q, k=10)}
            if exp_pages and not (exp_pages & retrieved):
                why = "retrieval missed the page"
            elif res.status == "not_found":
                why = "model said not found although the page was retrieved"
            elif res.status == "unverified":
                why = "quote failed the citation check"
            elif not page_ok:
                why = "cited a different page"
            else:
                why = "model misread it"
            ev.failures.append(f"Ask {row.get('id', '')} {q!r} — {why} (got {res.answer.text[:80]!r})")
        else:
            nf_n += 1
            if res.status == "not_found":
                nf_ok += 1
            else:
                ev.failures.append(f"Ask {row.get('id', '')} {q!r} — answered a question the documents don't answer "
                                   f"(got {res.status}: {res.answer.text[:80]!r})")
    ev.metrics.append(Metric("Ask: answers correct and cited to the right page", ans_ok, ans_n, "85%+"))
    ev.metrics.append(Metric("Ask: unanswerable marked 'not found'", nf_ok, nf_n, "90%+"))


# ------------------------------------------------------------- risks
def eval_risks(pkg, key_dir: Path, ev: PackageEval) -> None:
    key = _read_csv(key_dir / "risks.csv")
    if not key:
        return
    flags = pkg.result.risks.flags
    if pkg.result.risks.note and not flags:
        ev.metrics.append(Metric("Risks", extra="skipped: no model key set"))
        return
    found = 0
    used = set()
    for row in key:
        pages = _pages(row.get("pages"))
        hit = None
        for i, fl in enumerate(flags):
            if fl.category.lower() == row["category"].lower() and any(c.page in pages for c in fl.citations):
                hit = i
                break
        if hit is None:
            ev.failures.append(f"Risk {row['category']} ({row.get('note', '')}) — real risk not flagged")
        else:
            found += 1
            used.add(hit)
    false_flags = 0
    for i, fl in enumerate(flags):
        if i in used:
            continue
        match_any = any(fl.category.lower() == r["category"].lower() and
                        any(c.page in _pages(r.get("pages")) for c in fl.citations) for r in key)
        if not match_any:
            false_flags += 1
            ev.failures.append(f"Risk flag {fl.category} p.{fl.citations[0].page} {fl.reason[:60]!r} — flag not in the answer key")
    ev.metrics.append(Metric("Risks: real risks found", found, len(key), "report",
                             extra=f"{false_flags} flags not in key"))


# ------------------------------------------------------------- run
def run(packages: list[str] | None, with_ask: bool = True, label: str = "", include_sample: bool = False) -> dict:
    s = get_settings()
    keys_root = s.answer_keys_dir
    if packages:
        ids = list(packages)
    elif keys_root.exists():
        ids = sorted(p.name for p in keys_root.iterdir() if p.is_dir() and not p.name.startswith("_"))
    else:
        ids = []
    evals: list[PackageEval] = []
    cit = {"model": 0, "verified": 0}
    for pid in ids:
        key_dir = keys_root / pid
        if not result_path(pid).exists():
            pdir = s.packages_dir / pid
            if not pdir.exists():
                print(f"[skip] {pid}: no results and no package folder")
                continue
            print(f"[process] {pid} ...")
            process_package(pdir, pid)
        pkg = load_package(pid)
        if pkg.result.synthetic and not (include_sample or packages):
            print(f"[skip] {pid}: synthetic sample (use --include-sample or --packages {pid})")
            continue
        ev = PackageEval(pid + (" (synthetic)" if pkg.result.synthetic else ""))
        eval_items(pkg, key_dir, ev)
        eval_brief(pkg, key_dir, ev)
        if with_ask:
            eval_ask(pkg, key_dir, ev, cit)
        eval_risks(pkg, key_dir, ev)
        st = pkg.result.citation_stats
        cit["model"] += st.get("model_citations", 0)
        cit["verified"] += st.get("verified_citations", 0)
        t = pkg.result.timings.get("total_s", 0.0)
        ev.metrics.append(Metric("Speed: time to process", extra=f"{t:.0f} s for {pkg.result.page_count} pages "
                                 f"(ingest {pkg.result.timings.get('ingest_s', 0):.0f} s)"))
        evals.append(ev)

    run_data = {
        "label": label, "time": datetime.now().isoformat(timespec="seconds"),
        "packages": {e.package_id: {m.name: {"num": m.num, "den": m.den, "pct": m.pct, "extra": m.extra}
                                    for m in e.metrics} for e in evals},
        "failures": {e.package_id: e.failures for e in evals},
        "citations": cit,
    }
    report = render(evals, cit, label)
    print(report)
    out_dir = s.data_dir / "eval_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out_dir / f"{stamp}.json").write_text(json.dumps(run_data, indent=1))
    (PROJECT_ROOT / "EVAL_RESULTS.md").write_text(report)
    return run_data


def render(evals: list[PackageEval], cit: dict, label: str) -> str:
    lines = [f"# BidBrief evaluation{(' — ' + label) if label else ''}", "",
             f"_Run {datetime.now():%Y-%m-%d %H:%M}. Numbers are measured, not promised._", ""]
    for e in evals:
        lines += [f"## {e.package_id}", "", "| What | Result | Target |", "|---|---|---|"]
        for m in e.metrics:
            lines.append(f"| {m.name} | {m.cell()} | {m.target} |")
        lines.append("")
    raw = (100.0 * cit["verified"] / cit["model"]) if cit["model"] else None
    lines += ["## Citations", "",
              f"- Model quotes that passed the check before filtering: "
              f"{'n/a' if raw is None else f'{raw:.1f}%'} ({cit['verified']}/{cit['model']})",
              "- Quotes shown as verified that really appear on the cited page: 100% (enforced by the citation check; "
              "anything else is dropped or shown grey as unverified)", ""]
    lines += ["## Error analysis (one line per failure)", ""]
    any_fail = False
    for e in evals:
        for f in e.failures:
            lines.append(f"- [{e.package_id}] {f}")
            any_fail = True
    if not any_fail:
        lines.append("- No failures.")
    causes: dict[str, int] = {}
    for e in evals:
        for f in e.failures:
            cause = f.rsplit(" — ", 1)[-1].split(" (got")[0] if " — " in f else "other"
            causes[cause] = causes.get(cause, 0) + 1
    if causes:
        lines += ["", "### Biggest causes", ""]
        for c, n in sorted(causes.items(), key=lambda kv: -kv[1])[:6]:
            lines.append(f"- {n} × {c}")
    return "\n".join(lines) + "\n"


def compare_last_two() -> str:
    runs = sorted((get_settings().data_dir / "eval_runs").glob("*.json"))
    if len(runs) < 2:
        return "Need at least two saved runs to compare."
    a, b = (json.loads(p.read_text()) for p in runs[-2:])
    lines = [f"| Package | What | Before ({a.get('label') or a['time']}) | After ({b.get('label') or b['time']}) |",
             "|---|---|---|---|"]
    for pid, metrics in b["packages"].items():
        for name, m in metrics.items():
            old = a["packages"].get(pid, {}).get(name)

            def fmt(x):
                if not x:
                    return "—"
                return f"{x['pct']:.1f}%" if x.get("pct") is not None else (x.get("extra") or "—")
            lines.append(f"| {pid} | {name} | {fmt(old)} | {fmt(m)} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m bidbrief.eval", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--packages", nargs="*", help="package ids (default: all with an answer key)")
    ap.add_argument("--no-ask", action="store_true", help="skip the 20 questions per package (saves model calls)")
    ap.add_argument("--label", default="", help="a name for this run, e.g. 'baseline' or 'after OCR fix'")
    ap.add_argument("--include-sample", action="store_true", help="also evaluate the synthetic sample package")
    ap.add_argument("--compare", action="store_true", help="after the run, print a before/after table vs the previous run")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    run(args.packages, with_ask=not args.no_ask, label=args.label, include_sample=args.include_sample)
    if args.compare:
        print("\n## Before and after\n")
        print(compare_last_two())
    return 0


if __name__ == "__main__":
    sys.exit(main())
