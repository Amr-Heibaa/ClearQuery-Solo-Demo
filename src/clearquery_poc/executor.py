"""Person 1 read-only database execution component (contract owner: SqlResult).

Scope (Phase 2a):
* This module does NOT validate SQL. SQL validation (single SELECT, object and
  function allowlist, BIR<->SQL consistency) belongs to Person 3's SQL Guard
  and is NOT implemented here.
* The executor only runs SQL that arrives as a ``ValidatedSql`` with
  ``validated=True`` from an *enabled* validator. In Phase 2a the only enabled
  validator is ``gold_fixture``, which accepts nothing but the exact text of an
  approved Gold SQL fixture. Model-generated SQL can therefore never reach the
  database in this phase.
* Defence in depth on every execution: read-only transaction, server-side
  ``statement_timeout``, ``search_path=analytics``, row cap, always ROLLBACK,
  server-side prepared statement (rejects multi-statement strings), and the database
  role itself has no write privileges.
* Credentials come ONLY from environment variables (see ``DbSettings``).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from . import CONTRACT_VERSION, gold_sql

GOLD_FIXTURE_VALIDATOR = "gold_fixture"
DEFAULT_ALLOWED_VALIDATORS = frozenset({GOLD_FIXTURE_VALIDATOR})


class ConfigError(RuntimeError):
    """Raised when required environment variables are missing."""


@dataclass(frozen=True)
class DbSettings:
    host: str
    port: int
    dbname: str
    user: str
    password: str = field(repr=False)
    statement_timeout_ms: int = 5000
    row_cap: int = 1000

    ENV = {
        "host": "CLEARQUERY_DB_HOST",
        "port": "CLEARQUERY_DB_PORT",
        "dbname": "CLEARQUERY_DB_NAME",
        "user": "CLEARQUERY_APP_USER",
        "password": "CLEARQUERY_APP_PASSWORD",
    }

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> "DbSettings":
        env = os.environ if environ is None else environ
        missing = [v for v in cls.ENV.values() if not env.get(v)]
        if missing:
            raise ConfigError("missing environment variables: " + ", ".join(missing))
        return cls(
            host=env[cls.ENV["host"]],
            port=int(env[cls.ENV["port"]]),
            dbname=env[cls.ENV["dbname"]],
            user=env[cls.ENV["user"]],
            password=env[cls.ENV["password"]],
            statement_timeout_ms=int(env.get("CLEARQUERY_STATEMENT_TIMEOUT_MS", "5000")),
            row_cap=int(env.get("CLEARQUERY_ROW_CAP", "1000")),
        )


@dataclass(frozen=True)
class ValidatedSql:
    """What the executor accepts. In Phase 2b this is produced by Person 3's
    SQL Guard (its ValidationResult); in Phase 2a only by ``gold_fixture``."""
    trace_id: str
    sql: str
    validated: bool
    validator: str
    rejected_reason: str | None = None


@dataclass
class SqlResult:
    """Contract object ``SqlResult`` (INTERFACE_CONTRACT_DRAFT §3.6) plus a
    proposed ``error`` field for DB_TIMEOUT / DB_ERROR (contract note needed)."""
    trace_id: str
    sql: str
    validated: bool
    rejected_reason: str | None = None
    rows: list[list[Any]] = field(default_factory=list)
    row_count: int = 0
    columns: list[str] = field(default_factory=list)
    elapsed_ms: float | None = None
    executed: bool = False
    truncated: bool = False
    error: dict[str, str] | None = None
    contract_version: str = CONTRACT_VERSION

    def to_contract_dict(self) -> dict[str, Any]:
        def enc(v: Any) -> Any:
            if isinstance(v, Decimal):
                return str(v)
            if isinstance(v, date):
                return v.isoformat()
            return v
        return {
            "contract_version": self.contract_version,
            "trace_id": self.trace_id,
            "sql": self.sql,
            "validated": self.validated,
            "rejected_reason": self.rejected_reason,
            "rows": [[enc(v) for v in r] for r in self.rows],
            "row_count": self.row_count,
            "columns": list(self.columns),
            "elapsed_ms": self.elapsed_ms,
            "executed": self.executed,
            "truncated": self.truncated,
            "error": self.error,
        }


def gold_fixture_request(qid: str, trace_id: str) -> ValidatedSql:
    """Phase 2a test-only source of executable SQL: an approved Gold fixture."""
    return ValidatedSql(trace_id=trace_id, sql=gold_sql.load(qid), validated=True,
                        validator=GOLD_FIXTURE_VALIDATOR)


def _is_gold_fixture(sql: str) -> bool:
    return sql in gold_sql.load_all().values()


def execute_readonly(request: ValidatedSql, settings: DbSettings | None = None, *,
                     allowed_validators: frozenset[str] = DEFAULT_ALLOWED_VALIDATORS) -> SqlResult:
    """Execute one validated SELECT with the read-only role. Never raises for
    rejection or database errors: they are reported in the returned SqlResult
    (fail closed: no rows, visible error)."""
    res = SqlResult(trace_id=request.trace_id, sql=request.sql, validated=False)

    if not request.validated:
        res.rejected_reason = request.rejected_reason or "NOT_VALIDATED"
        return res
    if request.validator not in allowed_validators:
        res.rejected_reason = "VALIDATOR_NOT_ENABLED"
        return res
    if request.validator == GOLD_FIXTURE_VALIDATOR and not _is_gold_fixture(request.sql):
        res.rejected_reason = "NOT_A_GOLD_FIXTURE"
        return res
    res.validated = True

    try:
        settings = settings or DbSettings.from_env()
    except ConfigError as exc:
        res.error = {"code": "DB_ERROR", "message": str(exc)}
        return res

    import psycopg  # imported lazily so non-DB tests need no driver
    from psycopg import errors

    options = (f"-c search_path=analytics -c default_transaction_read_only=on "
               f"-c statement_timeout={settings.statement_timeout_ms}")
    start = time.perf_counter()
    try:
        with psycopg.connect(host=settings.host, port=settings.port, dbname=settings.dbname,
                             user=settings.user, password=settings.password,
                             options=options, connect_timeout=5,
                             application_name="clearquery_poc_executor") as conn:
            conn.read_only = True
            try:
                with conn.cursor() as cur:
                    # prepare=True forces a server-side prepared statement:
                    # PostgreSQL rejects more than one command per string.
                    cur.execute(request.sql, prepare=True)
                    res.executed = True
                    if cur.description is None:
                        res.error = {"code": "DB_ERROR", "message": "statement returned no result set"}
                        return res
                    res.columns = [c.name for c in cur.description]
                    rows = cur.fetchmany(settings.row_cap + 1)
                    res.truncated = len(rows) > settings.row_cap
                    res.rows = [list(r) for r in rows[: settings.row_cap]]
                    res.row_count = len(res.rows)
            finally:
                conn.rollback()
    except errors.QueryCanceled as exc:
        res.rows, res.row_count = [], 0
        res.error = {"code": "DB_TIMEOUT", "message": str(exc).strip()}
    except psycopg.Error as exc:
        res.rows, res.row_count = [], 0
        res.error = {"code": "DB_ERROR", "message": str(exc).strip()}
    finally:
        res.elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    return res
