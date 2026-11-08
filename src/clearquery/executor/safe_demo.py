"""Explicit, one-shot execution capabilities for the verified synthetic demo.

Person 1's contracts remain unchanged. No raw-SQL execution entry point is
exposed here; every capability is issued and rechecked by the current Guard.
"""
from dataclasses import dataclass
import csv
from datetime import date
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
from time import monotonic, perf_counter

from clearquery.contracts import BIR, SqlCandidate
from clearquery.sql_guard import SqlGuard
from clearquery.decision import decide
from clearquery_poc.executor import DbSettings, ValidatedSql, SqlResult

ROOT = Path(__file__).resolve().parents[3]
_VIEWS = {
    "branches": "SELECT branch_id, branch_name FROM sales_poc.branches;",
    "orders": "SELECT order_id, branch_id, order_date, total_amount FROM sales_poc.orders;",
}


class SafetyError(RuntimeError):
    pass


def demo_settings():
    # Read existing local credentials without copying them or loading admin keys.
    for path in (ROOT / ".secrets/demo.env",):
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                key, sep, value = line.partition("=")
                if sep and key.strip() in DbSettings.ENV.values():
                    os.environ.setdefault(key.strip(), value.strip())
            break
    return DbSettings.from_env()


def _require(value, reason):
    if not value:
        raise SafetyError(reason)


def connect_demo(settings):
    import psycopg
    _require((settings.host, settings.port, settings.dbname, settings.user) ==
        ("127.0.0.1", 55432, "clearquery_demo", "clearquery_app_ro"), "DATABASE_IDENTITY_REJECTED")
    _require(1 <= settings.row_cap <= 1000 and 100 <= settings.statement_timeout_ms <= 5000,
        "DATABASE_LIMITS_REJECTED")
    conn = psycopg.connect(host=settings.host, port=settings.port, dbname=settings.dbname,
        user=settings.user, password=settings.password, connect_timeout=5,
        options=f"-c default_transaction_read_only=on -c search_path=analytics -c statement_timeout={settings.statement_timeout_ms}",
        application_name="clearquery_safe_synthetic_demo")
    conn.read_only = True
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    return conn


def preflight(conn, settings):
    """Read-only checks on the SAME connection/transaction used for execution."""
    identity = conn.execute("SELECT current_database(),current_user,host(inet_server_addr()),inet_server_port()").fetchone()
    _require(identity == ("clearquery_demo", "clearquery_app_ro", "127.0.0.1", 55432), "DATABASE_IDENTITY_REJECTED")
    flags = {k: conn.execute("SELECT current_setting(%s)", (k,)).fetchone()[0]
        for k in ("transaction_read_only", "default_transaction_read_only", "search_path", "statement_timeout")}
    _require(flags["transaction_read_only"] == flags["default_transaction_read_only"] == "on" and
        flags["search_path"] == "analytics", "DATABASE_SESSION_UNSAFE")
    timeout = conn.execute("SELECT setting::int FROM pg_settings WHERE name='statement_timeout'").fetchone()[0]
    _require(timeout == settings.statement_timeout_ms, "DATABASE_TIMEOUT_UNSAFE")
    roles = conn.execute("SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolcanlogin FROM pg_roles WHERE pg_has_role(current_user,oid,'MEMBER') ORDER BY rolname").fetchall()
    _require(roles == [("analyst_ro",False,False,False,False,False,False),
        ("clearquery_app_ro",False,False,False,False,False,True)], "DATABASE_ROLES_UNSAFE")
    _require(not conn.execute("SELECT has_database_privilege(current_user,current_database(),'CREATE') OR has_database_privilege(current_user,current_database(),'TEMP')").fetchone()[0], "DATABASE_CREATE_PRIVILEGE")
    _require(not conn.execute("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE has_schema_privilege(current_user,oid,'CREATE'))").fetchone()[0], "DATABASE_SCHEMA_CREATE_PRIVILEGE")
    objects = conn.execute("""SELECT n.nspname,c.relname,c.relkind,c.relowner=current_user::regrole,
        has_table_privilege(current_user,c.oid,'SELECT'),
        has_any_column_privilege(current_user,c.oid,'SELECT'),
        has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'),
        has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE,REFERENCES')
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%%'
        AND c.relkind IN ('r','v','m','p','f','S')""").fetchall()
    readable = set()
    for schema, name, kind, owner, select, column_select, write, column_write in objects:
        _require(not (owner or write or column_write), "DATABASE_WRITE_OR_OWNERSHIP_PRIVILEGE")
        if select or column_select:
            readable.add((schema,name,kind))
    _require(readable == {("analytics",name,"v") for name in _VIEWS}, "DATABASE_RELATION_ALLOWLIST")
    _require(not conn.execute("SELECT EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND has_function_privilege(current_user,p.oid,'EXECUTE'))").fetchone()[0], "DATABASE_CUSTOM_FUNCTION_ACCESS")
    for name, expected in _VIEWS.items():
        definition, comment = conn.execute("SELECT pg_get_viewdef(%s::regclass),obj_description(%s::regclass)",
            ("analytics."+name,"analytics."+name)).fetchone()
        _require("".join(definition.split()) == "".join(expected.split()) and comment.startswith("SYNTHETIC demo data."), "DATABASE_VIEW_IDENTITY")
    # Verify every exposed row against SHA-verified, frozen synthetic fixtures.
    sums = dict(line.split(maxsplit=1)[::-1] for line in (ROOT/"data/SHA256SUMS").read_text().splitlines() if line.strip())
    counts = {}
    for name in _VIEWS:
        path = ROOT / "data" / (name+".csv")
        _require(hashlib.sha256(path.read_bytes()).hexdigest() == sums[path.name], "SYNTHETIC_FIXTURE_HASH_MISMATCH")
        with path.open(encoding="utf-8",newline="") as source:
            rows = list(csv.DictReader(source))
        if name == "branches":
            expected = [(int(r["branch_id"]),r["branch_name"]) for r in rows]
            sql = "SELECT branch_id,branch_name FROM analytics.branches ORDER BY branch_id"
        else:
            expected = [(int(r["order_id"]),int(r["branch_id"]),date.fromisoformat(r["order_date"]),Decimal(r["total_amount"])) for r in rows]
            sql = "SELECT order_id,branch_id,order_date,total_amount FROM analytics.orders ORDER BY order_id"
        actual = conn.execute(sql,prepare=True).fetchmany(1001)
        _require(actual == expected and len(actual) <= 1000, "DATABASE_NOT_VERIFIED_SYNTHETIC")
        counts[name] = len(actual)
    return {"verified":True,"database":identity[0],"login":identity[1],"grant_role":"analyst_ro",
        "host":identity[2],"port":identity[3],"transaction":flags,"synthetic_rows":counts,
        "row_cap":settings.row_cap,"statement_timeout_ms":timeout,"write_privileges":False}


@dataclass(frozen=True)
class Approval:
    token: str
    trace_id: str


class ControlledExecutor:
    TTL_SECONDS = 300

    def __init__(self):
        self._issued = {}
        self._lock = threading.Lock()

    def approve(self, candidate: SqlCandidate, bir: BIR):
        candidate = SqlCandidate.model_validate(candidate.model_dump()).model_copy(deep=True)
        bir = BIR.model_validate(bir.model_dump()).model_copy(deep=True)
        _require(candidate.trace_id == bir.trace_id and not candidate.error, "CANDIDATE_TRACE_OR_ERROR")
        _require(decide(bir).decision == "ANSWER", "EXECUTION_POLICY_REJECTED")
        validation = SqlGuard().review(candidate.candidate_sql, bir)
        _require(validation.validated and validation.trace_id == candidate.trace_id and
            validation.reviewed_sql == candidate.candidate_sql, "CURRENT_GUARD_REJECTED")
        approval = Approval(secrets.token_urlsafe(32),candidate.trace_id)
        with self._lock:
            self._issued = {key:value for key,value in self._issued.items() if monotonic()-value[3] <= self.TTL_SECONDS}
            self._issued[approval.token] = (approval,candidate,bir,monotonic())
        return approval

    def execute(self, approval, settings=None):
        started = perf_counter()
        _require(type(approval) is Approval, "EXECUTION_PROVENANCE_REJECTED")
        with self._lock:
            stored = self._issued.pop(approval.token,None)
        _require(stored is not None and stored[0] is approval, "EXECUTION_PROVENANCE_REJECTED")
        _, candidate, bir, issued = stored
        res = SqlResult(trace_id=approval.trace_id,sql=candidate.candidate_sql,validated=False)
        _require(monotonic()-issued <= self.TTL_SECONDS, "VALIDATION_EXPIRED")
        validation = SqlGuard().review(candidate.candidate_sql,bir)
        _require(validation.validated and validation.trace_id == approval.trace_id and
            validation.reviewed_sql == candidate.candidate_sql,"EXECUTION_GUARD_REJECTED")
        request = ValidatedSql(approval.trace_id,candidate.candidate_sql,True,"sql_guard")
        # The preserved Person 1 contract is constructed only inside this boundary.
        import psycopg
        try:
            settings = settings or demo_settings()
            with connect_demo(settings) as conn:
                try:
                    audit = preflight(conn,settings)
                    res.validated = True
                    with conn.cursor() as cursor:
                        cursor.execute(request.sql,prepare=True)
                        res.executed = True
                        res.columns = [column.name for column in cursor.description]
                        rows = cursor.fetchmany(settings.row_cap+1)
                        res.truncated = len(rows)>settings.row_cap
                        res.rows = [list(row) for row in rows[:settings.row_cap]]
                        res.row_count = len(res.rows)
                finally:
                    conn.rollback()
        except SafetyError as exc:
            res.error = {"code":"DB_PREFLIGHT_FAILED","message":str(exc)}
        except psycopg.errors.QueryCanceled:
            res.error = {"code":"DB_TIMEOUT","message":"Read-only query exceeded the configured timeout."}
        except Exception:
            # Database exception text can contain connection credentials/DSNs.
            res.error = {"code":"DB_ERROR","message":"Could not complete the local read-only database query."}
        if res.error:
            res.rows,res.row_count = [],0
        res.elapsed_ms = round((perf_counter()-started)*1000,2)
        return res
