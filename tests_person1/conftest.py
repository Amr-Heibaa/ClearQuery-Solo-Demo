from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import _env  # noqa: E402

_env.load()

DB_ENV = ("CLEARQUERY_DB_HOST", "CLEARQUERY_DB_PORT", "CLEARQUERY_DB_NAME",
          "CLEARQUERY_ADMIN_USER", "CLEARQUERY_ADMIN_PASSWORD",
          "CLEARQUERY_APP_USER", "CLEARQUERY_APP_PASSWORD")


def _db_configured() -> bool:
    return all(os.environ.get(k) for k in DB_ENV)


def pytest_collection_modifyitems(config, items):
    if _db_configured():
        return
    skip = pytest.mark.skip(reason="demo DB environment variables not set")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


def _connect(user_key: str, pw_key: str, **kw):
    import psycopg
    return psycopg.connect(host=os.environ["CLEARQUERY_DB_HOST"],
                           port=int(os.environ["CLEARQUERY_DB_PORT"]),
                           dbname=os.environ["CLEARQUERY_DB_NAME"],
                           user=os.environ[user_key], password=os.environ[pw_key], **kw)


@pytest.fixture
def admin_conn():
    """Demo DB owner. Every test transaction is rolled back."""
    conn = _connect("CLEARQUERY_ADMIN_USER", "CLEARQUERY_ADMIN_PASSWORD")
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def app_conn():
    """The application read-only role, autocommit so each statement stands alone."""
    conn = _connect("CLEARQUERY_APP_USER", "CLEARQUERY_APP_PASSWORD", autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture(scope="session")
def dataset():
    from clearquery_poc import generator
    return generator.generate()
