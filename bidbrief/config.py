"""Settings, read once from environment variables (and an optional .env file).

Nothing secret is ever hard-coded here. See .env.example for every name.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional dependency; the app works without it
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except Exception:  # pragma: no cover
    pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name, "1" if default else "0").lower()
    return raw in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("BIDBRIEF_DATA_DIR", str(PROJECT_ROOT / "data"))))
    model_cache_dir: Path = field(
        default_factory=lambda: Path(
            _env("BIDBRIEF_MODEL_CACHE", _env("HF_HOME", str(PROJECT_ROOT / ".model_cache")))
        )
    )

    # Which model provider to use: "gemini", "openai" (any OpenAI-compatible server,
    # e.g. a local Qwen served by vLLM on SCC) or "none" (no model calls at all).
    llm_provider: str = field(default_factory=lambda: _env("BIDBRIEF_LLM", "gemini").lower())
    gemini_api_key: str = field(default_factory=lambda: _env("GEMINI_API_KEY", _env("GOOGLE_API_KEY")))
    gemini_model: str = field(default_factory=lambda: _env("GEMINI_MODEL", "gemini-flash-latest"))
    openai_base_url: str = field(default_factory=lambda: _env("OPENAI_BASE_URL", "http://localhost:8000/v1"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY", "not-needed"))
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL", "Qwen/Qwen2.5-7B-Instruct"))
    llm_rpm: float = field(default_factory=lambda: _env_float("LLM_RPM", 5.0))
    llm_timeout: float = field(default_factory=lambda: _env_float("LLM_TIMEOUT", 120.0))
    llm_cache: bool = field(default_factory=lambda: _env_bool("LLM_CACHE", True))

    # Retrieval
    embed_model: str = field(default_factory=lambda: _env("BIDBRIEF_EMBED_MODEL", "BAAI/bge-small-en-v1.5"))
    use_embeddings: bool = field(default_factory=lambda: _env_bool("BIDBRIEF_EMBEDDINGS", True))
    use_reranker: bool = field(default_factory=lambda: _env_bool("BIDBRIEF_RERANK", True))
    rerank_model: str = field(
        default_factory=lambda: _env("BIDBRIEF_RERANK_MODEL", "Xenova/ms-marco-MiniLM-L-6-v2")
    )
    top_k: int = field(default_factory=lambda: _env_int("BIDBRIEF_TOP_K", 10))

    # Citation check: fuzzy match threshold, 0-100 (the spec's 0.9)
    quote_threshold: float = field(default_factory=lambda: _env_float("BIDBRIEF_QUOTE_THRESHOLD", 90.0))

    # OCR
    ocr_enabled: bool = field(default_factory=lambda: _env_bool("BIDBRIEF_OCR", True))
    ocr_jobs: int = field(default_factory=lambda: _env_int("BIDBRIEF_OCR_JOBS", max(1, (os.cpu_count() or 2) - 1)))

    @property
    def packages_dir(self) -> Path:
        return self.data_dir / "packages"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def results_dir(self) -> Path:
        return self.data_dir / "results"

    @property
    def answer_keys_dir(self) -> Path:
        return self.data_dir / "answer_keys"

    @property
    def llm_cache_dir(self) -> Path:
        return self.data_dir / "llm_cache"

    def llm_ready(self) -> bool:
        if self.llm_provider == "gemini":
            return bool(self.gemini_api_key)
        if self.llm_provider == "openai":
            return bool(self.openai_base_url)
        return False


_SETTINGS: Settings | None = None


def get_settings() -> Settings:
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = Settings()
    return _SETTINGS


def reset_settings() -> None:
    """Re-read the environment (used by tests)."""
    global _SETTINGS
    _SETTINGS = None
