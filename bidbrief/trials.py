"""Trial access codes and package ownership.

* Codes are set by you, the owner, in the environment:
      BIDBRIEF_TRIAL_CODES="SEAN-7Q4K:3,DEMO-22XP:2"      (code:number of packages)
  Generate a code with:  python -m bidbrief new-code
* If no codes are set, uploads are open (handy on SCC); set codes before you
  share a public link.
* A package someone uploads with a code is visible only to people who enter
  that same code. The pre-loaded public packages are visible to everyone.
* A trial is used only when a package finishes processing. If processing
  fails, the trial is given back.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import threading
from pathlib import Path

from .config import get_settings

_LOCK = threading.Lock()
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I


def configured_codes() -> dict[str, int]:
    raw = os.environ.get("BIDBRIEF_TRIAL_CODES", "").strip()
    out: dict[str, int] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        code, _, n = part.partition(":")
        code = normalize_code(code)
        try:
            uses = max(0, int(n)) if n.strip() else 3
        except ValueError:
            uses = 3
        if code:
            out[code] = uses
    return out


def trial_required() -> bool:
    return bool(configured_codes())


def normalize_code(code: str | None) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum() or ch == "-").strip("-")


def code_hash(code: str) -> str:
    salt = os.environ.get("BIDBRIEF_SECRET", "bidbrief-v1")
    return hashlib.sha256(f"{salt}:{normalize_code(code)}".encode()).hexdigest()[:24]


def new_code(prefix: str = "") -> str:
    body = "".join(secrets.choice(_ALPHABET) for _ in range(4)) + "-" + "".join(
        secrets.choice(_ALPHABET) for _ in range(4))
    return f"{normalize_code(prefix)}-{body}" if prefix else body


def _usage_path() -> Path:
    return get_settings().data_dir / "trials.json"


def _read_usage() -> dict[str, int]:
    p = _usage_path()
    if not p.exists():
        return {}
    try:
        return {k: int(v) for k, v in json.loads(p.read_text()).items()}
    except Exception:
        return {}


def _write_usage(usage: dict[str, int]) -> None:
    p = _usage_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(usage, indent=1))
    tmp.replace(p)


def is_valid(code: str | None) -> bool:
    return normalize_code(code) in configured_codes()


def remaining(code: str | None) -> int:
    codes = configured_codes()
    c = normalize_code(code)
    if c not in codes:
        return 0
    with _LOCK:
        used = _read_usage().get(code_hash(c), 0)
    return max(0, codes[c] - used)


def consume(code: str | None) -> bool:
    """Reserve one trial. Returns False if none are left."""
    codes = configured_codes()
    c = normalize_code(code)
    if c not in codes:
        return False
    with _LOCK:
        usage = _read_usage()
        h = code_hash(c)
        if usage.get(h, 0) >= codes[c]:
            return False
        usage[h] = usage.get(h, 0) + 1
        _write_usage(usage)
    return True


def refund(code: str | None) -> None:
    c = normalize_code(code)
    if not c:
        return
    with _LOCK:
        usage = _read_usage()
        h = code_hash(c)
        if usage.get(h, 0) > 0:
            usage[h] -= 1
            _write_usage(usage)


# ------------------------------------------------------------- ownership
def _owner_file(package_id: str) -> Path:
    return get_settings().results_dir / package_id / "owner.json"


def set_owner(package_id: str, code: str | None) -> None:
    p = _owner_file(package_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"owner": code_hash(code) if code else None, "uploaded": True}))


def owner_info(package_id: str) -> dict | None:
    p = _owner_file(package_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return {"owner": "unknown", "uploaded": True}


def can_view(package_id: str, code: str | None) -> bool:
    info = owner_info(package_id)
    if info is None or info.get("owner") is None:
        return True  # pre-loaded package, or uploaded while uploads were open
    return bool(code) and info.get("owner") == code_hash(code)


def is_mine(package_id: str, code: str | None) -> bool:
    info = owner_info(package_id)
    if info is None:
        return False
    if info.get("owner") is None:
        return True  # open mode: whoever uploaded can delete
    return bool(code) and info.get("owner") == code_hash(code)


def delete_package(package_id: str) -> None:
    s = get_settings()
    for root in (s.packages_dir, s.work_dir, s.results_dir):
        target = (root / package_id).resolve()
        if root.resolve() in target.parents and target.exists():
            shutil.rmtree(target, ignore_errors=True)


def usage_report() -> list[tuple[str, int, int]]:
    codes = configured_codes()
    usage = _read_usage()
    return [(c, usage.get(code_hash(c), 0), n) for c, n in codes.items()]
