"""Database integrity, boundary and metric tests (run as the demo DB owner,
every transaction rolled back)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from clearquery_poc import generator as g

pytestmark = pytest.mark.db


def one(conn, sql, params=None):
    return conn.execute(sql, params).fetchone()[0]


def test_exact_counts(admin_conn):
    assert one(admin_conn, "SELECT count(*) FROM sales_poc.branches") == 4
    assert one(admin_conn, "SELECT count(*) FROM sales_poc.orders") == 50
    assert one(admin_conn, "SELECT count(*) FROM analytics.branches") == 4
    assert one(admin_conn, "SELECT count(*) FROM analytics.orders") == 50


def test_database_matches_generator(admin_conn, dataset):
    branches, orders = dataset
    db_b = admin_conn.execute("SELECT branch_id, branch_name, branch_name_ar FROM sales_poc.branches "
                              "ORDER BY branch_id").fetchall()
    assert db_b == [(b.branch_id, b.branch_name, b.branch_name_ar) for b in branches]
    db_o = admin_conn.execute("SELECT order_id, branch_id, order_date, total_amount FROM sales_poc.orders "
                              "ORDER BY order_id").fetchall()
    assert db_o == [(o.order_id, o.branch_id, o.order_date, o.total_amount) for o in orders]


def test_primary_keys_unique_and_no_orphans(admin_conn):
    assert one(admin_conn, "SELECT count(*) - count(DISTINCT order_id) FROM sales_poc.orders") == 0
    assert one(admin_conn, "SELECT count(*) - count(DISTINCT branch_id) FROM sales_poc.branches") == 0
    assert one(admin_conn, "SELECT count(*) FROM sales_poc.orders o "
                           "LEFT JOIN sales_poc.branches b USING (branch_id) WHERE b.branch_id IS NULL") == 0
    assert one(admin_conn, "SELECT count(*) FROM sales_poc.orders WHERE total_amount < 0 "
                           "OR total_amount IS NULL OR order_date IS NULL") == 0


def test_declared_constraints_exist(admin_conn):
    rows = admin_conn.execute(
        "SELECT conname, contype FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = 'sales_poc' ORDER BY conname").fetchall()
    names = {r[0]: r[1] for r in rows}
    assert names["branches_pk"] == "p" and names["orders_pk"] == "p"
    assert names["orders_branch_fk"] == "f"
    assert names["branches_name_uq"] == "u" and names["branches_name_ar_uq"] == "u"
    for chk in ("orders_amount_nonneg", "orders_date_in_demo_window", "orders_id_positive"):
        assert names[chk] == "c"
    idx = {r[0] for r in admin_conn.execute(
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'sales_poc'").fetchall()}
    assert {"orders_branch_id_idx", "orders_order_date_idx"} <= idx


@pytest.mark.parametrize("bad_insert, sqlstate", [
    ("INSERT INTO sales_poc.orders VALUES (999, 99, DATE '2026-10-05', 10)", "23503"),    # FK
    ("INSERT INTO sales_poc.orders VALUES (1, 1, DATE '2026-10-05', 10)", "23505"),       # PK
    ("INSERT INTO sales_poc.orders VALUES (999, 1, DATE '2026-10-05', -1)", "23514"),     # amount
    ("INSERT INTO sales_poc.orders VALUES (999, 1, DATE '2026-11-02', 10)", "23514"),     # date window
    ("DELETE FROM sales_poc.branches WHERE branch_id = 1", "23001"),                       # RESTRICT
])
def test_constraints_reject_invalid_rows(admin_conn, bad_insert, sqlstate):
    import psycopg
    with pytest.raises(psycopg.Error) as exc:
        admin_conn.execute(bad_insert)
    assert exc.value.sqlstate == sqlstate
    admin_conn.rollback()


def test_order_date_is_date_type(admin_conn):
    assert one(admin_conn, "SELECT data_type FROM information_schema.columns WHERE table_schema='analytics' "
                           "AND table_name='orders' AND column_name='order_date'") == "date"


def test_date_boundaries(admin_conn):
    def count(lo, hi):
        return one(admin_conn, "SELECT count(*) FROM analytics.orders WHERE order_date >= %s AND order_date < %s",
                   (lo, hi))
    assert count(date(2026, 8, 1), date(2026, 9, 1)) == 8
    assert count(date(2026, 9, 1), date(2026, 10, 1)) == 15
    assert count(date(2026, 10, 1), date(2026, 11, 1)) == 26
    assert count(date(2026, 11, 1), date(2026, 12, 1)) == 1
    assert one(admin_conn, "SELECT count(*) FROM analytics.orders WHERE order_date > DATE '2026-11-01'") == 0
    for d in ("2026-09-30", "2026-10-01", "2026-10-31", "2026-11-01"):
        assert one(admin_conn, "SELECT count(*) FROM analytics.orders WHERE order_date = %s", (d,)) >= 1, d
    # A closed interval would wrongly include the 2026-11-01 boundary row.
    assert one(admin_conn, "SELECT count(*) FROM analytics.orders WHERE order_date BETWEEN "
                           "DATE '2026-10-01' AND DATE '2026-11-01'") == 27


def test_metric_calculations_match_independent_recomputation(admin_conn, dataset):
    _, orders = dataset
    for (y, m) in ((2026, 8), (2026, 9), (2026, 10), (2026, 11)):
        start, end = g.month_range(y, m)
        expected = g.metrics(orders, start, end)
        rows = admin_conn.execute(
            "SELECT b.branch_id, COUNT(o.order_id), COALESCE(SUM(o.total_amount), 0) "
            "FROM analytics.branches b LEFT JOIN analytics.orders o ON o.branch_id = b.branch_id "
            "AND o.order_date >= %s AND o.order_date < %s GROUP BY b.branch_id ORDER BY b.branch_id",
            (start, end)).fetchall()
        assert {r[0]: (r[1], Decimal(r[2])) for r in rows} == expected, (y, m)


def test_october_rankings(admin_conn):
    rows = admin_conn.execute(
        "SELECT b.branch_name, COUNT(o.order_id) AS n, SUM(o.total_amount) AS s "
        "FROM analytics.orders o JOIN analytics.branches b USING (branch_id) "
        "WHERE o.order_date >= DATE '2026-10-01' AND o.order_date < DATE '2026-11-01' "
        "GROUP BY b.branch_name").fetchall()
    by_count = sorted(rows, key=lambda r: r[1], reverse=True)
    by_sales = sorted(rows, key=lambda r: r[2], reverse=True)
    assert by_count[0][0] == "Nasr City" and by_count[0][1] > by_count[1][1]
    assert by_sales[0][0] == "Maadi" and by_sales[0][2] > by_sales[1][2]


def test_views_expose_only_approved_columns(admin_conn):
    cols = admin_conn.execute(
        "SELECT table_name, string_agg(column_name, ',' ORDER BY ordinal_position) "
        "FROM information_schema.columns WHERE table_schema = 'analytics' GROUP BY table_name "
        "ORDER BY table_name").fetchall()
    assert cols == [("branches", "branch_id,branch_name"),
                    ("orders", "order_id,branch_id,order_date,total_amount")]
    assert one(admin_conn, "SELECT count(*) FROM information_schema.columns WHERE table_schema='sales_poc' "
                           "AND table_name='branches' AND column_name='branch_name_ar'") == 1
