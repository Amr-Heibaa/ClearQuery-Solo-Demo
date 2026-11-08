"""Gold SQL Q1–Q5 executed through the Person 1 read-only executor (app role),
compared with an independent Python recomputation from the generator."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from clearquery_poc import executor as ex
from clearquery_poc import generator as g

pytestmark = pytest.mark.db

RESULTS: dict[str, dict] = {}
OUT = Path(__file__).resolve().parents[1] / "reports" / "gold_sql_results.json"


@pytest.fixture(scope="module")
def october(dataset):
    branches, orders = dataset
    m = g.metrics(orders, *g.month_range(2026, 10))
    names = {b.branch_id: b.branch_name for b in branches}
    return {names[k]: v for k, v in m.items()}


def run(qid: str) -> ex.SqlResult:
    res = ex.execute_readonly(ex.gold_fixture_request(qid, f"t-gold-{qid}"))
    assert res.error is None, res.error
    assert res.validated and res.executed and not res.truncated
    RESULTS[qid] = res.to_contract_dict()
    return res


def test_q1_total_sales_per_branch(october):
    r = run("Q1")
    assert r.columns == ["branch_name", "total_sales"]
    assert {n: Decimal(s) for n, s in r.rows} == {n: v[1] for n, v in october.items()}
    assert [row[0] for row in r.rows] == sorted(october)


def test_q2_order_count_per_branch(october):
    r = run("Q2")
    assert r.columns == ["branch_name", "order_count"]
    assert dict(r.rows) == {n: v[0] for n, v in october.items()}
    assert max(r.rows, key=lambda x: x[1])[0] == "Nasr City"


def test_q3_total_sales_all_branches(october):
    r = run("Q3")
    assert r.columns == ["total_sales"] and r.row_count == 1
    assert r.rows[0][0] == sum(v[1] for v in october.values())


def test_q4_total_sales_maadi(october):
    r = run("Q4")
    assert r.rows == [[october["Maadi"][1]]]


def test_q5_top_branch_by_total_sales(october):
    r = run("Q5")
    assert r.rows == [["Maadi", october["Maadi"][1]]]


def test_cross_checks():
    q1 = {n: s for n, s in RESULTS["Q1"]["rows"]}
    assert Decimal(RESULTS["Q3"]["rows"][0][0]) == sum(Decimal(s) for s in q1.values())
    assert RESULTS["Q4"]["rows"][0][0] == q1["Maadi"]
    assert RESULTS["Q5"]["rows"][0][0] == "Maadi"


def test_write_results_artifact():
    assert sorted(RESULTS) == ["Q1", "Q2", "Q3", "Q4", "Q5"]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
