"""Shared fixtures: a temporary data folder with the synthetic sample package,
processed once with the test model (no network, no API key needed)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("bidbrief_data")
    env = {
        "BIDBRIEF_DATA_DIR": str(d), "BIDBRIEF_EMBEDDINGS": "0", "BIDBRIEF_RERANK": "0",
        "LLM_CACHE": "0", "LLM_RPM": "0", "BIDBRIEF_LLM": "none",
    }
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    from bidbrief.config import reset_settings
    from bidbrief.llm import reset_llm

    reset_settings()
    reset_llm()
    from bidbrief import sample

    info = sample.build(d / "packages" / sample.PACKAGE_ID)
    sample.write_answer_key(d / "answer_keys" / sample.PACKAGE_ID, info)
    yield d
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    reset_settings()
    reset_llm()


@pytest.fixture(scope="session")
def ingested(data_dir):
    from bidbrief.ingest import ingest_package, save_ingested
    from bidbrief.sample import PACKAGE_ID

    work = data_dir / "work" / PACKAGE_ID
    ing = ingest_package(PACKAGE_ID, data_dir / "packages" / PACKAGE_ID, work)
    save_ingested(ing, work)
    return ing


@pytest.fixture(scope="session")
def processed(data_dir):
    from fake_llm import FakeLLM

    from bidbrief.llm import reset_llm, set_llm
    from bidbrief.pipeline import process_package
    from bidbrief.sample import PACKAGE_ID

    fake = FakeLLM()
    set_llm(fake)
    res = process_package(data_dir / "packages" / PACKAGE_ID, PACKAGE_ID)
    yield res, fake
    reset_llm()


@pytest.fixture()
def store(ingested):
    from bidbrief.citations import PageStore

    s = PageStore(ingested.working)
    yield s
    s.close()
