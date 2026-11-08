"""Non-database tests of the executor's fail-closed gates and credential handling."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from clearquery_poc import executor as ex
from clearquery_poc import gold_sql

ROOT = Path(__file__).resolve().parents[1]

# Settings pointing nowhere: if a gate failed open, the connection attempt
# would surface as an error instead of a clean rejection.
NOWHERE = ex.DbSettings(host="127.0.0.1", port=1, dbname="none", user="none", password="x")


def test_unvalidated_sql_is_not_executed():
    r = ex.execute_readonly(ex.ValidatedSql("t-1", "SELECT 1", False, "sql_guard", "DDL_OR_DML"), NOWHERE)
    assert (r.validated, r.executed, r.rows, r.error) == (False, False, [], None)
    assert r.rejected_reason == "DDL_OR_DML"


def test_validator_not_enabled_in_phase_2a():
    r = ex.execute_readonly(ex.ValidatedSql("t-2", gold_sql.load("Q3"), True, "sql_guard"), NOWHERE)
    assert (r.validated, r.executed, r.rejected_reason) == (False, False, "VALIDATOR_NOT_ENABLED")


def test_forged_gold_fixture_is_rejected():
    forged = ex.ValidatedSql("t-3", "SELECT * FROM orders", True, ex.GOLD_FIXTURE_VALIDATOR)
    r = ex.execute_readonly(forged, NOWHERE)
    assert (r.validated, r.executed, r.rejected_reason) == (False, False, "NOT_A_GOLD_FIXTURE")


def test_missing_env_fails_closed():
    with pytest.raises(ex.ConfigError):
        ex.DbSettings.from_env({})


def test_password_not_in_repr():
    s = ex.DbSettings(host="h", port=1, dbname="d", user="u", password="s3cr3t-value")
    assert "s3cr3t-value" not in repr(s) and "password" not in repr(s)


def test_five_gold_fixtures_are_single_selects():
    fixtures = gold_sql.load_all()
    assert sorted(fixtures) == ["Q1", "Q2", "Q3", "Q4", "Q5"]
    for qid, text in fixtures.items():
        body = "\n".join(l for l in text.splitlines() if not l.strip().startswith("--"))
        assert body.lstrip().upper().startswith("SELECT"), qid
        assert ";" not in body, qid
        assert "sales_poc" not in body and "CURRENT_DATE" not in body.upper(), qid


def test_no_secret_values_in_project_files():
    secrets = [os.environ.get(k) for k in ("CLEARQUERY_ADMIN_PASSWORD", "CLEARQUERY_APP_PASSWORD")]
    secrets = [s for s in secrets if s]
    if not secrets:
        pytest.skip("no local secrets loaded")
    for d in ("src", "scripts", "db", "gold", "data", "tests", "reports"):
        for p in (ROOT / d).rglob("*"):
            if p.is_file() and p.suffix in {".py", ".sql", ".csv", ".json", ".md", ".txt", ""}:
                text = p.read_text(encoding="utf-8", errors="ignore")
                assert not any(s in text for s in secrets), p
