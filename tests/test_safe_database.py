"""Real PostgreSQL integration checks and separately labelled boundary unit tests."""
from dataclasses import replace
import json
from pathlib import Path
import pytest
from clearquery.contracts import BIR,SqlCandidate
from clearquery.executor import safe_demo as db
from clearquery.sql_guard import reference_sql
from clearquery.intent import parse
from clearquery_poc.executor import ValidatedSql

ROOT=Path(__file__).resolve().parents[1]


def candidate(sql=None, ar="إجمالي المبيعات لكل فرع في أكتوبر 2026 كام؟"):
    bir=parse(ar,"execution-test")[0]
    return SqlCandidate(trace_id=bir.trace_id,raw_model_output="UNIT_TEST_NOT_REAL_MODEL",
        candidate_sql=sql or reference_sql(bir),model_id="TEST_ONLY",model_revision=None,error=None),bir


@pytest.mark.parametrize("sql",[
    "INSERT INTO analytics.orders VALUES (1,1,DATE '2026-10-01',1)",
    "UPDATE analytics.orders SET total_amount=0", "DELETE FROM analytics.orders",
    "DROP TABLE analytics.orders", "CREATE TABLE analytics.x(a int)",
    "SELECT 1; SELECT 2", "SELECT * FROM sales_poc.orders",
    "SELECT password FROM analytics.orders", "SELECT pg_sleep(1)",
    "SELECT b.branch_name,SUM(o.total_amount) FROM analytics.branches b JOIN analytics.orders o ON b.branch_id=o.order_id GROUP BY b.branch_name",
    "SELECT SUM(total_amount) FROM analytics.orders WHERE order_date >= DATE '2025-10-01'",
])
def test_unsafe_or_inconsistent_sql_cannot_obtain_execution_capability(sql):
    executor=db.ControlledExecutor()
    with pytest.raises(db.SafetyError):executor.approve(*candidate(sql))
    assert not executor._issued


def test_trace_and_policy_must_match():
    executor=db.ControlledExecutor(); c,b=candidate()
    with pytest.raises(db.SafetyError):executor.approve(c.model_copy(update={"trace_id":"other"}),b)
    for ar in ["مين أحسن فرع في أكتوبر 2026؟","صافي الربح لكل فرع كام؟"]:
        c,b=candidate("SELECT 1",ar)
        with pytest.raises(db.SafetyError):executor.approve(c,b)


def test_fabricated_approval_raw_sql_and_person1_request_cannot_execute():
    executor=db.ControlledExecutor()
    for value in ["SELECT 1",ValidatedSql("t","SELECT 1",True,"sql_guard"),db.Approval("forged","t")]:
        with pytest.raises(db.SafetyError):executor.execute(value)
    approval=executor.approve(*candidate())
    with pytest.raises(db.SafetyError):executor.execute(replace(approval))


def test_expired_validation_is_not_executed(monkeypatch):
    executor=db.ControlledExecutor(); approval=executor.approve(*candidate())
    monkeypatch.setattr(executor,"TTL_SECONDS",-1)
    with pytest.raises(db.SafetyError,match="EXPIRED"):executor.execute(approval)


def test_boundary_rechecks_current_guard(monkeypatch):
    executor=db.ControlledExecutor(); c,b=candidate(); approval=executor.approve(c,b)
    rejected=db.SqlGuard().review("DELETE FROM orders",b)
    monkeypatch.setattr(db.SqlGuard,"review",lambda *args:rejected)
    with pytest.raises(db.SafetyError,match="EXECUTION_GUARD"):executor.execute(approval)


def test_connection_error_redacts_password_and_consumes_approval(monkeypatch):
    executor=db.ControlledExecutor();approval=executor.approve(*candidate())
    def fail(*args):raise RuntimeError("password=TOP_SECRET postgres://sensitive")
    monkeypatch.setattr(db,"connect_demo",fail)
    result=executor.execute(approval,object())
    assert not result.executed and result.error["code"]=="DB_ERROR" and "SECRET" not in str(result.error)
    with pytest.raises(db.SafetyError):executor.execute(approval,object())


def test_preflight_failure_never_dispatches_candidate(monkeypatch):
    class Connection:
        executed=False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def rollback(self):pass
        def cursor(self):self.executed=True;raise AssertionError("Must not dispatch")
    connection=Connection()
    monkeypatch.setattr(db,"connect_demo",lambda settings:connection)
    def reject(*args):raise db.SafetyError("SYNTHETIC_MISMATCH")
    monkeypatch.setattr(db,"preflight",reject)
    executor=db.ControlledExecutor()
    result=executor.execute(executor.approve(*candidate()),object())
    assert not connection.executed and not result.executed and not result.rows
    assert result.error["code"]=="DB_PREFLIGHT_FAILED"


@pytest.mark.parametrize("changes",[{"host":"localhost"},{"port":5432},{"dbname":"production"},
    {"user":"analyst_ro"},{"row_cap":1001},{"row_cap":0},{"statement_timeout_ms":6000}])
def test_wrong_database_or_unbounded_limits_rejected_before_connect(changes):
    settings=db.DbSettings("127.0.0.1",55432,"clearquery_demo","clearquery_app_ro","TEST_ONLY")
    with pytest.raises(db.SafetyError):db.connect_demo(replace(settings,**changes))


@pytest.mark.db
@pytest.mark.parametrize("index", range(5))
def test_guarded_select_fixture_results_equal_gold(index):
    """Trusted fixture SQL, not model inference; real DB comparison is opt-in."""
    from test_pipeline import QUESTIONS
    from clearquery_poc.executor import execute_readonly,gold_fixture_request
    ar=QUESTIONS[index][0];bir=parse(ar,"public-db-fixture")[0]
    value=SqlCandidate(trace_id=bir.trace_id,raw_model_output="TRUSTED_TEST_FIXTURE",
        candidate_sql=reference_sql(bir),model_id="TEST_FIXTURE",model_revision=None,error=None)
    settings=db.demo_settings();executor=db.ControlledExecutor()
    actual=executor.execute(executor.approve(value,bir),settings)
    gold=execute_readonly(gold_fixture_request("Q"+str(index+1),"gold-reference"),settings)
    assert actual.executed and not actual.error and not gold.error
    assert actual.columns==gold.columns and sorted(actual.rows)==sorted(gold.rows)

@pytest.mark.db
def test_real_zero_order_branches_and_row_cap():
    # Trusted test SQL from current Guard grammar; not represented as model output.
    settings=db.demo_settings();executor=db.ControlledExecutor()
    c,b=candidate(ar="عدد الطلبات لكل فرع في نوفمبر 2026؟")
    all_rows=executor.execute(executor.approve(c,b),settings)
    assert all_rows.executed and not all_rows.error
    assert any(row[1]==0 for row in all_rows.rows)
    bounded=executor.execute(executor.approve(c,b),replace(settings,row_cap=2))
    assert bounded.executed and bounded.truncated and bounded.row_count==2


@pytest.mark.db
def test_real_session_timeout_and_data_unchanged():
    import psycopg
    settings=replace(db.demo_settings(),statement_timeout_ms=100)
    with db.connect_demo(settings) as conn:
        try:
            assert db.preflight(conn,settings)["verified"]
            # Trusted timing probe only; this function is forbidden in model SQL.
            with pytest.raises(psycopg.errors.QueryCanceled):conn.execute("SELECT pg_sleep(0.3)")
        finally:conn.rollback()
    with db.connect_demo(settings) as conn:
        try:assert db.preflight(conn,settings)["synthetic_rows"]=={"branches":4,"orders":50}
        finally:conn.rollback()


@pytest.mark.db
def test_real_approval_can_execute_exactly_once():
    settings=db.demo_settings();executor=db.ControlledExecutor()
    approval=executor.approve(*candidate())
    result=executor.execute(approval,settings)
    assert result.executed and not result.error
    with pytest.raises(db.SafetyError,match='PROVENANCE'):executor.execute(approval,settings)
