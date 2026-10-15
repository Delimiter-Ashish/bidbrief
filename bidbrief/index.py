"""Hybrid search: BM25 keywords + embeddings, merged with reciprocal rank
fusion and (optionally) re-scored by a small cross-encoder.

Keywords matter here: bid documents use exact terms ("liquidated damages",
"retainage", "Item 401"). Embeddings catch paraphrases. If the embedding or
reranking model cannot be loaded (for example no internet on a compute node),
search falls back to keywords only and says so in the logs. Nothing breaks.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from .config import get_settings
from .schemas import Chunk

log = logging.getLogger(__name__)

_STOP = set("""a an the of to and or in on for by with at from as is are be been this that these those it its
shall will may any all such which who whom their there than then into upon under over not no""".split())

_TOKEN = re.compile(r"[a-z0-9]+(?:[.\-/][a-z0-9]+)*")


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for tok in _TOKEN.findall((text or "").lower()):
        if tok in _STOP:
            continue
        out.append(tok)
        if any(ch in tok for ch in ".-/"):
            out.extend(p for p in re.split(r"[.\-/]", tok) if p and p not in _STOP)
    return out


# ------------------------------------------------------- shared models
_MODEL_LOCK = threading.Lock()
_EMBEDDER = None
_EMBEDDER_FAILED = False
_RERANKER = None
_RERANKER_FAILED = False


def get_embedder():
    global _EMBEDDER, _EMBEDDER_FAILED
    s = get_settings()
    if not s.use_embeddings or _EMBEDDER_FAILED:
        return None
    with _MODEL_LOCK:
        if _EMBEDDER is None and not _EMBEDDER_FAILED:
            try:
                from fastembed import TextEmbedding

                s.model_cache_dir.mkdir(parents=True, exist_ok=True)
                _EMBEDDER = TextEmbedding(model_name=s.embed_model, cache_dir=str(s.model_cache_dir))
                log.info("Embedding model loaded: %s", s.embed_model)
            except Exception as exc:
                _EMBEDDER_FAILED = True
                log.warning("Embeddings off (keyword search only): %s", exc)
    return _EMBEDDER


def get_reranker():
    global _RERANKER, _RERANKER_FAILED
    s = get_settings()
    if not s.use_reranker or _RERANKER_FAILED:
        return None
    with _MODEL_LOCK:
        if _RERANKER is None and not _RERANKER_FAILED:
            try:
                from fastembed.rerank.cross_encoder import TextCrossEncoder

                s.model_cache_dir.mkdir(parents=True, exist_ok=True)
                _RERANKER = TextCrossEncoder(model_name=s.rerank_model, cache_dir=str(s.model_cache_dir))
                log.info("Reranker loaded: %s", s.rerank_model)
            except Exception as exc:
                _RERANKER_FAILED = True
                log.warning("Reranker off (fusion order only): %s", exc)
    return _RERANKER


def _chunk_doc_text(c: Chunk) -> str:
    return f"{c.section or ''}\n{c.text}"


def _fingerprint(chunks: list[Chunk]) -> str:
    h = hashlib.sha1()
    for c in chunks:
        h.update(c.chunk_id.encode())
        h.update(str(len(c.text)).encode())
    return h.hexdigest()[:16]


# ------------------------------------------------------------- index
@dataclass
class Hit:
    chunk: Chunk
    score: float


class SearchIndex:
    def __init__(self, chunks: list[Chunk], work_dir: Path | None = None, build_embeddings: bool = True):
        self.chunks = chunks
        self.work_dir = work_dir
        self._bm25 = BM25Okapi([tokenize(_chunk_doc_text(c)) or ["_"] for c in chunks]) if chunks else None
        self._emb: np.ndarray | None = None
        self._faiss = None
        self.mode = "keywords"
        if build_embeddings and chunks:
            self._load_or_build_embeddings()

    # -- embeddings
    def _load_or_build_embeddings(self) -> None:
        s = get_settings()
        fp = _fingerprint(self.chunks)
        if self.work_dir:
            meta_p = self.work_dir / "embeddings.json"
            emb_p = self.work_dir / "embeddings.npy"
            if meta_p.exists() and emb_p.exists():
                try:
                    meta = json.loads(meta_p.read_text())
                    if meta.get("model") == s.embed_model and meta.get("fingerprint") == fp:
                        self._set_embeddings(np.load(emb_p))
                        return
                except Exception as exc:  # pragma: no cover
                    log.warning("Could not load saved embeddings: %s", exc)
        emb_model = get_embedder()
        if emb_model is None:
            return
        try:
            texts = [_chunk_doc_text(c)[:2000] for c in self.chunks]
            vecs = np.asarray(list(emb_model.passage_embed(texts, batch_size=64)), dtype=np.float32)
            vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
        except Exception as exc:
            log.warning("Embedding failed, keyword search only: %s", exc)
            return
        self._set_embeddings(vecs)
        if self.work_dir:
            self.work_dir.mkdir(parents=True, exist_ok=True)
            np.save(self.work_dir / "embeddings.npy", vecs)
            (self.work_dir / "embeddings.json").write_text(json.dumps({"model": s.embed_model, "fingerprint": fp}))

    def _set_embeddings(self, vecs: np.ndarray) -> None:
        if len(vecs) != len(self.chunks):
            return
        self._emb = vecs.astype(np.float32)
        try:
            import faiss

            idx = faiss.IndexFlatIP(self._emb.shape[1])
            idx.add(self._emb)
            self._faiss = idx
        except Exception:
            self._faiss = None  # numpy dot product is fine for a few thousand chunks
        self.mode = "hybrid"

    def _dense(self, query: str, k: int) -> list[int]:
        if self._emb is None:
            return []
        emb_model = get_embedder()
        if emb_model is None:
            return []
        try:
            q = np.asarray(list(emb_model.query_embed([query]))[0], dtype=np.float32)
            q /= np.linalg.norm(q) + 1e-9
        except Exception as exc:  # pragma: no cover
            log.warning("Query embedding failed: %s", exc)
            return []
        if self._faiss is not None:
            _, ids = self._faiss.search(q[None, :], min(k, len(self.chunks)))
            return [int(i) for i in ids[0] if i >= 0]
        scores = self._emb @ q
        return [int(i) for i in np.argsort(-scores)[:k]]

    def _sparse(self, query: str, k: int) -> list[int]:
        if self._bm25 is None:
            return []
        toks = tokenize(query)
        if not toks:
            return []
        scores = self._bm25.get_scores(toks)
        order = np.argsort(-scores)[:k]
        return [int(i) for i in order if scores[i] > 0]

    # -- public
    def search(self, query: str, k: int | None = None, pool: int = 40, rerank: bool = True,
               sections: set[str] | None = None) -> list[Hit]:
        k = k or get_settings().top_k
        if not self.chunks:
            return []
        lists = [self._sparse(query, pool), self._dense(query, pool)]
        fused: dict[int, float] = {}
        for lst in lists:
            for rank, i in enumerate(lst):
                fused[i] = fused.get(i, 0.0) + 1.0 / (60 + rank)
        cand = sorted(fused, key=lambda i: -fused[i])
        from .ingest import top_section

        # A table of contents mentions every topic but answers none: push it last.
        cand = ([i for i in cand if top_section(self.chunks[i].section) != "Table of Contents"]
                + [i for i in cand if top_section(self.chunks[i].section) == "Table of Contents"])
        if sections:
            cand = [i for i in cand if top_section(self.chunks[i].section) in sections] or cand
        cand = cand[:pool]
        if rerank and len(cand) > 1:
            rr = get_reranker()
            if rr is not None:
                try:
                    docs = [self.chunks[i].text[:1500] for i in cand]
                    scores = list(rr.rerank(query, docs))
                    order = sorted(range(len(cand)), key=lambda j: -float(scores[j]))
                    return [Hit(self.chunks[cand[j]], float(scores[j])) for j in order[:k]]
                except Exception as exc:  # pragma: no cover
                    log.warning("Rerank failed, using fusion order: %s", exc)
        return [Hit(self.chunks[i], fused[i]) for i in cand[:k]]

    def multi_search(self, queries: list[str], per_query: int = 4, limit: int = 30,
                     rerank: bool = False) -> list[Chunk]:
        seen: dict[str, Chunk] = {}
        for q in queries:
            for h in self.search(q, k=per_query, rerank=rerank):
                seen.setdefault(h.chunk.chunk_id, h.chunk)
                if len(seen) >= limit:
                    break
        return list(seen.values())

    def chunks_on_pages(self, pages: list[tuple[str, int]]) -> list[Chunk]:
        want = set(pages)
        return [c for c in self.chunks if (c.file, c.page) in want]


def format_excerpts(chunks: list[Chunk], max_chars: int = 60000) -> str:
    """Excerpts as shown to the model: each with file and page."""
    parts: list[str] = []
    total = 0
    for c in sorted(chunks, key=lambda c: (c.file, c.page, c.char_start)):
        block = f'<excerpt file="{c.file}" page="{c.page}" section="{c.section or ""}">\n{c.text}\n</excerpt>'
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n\n".join(parts)


def excerpt_pages(chunks: list[Chunk]) -> list[tuple[str, int]]:
    return list(dict.fromkeys((c.file, c.page) for c in chunks))
