"""Read-only role restrictions (rule R1) and the second safety layer (R5):
even if a guard were bypassed, the database refuses writes, DDL and access
to non-allowlisted objects. These crafted statements are hand-written attack
fixtures, NOT model-generated SQL."""

from __future__ import annotations

import os

import psycopg
import pytest

from clearquery_poc import executor as ex

pytestmark = pytest.mark.db

DENIED = "42501"  # insufficient_privilege
READ_ONLY_TXN = "25006"


def counts(admin_conn):
    return admin_conn.execute("SELECT (SELECT count(*) FROM sales_poc.branches), "
                              "(SELECT count(*) FROM sales_poc.orders), "
                              "(SELECT sum(total_amount) FROM sales_poc.orders)").fetchone()


def test_role_attributes(admin_conn):
    row = admin_conn.execute(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, rolcanlogin "
        "FROM pg_roles WHERE rolname = 'clearquery_app_ro'").fetchone()
    assert row == (False, False, False, False, False, True)
    assert admin_conn.execute("SELECT rolcanlogin FROM pg_roles WHERE rolname='analyst_ro'").fetchone() == (False,)


def test_role_has_only_select_on_allowlisted_views(admin_conn):
    grants = admin_conn.execute(
        "SELECT table_schema, table_name, privilege_type FROM information_schema.role_table_grants "
        "WHERE grantee IN ('analyst_ro', 'clearquery_app_ro') ORDER BY 1, 2, 3").fetchall()
    assert grants == [("analytics", "branches", "SELECT"), ("analytics", "orders", "SELECT")]


def test_select_on_views_allowed(app_conn):
    assert app_conn.execute("SELECT count(*) FROM orders").fetchone() == (50,)        # via search_path
    assert app_conn.execute("SELECT count(*) FROM analytics.branches").fetchone() == (4,)
    assert app_conn.execute("SHOW default_transaction_read_only").fetchone() == ("on",)


@pytest.mark.parametrize("stmt, expected", [
    ("SELECT * FROM sales_poc.orders", DENIED),
    ("SELECT * FROM sales_poc.branches", DENIED),
    # DML on views: PostgreSQL checks privileges before the read-only flag.
    ("INSERT INTO analytics.orders VALUES (999, 1, DATE '2026-10-05', 1)", DENIED),
    ("UPDATE analytics.orders SET total_amount = 0", DENIED),
    ("DELETE FROM analytics.orders", DENIED),
    # DDL utility commands: rejected by the read-only transaction first.
    ("DROP VIEW analytics.orders", READ_ONLY_TXN),
    ("CREATE TABLE analytics.x (a int)", READ_ONLY_TXN),
    ("TRUNCATE sales_poc.orders", READ_ONLY_TXN),
])
def test_writes_and_hidden_objects_refused(app_conn, admin_conn, stmt, expected):
    before = counts(admin_conn)
    with pytest.raises(psycopg.Error) as exc:
        app_conn.execute(stmt)
    assert exc.value.sqlstate == expected, exc.value
    assert counts(admin_conn) == before


@pytest.mark.parametrize("stmt", [
    "INSERT INTO analytics.orders VALUES (999, 1, DATE '2026-10-05', 1)",
    "UPDATE analytics.orders SET total_amount = 0",
    "DELETE FROM analytics.orders",
    "DELETE FROM sales_poc.orders",
    "CREATE TABLE analytics.x (a int)",
    "CREATE TABLE public.x (a int)",
    "CREATE TEMP TABLE x (a int)",
    "DROP VIEW analytics.orders",
    "TRUNCATE sales_poc.orders",
    "ALTER VIEW analytics.orders RENAME TO y",
    "GRANT SELECT ON sales_poc.orders TO clearquery_app_ro",
])
def test_privileges_deny_even_after_disabling_read_only(app_conn, admin_conn, stmt):
    """Session can turn the read-only default off; privileges must still deny."""
    before = counts(admin_conn)
    app_conn.execute("SET default_transaction_read_only = off")
    with pytest.raises(psycopg.Error) as exc:
        app_conn.execute(stmt)
    assert exc.value.sqlstate == DENIED, exc.value
    assert counts(admin_conn) == before


@pytest.mark.parametrize("stmt", [
    "SET ROLE clearquery_demo_admin",
    "SELECT pg_read_file('postgresql.conf')",
    "COPY (SELECT 1) TO PROGRAM 'cmd /c echo x'",
    "CREATE ROLE intruder",
    "CREATE DATABASE intruder",
])
def test_escalation_attempts_refused(app_conn, stmt):
    app_conn.execute("SET default_transaction_read_only = off")
    with pytest.raises(psycopg.Error) as exc:
        app_conn.execute(stmt)
    assert exc.value.sqlstate == DENIED, exc.value


def test_cannot_connect_to_other_databases():
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(host=os.environ["CLEARQUERY_DB_HOST"], port=int(os.environ["CLEARQUERY_DB_PORT"]),
                        dbname="postgres", user=os.environ["CLEARQUERY_APP_USER"],
                        password=os.environ["CLEARQUERY_APP_PASSWORD"], connect_timeout=5)


# --- executor-level defence in depth (guard bypass simulation) -------------

BYPASS = frozenset({"bypass_test"})  # test-only validator name; never enabled by default


@pytest.mark.parametrize("sql, code_fragment", [
    ("DELETE FROM orders", "permission denied"),
    ("SELECT 1; DELETE FROM orders", "multiple commands"),
    ("WITH d AS (DELETE FROM orders RETURNING *) SELECT count(*) FROM d", "permission denied"),
    ("SELECT * FROM sales_poc.orders", "permission denied"),
    ("SET default_transaction_read_only = off", ""),
])
def test_executor_fails_closed_when_guard_is_bypassed(admin_conn, sql, code_fragment):
    before = counts(admin_conn)
    res = ex.execute_readonly(ex.ValidatedSql("t-bypass", sql, True, "bypass_test"),
                              allowed_validators=BYPASS)
    assert res.rows == [] and res.row_count == 0
    assert res.error is not None, res
    assert res.error["code"] == "DB_ERROR"
    assert code_fragment in res.error["message"], res.error
    assert counts(admin_conn) == before


def test_executor_statement_timeout(admin_conn):
    settings = ex.DbSettings.from_env()
    settings = ex.DbSettings(**{**settings.__dict__, "statement_timeout_ms": 200})
    res = ex.execute_readonly(ex.ValidatedSql("t-timeout", "SELECT pg_sleep(2)", True, "bypass_test"),
                              settings, allowed_validators=BYPASS)
    assert res.error and res.error["code"] == "DB_TIMEOUT", res
    assert res.rows == []


def test_executor_row_cap():
    settings = ex.DbSettings.from_env()
    settings = ex.DbSettings(**{**settings.__dict__, "row_cap": 10})
    res = ex.execute_readonly(ex.ValidatedSql("t-cap", "SELECT * FROM orders ORDER BY order_id", True,
                                              "bypass_test"), settings, allowed_validators=BYPASS)
    assert res.error is None and res.row_count == 10 and res.truncated
