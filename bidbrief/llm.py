"""Model calls: JSON in, validated Pydantic out.

* Providers: Gemini (default) or any OpenAI-compatible server (for a local
  Qwen on BU SCC served with vLLM). Set BIDBRIEF_LLM=none to turn models off.
* Every output is parsed as JSON and validated against a Pydantic schema.
  Invalid output is retried once with the validation error shown to the model.
* Rate limited (LLM_RPM) with backoff on 429/5xx, so free-tier limits do not
  break a run.
* Every successful call is cached on disk by a hash of the request, so
  re-running a package or the evaluation costs nothing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .config import get_settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

SYSTEM_RULES = """You help construction estimators read public bid documents.
Rules for every answer:
1. Use only the provided excerpts. Never use outside knowledge about construction, law, or practice.
2. Every claim carries a quote copied exactly, character for character, from one excerpt, with that excerpt's file and page. Quotes are short (one sentence or less, at most about 40 words) and never stitched together from different places.
3. If the excerpts do not answer it, say so (found: false, or value: null). Never guess.
4. Never compute, round, total or convert quantities, money, dates or durations. Copy them as printed.
5. Write plain English for a busy estimator. No jargon about AI.
6. Return only JSON that matches the schema. No markdown, no commentary."""


class LLMError(RuntimeError):
    pass


class _RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self, rpm: float) -> None:
        if rpm <= 0:
            return
        gap = 60.0 / rpm
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + gap
        delay = start - time.monotonic()
        if delay > 0:
            time.sleep(delay)


_LIMITER = _RateLimiter()


def extract_json(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    if t.startswith("{") or t.startswith("["):
        return t
    a, b = t.find("{"), t.rfind("}")
    if a != -1 and b > a:
        return t[a:b + 1]
    return t


def schema_text(schema: type[BaseModel]) -> str:
    return json.dumps(schema.model_json_schema(), separators=(",", ":"))


class BaseLLM:
    name = "base"
    model = ""
    supports_images = False

    def __init__(self) -> None:
        self.calls = 0
        self.cache_hits = 0
        self._cache_dir: Path | None = None
        s = get_settings()
        if s.llm_cache:
            self._cache_dir = s.llm_cache_dir
            self._cache_dir.mkdir(parents=True, exist_ok=True)

    # implemented by providers: returns raw text
    def _call(self, system: str, prompt: str, images: list[bytes]) -> str:  # pragma: no cover
        raise NotImplementedError

    def _transient(self, exc: Exception) -> bool:
        msg = str(exc).upper()
        code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
        return code in (408, 429, 500, 502, 503, 504) or any(
            k in msg for k in ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "TIMEOUT", "TIMED OUT", "429", "503", "DEADLINE"))

    def _call_with_backoff(self, system: str, prompt: str, images: list[bytes]) -> str:
        s = get_settings()
        delays = [5, 20, 60]
        last: Exception | None = None
        for attempt in range(len(delays) + 1):
            _LIMITER.wait(s.llm_rpm)
            try:
                self.calls += 1
                return self._call(system, prompt, images)
            except Exception as exc:  # provider errors vary by SDK version
                last = exc
                if not self._transient(exc) or attempt == len(delays):
                    break
                log.warning("Model busy or rate limited (%s); retrying in %ss", type(exc).__name__, delays[attempt])
                time.sleep(delays[attempt])
        msg = str(last)
        if "RESOURCE_EXHAUSTED" in msg.upper() or "429" in msg:
            raise LLMError("The model's usage limit was reached. Wait a while (or raise the key's limit) and run again; "
                           "finished calls are cached, so nothing is lost.") from last
        raise LLMError(f"Model call failed: {type(last).__name__}: {msg[:400]}") from last

    def _key(self, system: str, prompt: str, schema: type[BaseModel], images: list[bytes]) -> str:
        h = hashlib.sha256()
        for part in (self.name, self.model, system, prompt, schema.__name__, schema_text(schema)):
            h.update(part.encode("utf-8"))
            h.update(b"\x00")
        for img in images:
            h.update(hashlib.sha256(img).digest())
        return h.hexdigest()

    def generate(self, prompt: str, schema: type[T], images: list[bytes] | None = None,
                 purpose: str = "", system: str = SYSTEM_RULES) -> T:
        images = images or []
        full = f"{prompt}\n\nReturn JSON matching this JSON Schema:\n{schema_text(schema)}"
        key = self._key(system, full, schema, images)
        cache_file = self._cache_dir / f"{key}.json" if self._cache_dir else None
        if cache_file and cache_file.exists():
            try:
                obj = schema.model_validate_json(cache_file.read_text(encoding="utf-8"))
                self.cache_hits += 1
                return obj
            except (ValidationError, OSError):
                pass

        raw = self._call_with_backoff(system, full, images)
        try:
            obj = schema.model_validate_json(extract_json(raw))
        except (ValidationError, ValueError) as err:
            log.info("Invalid JSON from model for %s; retrying once", purpose or schema.__name__)
            retry = (f"{full}\n\nYour previous reply was not valid for the schema. Error:\n{str(err)[:1500]}\n"
                     "Reply again with only valid JSON for the schema.")
            raw = self._call_with_backoff(system, retry, images)
            try:
                obj = schema.model_validate_json(extract_json(raw))
            except (ValidationError, ValueError) as err2:
                raise LLMError(f"Model returned invalid JSON twice for {purpose or schema.__name__}: {str(err2)[:300]}") from err2
        if cache_file:
            try:
                cache_file.write_text(obj.model_dump_json(), encoding="utf-8")
            except OSError:  # pragma: no cover
                pass
        return obj


class GeminiLLM(BaseLLM):
    name = "gemini"
    supports_images = True

    def __init__(self, api_key: str, model: str, timeout_s: float) -> None:
        super().__init__()
        from google import genai
        from google.genai import types

        self._types = types
        self.model = model
        try:
            http = types.HttpOptions(timeout=int(timeout_s * 1000))
            self._client = genai.Client(api_key=api_key, http_options=http)
        except Exception:  # older SDKs
            self._client = genai.Client(api_key=api_key)

    def _call(self, system: str, prompt: str, images: list[bytes]) -> str:
        types = self._types
        contents: list = [prompt]
        for img in images:
            contents.append(types.Part.from_bytes(data=img, mime_type="image/png"))
        cfg = types.GenerateContentConfig(
            system_instruction=system, temperature=0.0, response_mime_type="application/json",
        )
        resp = self._client.models.generate_content(model=self.model, contents=contents, config=cfg)
        text = getattr(resp, "text", None)
        if not text:
            reason = ""
            try:
                reason = str(resp.candidates[0].finish_reason)
            except Exception:
                pass
            raise LLMError(f"Empty reply from model {reason}".strip())
        return text


class OpenAICompatLLM(BaseLLM):
    """Any /v1/chat/completions server (vLLM, llama.cpp server, Ollama's OpenAI endpoint)."""

    name = "openai"
    supports_images = False

    def __init__(self, base_url: str, api_key: str, model: str, timeout_s: float) -> None:
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout_s
        self._json_mode = True

    def _call(self, system: str, prompt: str, images: list[bytes]) -> str:
        import requests

        body = {
            "model": self.model, "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        }
        if self._json_mode:
            body["response_format"] = {"type": "json_object"}
        r = requests.post(f"{self.base_url}/chat/completions", json=body, timeout=self.timeout,
                          headers={"Authorization": f"Bearer {self.api_key}"})
        if r.status_code == 400 and self._json_mode:
            self._json_mode = False  # server does not support JSON mode; rely on validation
            return self._call(system, prompt, images)
        if r.status_code >= 400:
            err = LLMError(f"HTTP {r.status_code}: {r.text[:300]}")
            err.code = r.status_code  # type: ignore[attr-defined]
            raise err
        return r.json()["choices"][0]["message"]["content"]


_LLM: BaseLLM | None = None
_LLM_SET = False
_LLM_LOCK = threading.Lock()


def get_llm() -> BaseLLM | None:
    """The configured model, or None if models are off or no key is set."""
    global _LLM, _LLM_SET
    with _LLM_LOCK:
        if _LLM_SET:
            return _LLM
        s = get_settings()
        llm: BaseLLM | None = None
        try:
            if s.llm_provider == "gemini" and s.gemini_api_key:
                llm = GeminiLLM(s.gemini_api_key, s.gemini_model, s.llm_timeout)
            elif s.llm_provider == "openai":
                llm = OpenAICompatLLM(s.openai_base_url, s.openai_api_key, s.openai_model, s.llm_timeout)
        except Exception as exc:
            log.error("Could not start the model client: %s", exc)
            llm = None
        _LLM, _LLM_SET = llm, True
        return _LLM


def set_llm(llm: BaseLLM | None) -> None:
    """Inject a model (tests) or reset (None + reset_llm)."""
    global _LLM, _LLM_SET
    with _LLM_LOCK:
        _LLM, _LLM_SET = llm, True


def reset_llm() -> None:
    global _LLM, _LLM_SET
    with _LLM_LOCK:
        _LLM, _LLM_SET = None, False


def describe_llm() -> str:
    llm = get_llm()
    if llm is None:
        return "none"
    return f"{llm.name}:{llm.model}"
