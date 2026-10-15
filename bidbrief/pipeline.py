"""Run a whole package: ingest -> bid items -> search index -> risks -> brief.
Results are saved so the app and the demo load instantly."""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .ask import EXAMPLE_QUESTIONS, ask
from .brief import build_brief_fields, build_read_first, empty_brief
from .changes import addendum_page_set, find_addenda, find_changes
from .citations import PageStore, shared_store
from .config import get_settings
from .index import SearchIndex
from .ingest import IngestedPackage, ingest_package, load_ingested, save_ingested
from .llm import describe_llm, get_llm
from .risks import build_risks
from .schemas import Brief, PackageResult, RisksResult
from .tables import extract_bid_items

log = logging.getLogger(__name__)

ProgressFn = Callable[[str, float], None]


def slugify(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
    return s[:60] or "package"


def _package_meta(package_dir: Path) -> dict:
    p = package_dir / "package.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def process_package(
    package_dir: Path,
    package_id: str | None = None,
    progress: ProgressFn | None = None,
    use_model: bool = True,
) -> PackageResult:
    s = get_settings()
    package_dir = Path(package_dir)
    package_id = package_id or slugify(package_dir.name)
    progress = progress or (lambda msg, frac: log.info("%3.0f%%  %s", frac * 100, msg))
    meta = _package_meta(package_dir)
    work_dir = s.work_dir / package_id
    timings: dict[str, float] = {}
    t_all = time.perf_counter()

    # 1. Read pages (and OCR scanned ones)
    t = time.perf_counter()
    ing = ingest_package(package_id, package_dir, work_dir,
                         progress=lambda m, f: progress(m, 0.05 + 0.35 * f))
    save_ingested(ing, work_dir)
    timings["ingest_s"] = round(time.perf_counter() - t, 2)
    if not ing.files:
        raise RuntimeError("None of the PDFs could be opened.")

    # 2. Bid items
    progress("Finding tables", 0.42)
    t = time.perf_counter()
    llm = get_llm() if use_model else None
    addenda = find_addenda(ing)
    items = extract_bid_items(ing, use_model=llm is not None, addendum_pages=addendum_page_set(addenda))
    timings["bid_items_s"] = round(time.perf_counter() - t, 2)

    # 3. Search index
    progress("Building the search index", 0.55)
    t = time.perf_counter()
    idx = SearchIndex(ing.chunks, work_dir=work_dir)
    timings["index_s"] = round(time.perf_counter() - t, 2)

    # 4. Brief and risks
    progress("Building the brief", 0.68)
    t = time.perf_counter()
    store = PageStore(ing.working)
    stats = {"model_citations": 0, "verified_citations": 0}
    examples: list = []
    if llm is None:
        note = "No model key is set, so the brief, risks and answers are off. Bid items still work."
        brief = empty_brief(note)
        risks = RisksResult(note=note)
        changes = find_changes(None, ing, idx, store, items.items)
    else:
        with ThreadPoolExecutor(max_workers=3) as pool:
            f_brief = pool.submit(build_brief_fields, llm, idx, store, ing)
            f_risks = pool.submit(build_risks, llm, idx, store)
            f_changes = pool.submit(find_changes, llm, ing, idx, store, items.items)
            fields, st1 = f_brief.result()
            risks, st2 = f_risks.result()
            changes = f_changes.result()
        for k in stats:
            stats[k] = st1.get(k, 0) + st2.get(k, 0)
        progress("Picking the pages to read first", 0.88)
        brief = Brief(fields=fields, read_first=build_read_first(ing, store, items, risks.flags))
        progress("Answering the example questions", 0.93)
        for q in EXAMPLE_QUESTIONS:
            try:
                examples.append(ask(q, idx, store, llm=llm))
            except Exception as exc:  # examples are a convenience; never fail the run
                log.warning("Example question failed: %s", exc)
    store.close()
    timings["brief_risks_s"] = round(time.perf_counter() - t, 2)
    timings["total_s"] = round(time.perf_counter() - t_all, 2)

    pf = brief.get("project_name")
    title = meta.get("title") or (pf.value if pf and pf.value else None) or package_dir.name
    result = PackageResult(
        package_id=package_id, title=title[:120], files=ing.files, page_count=sum(1 for p in ing.pages if p.page > 0),
        pages=ing.pages, brief=brief, bid_items=items, risks=risks, timings=timings,
        citation_stats={**stats, "risks_dropped_unverified": risks.dropped_unverified},
        model=describe_llm() if llm else "none",
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        synthetic=bool(meta.get("synthetic", False)),
        example_answers=examples,
        changes=changes,
    )
    save_result(result)
    progress("Done", 1.0)
    return result


# ------------------------------------------------------------- storage
def result_path(package_id: str) -> Path:
    return get_settings().results_dir / package_id / "result.json"


def save_result(result: PackageResult) -> Path:
    p = result_path(result.package_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(result.model_dump_json(indent=1), encoding="utf-8")
    return p


def list_packages() -> list[PackageResult]:
    root = get_settings().results_dir
    out = []
    if root.exists():
        for d in sorted(root.iterdir()):
            p = d / "result.json"
            if p.exists():
                try:
                    out.append(PackageResult.model_validate_json(p.read_text(encoding="utf-8")))
                except Exception as exc:
                    log.warning("Skipping %s: %s", p, exc)
    return out


@dataclass
class LoadedPackage:
    result: PackageResult
    work_dir: Path
    _ing: IngestedPackage | None = None
    _idx: SearchIndex | None = None
    _store: PageStore | None = field(default=None, repr=False)

    @property
    def ingested(self) -> IngestedPackage:
        if self._ing is None:
            self._ing = load_ingested(self.work_dir)
        return self._ing

    @property
    def store(self) -> PageStore:
        if self._store is None:
            self._store = shared_store(self.ingested.working)
        return self._store

    @property
    def index(self) -> SearchIndex:
        if self._idx is None:
            self._idx = SearchIndex(self.ingested.chunks, work_dir=self.work_dir)
        return self._idx


def load_package(package_id: str) -> LoadedPackage:
    p = result_path(package_id)
    if not p.exists():
        raise FileNotFoundError(f"No processed package named {package_id!r}. Run: python -m bidbrief process <folder>")
    result = PackageResult.model_validate_json(p.read_text(encoding="utf-8"))
    return LoadedPackage(result=result, work_dir=get_settings().work_dir / package_id)
