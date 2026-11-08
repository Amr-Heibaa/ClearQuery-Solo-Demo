"""Narrow join equivalence tests; SQL text and all other shapes stay strict."""
import pytest
from clearquery.intent import parse
from clearquery.sql_guard import SqlGuard, reference_sql


def bir():
    return parse("فرع المعادي باع بكام في أكتوبر 2026؟","join-unit")[0]


def reversed_sql():
    return reference_sql(bir()).replace("ON b.branch_id = o.branch_id", "ON o.branch_id = b.branch_id")


@pytest.mark.parametrize("transform",[
    lambda s:s,
    lambda s:s.replace("JOIN branches", "INNER JOIN branches"),
    lambda s:s.replace("FROM orders", "FROM analytics.orders").replace("JOIN branches", "JOIN analytics.branches"),
    lambda s:s.replace("orders o", "orders ord").replace("branches b", "branches br").replace("o.", "ord.").replace("b.", "br."),
    # Misleading alias letters still resolve to the real table identities.
    lambda s:s.replace("orders o", "orders b").replace("branches b", "branches o").replace("o.", "TMP.").replace("b.", "o.").replace("TMP.", "b."),
])
def test_only_real_branch_id_join_is_equivalent_and_text_exact(transform):
    sql = " \n" + transform(reversed_sql()) + ";\n "
    result = SqlGuard().review(sql,bir())
    assert result.validated, result
    assert result.reviewed_sql.encode() == sql.encode()


@pytest.mark.parametrize("old,new",[
    ("o.branch_id = b.branch_id","o.order_id = b.branch_id"),
    ("o.branch_id = b.branch_id","o.branch_id = b.branch_name"),
    ("o.branch_id = b.branch_id","o.branch_id = o.branch_id"),
    ("o.branch_id = b.branch_id","b.branch_id = b.branch_id"),
    ("o.branch_id = b.branch_id","o.branch_id <> b.branch_id"),
    ("o.branch_id = b.branch_id","o.branch_id IS NOT DISTINCT FROM b.branch_id"),
    ("o.branch_id = b.branch_id","o.branch_id::integer = b.branch_id"),
    ("o.branch_id = b.branch_id","o.branch_id = b.branch_id AND TRUE"),
    ("o.branch_id = b.branch_id","o.branch_id = b.branch_id OR TRUE"),
    ("o.branch_id = b.branch_id","unknown.branch_id = b.branch_id"),
    ("JOIN branches","LEFT JOIN branches"),
    ("JOIN branches","RIGHT JOIN branches"),
    ("JOIN branches","FULL JOIN branches"),
    ("JOIN branches","CROSS JOIN branches"),
    ("JOIN branches","JOIN public.branches"),
    ("FROM orders","FROM public.orders"),
    ("JOIN branches","JOIN customers"),
    ("JOIN branches b ON o.branch_id = b.branch_id","NATURAL JOIN branches b"),
    ("JOIN branches b ON o.branch_id = b.branch_id","JOIN branches b USING (branch_id)"),
    ("FROM orders o JOIN branches b","FROM branches b JOIN orders o"),
    ("'Maadi'","'Nasr City'"),
    ("2026-10-01","2026-09-01"),
    ("2026-11-01","2027-11-01"),
    ("SUM(o.total_amount)","COUNT(o.order_id)"),
    ("COALESCE(SUM(o.total_amount), 0)","SUM(o.total_amount)"),
    ("SELECT ","SELECT 42, "),
    ("b.branch_name = 'Maadi'","'Maadi' = b.branch_name"),
    ("o.branch_id = b.branch_id","o.branch_id = b.branch_id; DELETE FROM orders"),
])
def test_narrow_join_never_hides_other_differences(old,new):
    sql = reversed_sql().replace(old,new)
    result = SqlGuard().review(sql,bir())
    assert not result.validated
    assert result.reviewed_sql == sql


@pytest.mark.parametrize("question",[
    "إجمالي المبيعات لكل فرع في أكتوبر 2026 كام؟",
    "كل فرع عمل كام أوردر في أكتوبر 2026؟",
    "أنهي فرع عنده أعلى إجمالي مبيعات في أكتوبر 2026؟",
])
def test_other_gold_shapes_do_not_gain_equality_normalization(question):
    intent = parse(question,"other-gold-unit")[0]
    original = reference_sql(intent)
    reversed_join = original.replace("o.branch_id = b.branch_id","TMP").replace("b.branch_id = o.branch_id","o.branch_id = b.branch_id").replace("TMP","b.branch_id = o.branch_id")
    assert SqlGuard().review(original,intent).validated
    assert not SqlGuard().review(reversed_join,intent).validated


def test_generic_catalog_branch_and_dates_are_bound_to_bir():
    intent = parse("فرع مصر الجديدة باع بكام في ديسمبر 2027؟","generic-join-unit")[0]
    sql = reference_sql(intent).replace("ON b.branch_id = o.branch_id","ON o.branch_id = b.branch_id")
    assert SqlGuard().review(sql,intent).validated
    assert not SqlGuard().review(sql.replace("Heliopolis","Maadi"),intent).validated
