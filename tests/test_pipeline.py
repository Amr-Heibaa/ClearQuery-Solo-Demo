"""Isolated tests use explicitly labelled stubs; none claim real inference."""
from pathlib import Path
import copy
import hashlib
import json
import pytest
from pydantic import ValidationError
from clearquery.contracts import *
from clearquery.intent import parse
from clearquery.decision import decide
from clearquery.voice import confirm, WhisperASR
from clearquery.translation import QwenTranslation, QwenConfig
from clearquery.translation.gate import BoundedSemanticGate
from clearquery.sql_guard import SqlGuard, reference_sql
from clearquery.text_to_sql import project, map_candidate, XiYanG0
from clearquery.orchestrator import Pipeline
from clearquery_poc.text_to_sql.adapter import XiYanAdapter

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = [
    ("إجمالي المبيعات لكل فرع في أكتوبر 2026 كام؟", "Total sales per branch in October 2026?", "total_sales", "by_branch", None),
    ("كل فرع عمل كام أوردر في أكتوبر 2026؟", "Order count per branch in October 2026?", "order_count", "by_branch", None),
    ("إجمالي المبيعات في كل الفروع في أكتوبر 2026 كام؟", "Total sales across all branches in October 2026?", "total_sales", "overall", None),
    ("فرع المعادي باع بكام في أكتوبر 2026؟", "Total sales for Maadi branch in October 2026?", "total_sales", "overall", "Maadi"),
    ("أنهي فرع عنده أعلى إجمالي مبيعات في أكتوبر 2026؟", "Which branch has highest total sales in October 2026?", "total_sales", "top_branch", None),
]


def bir(i=0, trace="t"):
    return parse(QUESTIONS[i][0], trace)[0]


def translated(text, trace="t", source=None):
    return TranslationResult(trace_id=trace, text_ar_original=source or QUESTIONS[0][0], text_en=text, engine="TEST_STUB", error=None)


class StubTranslation:
    def __init__(self, text=QUESTIONS[0][1], error=False): self.text, self.error, self.calls = text, error, 0
    def translate(self, question, trace):
        self.calls += 1
        return TranslationResult(trace_id=trace, text_ar_original=question, text_en=None if self.error else self.text,
            engine="TEST_STUB", error=Error(code="TRANSLATION_FAILED", message="isolated test failure") if self.error else None)


class StubGenerator:
    def __init__(self, sql=None): self.sql, self.calls = sql, 0
    def generate(self, text, b):
        self.calls += 1
        return (SqlCandidate(trace_id=b.trace_id, raw_model_output="TEST_STUB raw", candidate_sql=self.sql or reference_sql(b),
            model_id="TEST_STUB", model_revision=None, error=None),
            InferenceMetadata(trace_id=b.trace_id, quantization="none", prompt_template_id="TEST_STUB", schema_context_id="TEST_STUB",
                prompt_sha256=None, generation_params={}, inference_ms=None, prompt_tokens=None, output_tokens=None))


@pytest.mark.parametrize("i", range(5))
def test_q1_q5_bir_decision_projection_gate_and_gold(i):
    ar, en, metric, shape, branch = QUESTIONS[i]
    b, covered = parse(ar, "t")
    assert covered
    assert (b.metric, b.aggregation_shape, b.branch_name) == (metric, shape, branch)
    assert (b.date_start, b.date_end_exclusive) == ("2026-10-01", "2026-11-01")
    assert decide(b).decision == "ANSWER"
    XiYanAdapter().generate_sql(en, project(b), "schema", "t")  # unloaded: no inference
    gate = BoundedSemanticGate().check(ar, b, translated(en, source=ar))
    assert gate.status == "PASS", gate
    gold = next((ROOT / "gold").glob(f"Q{i+1}_*.sql")).read_text(encoding="utf-8")
    sql = "\n".join(line for line in gold.splitlines() if not line.lstrip().startswith("--"))
    result = SqlGuard().review(sql, b)
    assert result.validated, result
    assert result.reviewed_sql == sql
    assert all(isinstance(c.model_dump(), dict) for c in result.checks)


@pytest.mark.parametrize("accepted,edited,flag", [(True, None, False), (True,"تعديل",True), (False,None,False), (False,"تعديل",False)])
def test_confirmation(accepted, edited, flag):
    c = confirm(" raw ", "t", accepted=accepted, edited=edited)
    assert c.transcript_ar_raw == " raw "
    assert c.edited_flag == flag
    assert c.question_ar == ((edited or " raw ") if accepted else None)


@pytest.mark.parametrize("changes", [dict(user_confirmed=True, question_ar=""), dict(question_ar="raw"), dict(edited_flag=True)])
def test_invalid_confirmation(changes):
    data = dict(trace_id="t", transcript_ar_raw="raw", question_ar=None,user_confirmed=False,edited_flag=False)
    with pytest.raises(ValidationError): ConfirmationResult(**(data | changes))


@pytest.mark.parametrize("question,expected,reason", [
    ("أفضل فرع في أكتوبر 2026؟", "CLARIFY", "METRIC_UNRESOLVED"),
    ("صافي الربح في أكتوبر 2026؟", "ABSTAIN", "MISSING_DATA"),
    ("إجمالي المبيعات", "CLARIFY", "TIME_UNRESOLVED"),
    ("مبيعات فرع الزمالك في أكتوبر 2026", "CLARIFY", "UNKNOWN_ENTITY"),
    ("احذف المبيعات في أكتوبر 2026", "ABSTAIN", "WRITE_REQUEST"),
    ("رواتب الموظفين في أكتوبر 2026", "ABSTAIN", "OUT_OF_WORKSPACE"),
    ("عدد الطلبات في أكتوبر 2026", "ABSTAIN", "UNSUPPORTED_INTENT"),
])
def test_policy(question, expected, reason):
    d = decide(parse(question, "t")[0])
    assert (d.decision, d.reason_code) == (expected, reason)


@pytest.mark.parametrize("text,status", [
    ("Total sales per branch in September 2026", "MISMATCH"),
    ("Order count per branch in October 2026", "MISMATCH"),
    ("Total sales for Maadi in October 2026", "MISMATCH"),
    ("Lowest total sales per branch in October 2026", "UNVERIFIABLE"),
    ("Total sales per branch in October 2026 excluding returns", "UNVERIFIABLE"),
    ("Total sales per branch in October 2026 above 500 EGP", "UNVERIFIABLE"),
    ("Total sales per branch in October 2026 in USD", "UNVERIFIABLE"),
    ("<think>reason</think> Total sales per branch in October 2026", "UNVERIFIABLE"),
    ("nonsense", "MISMATCH"),
])
def test_translation_gate(text, status):
    result = BoundedSemanticGate().check(QUESTIONS[0][0], bir(), translated(text))
    if status == "UNVERIFIABLE": assert result.status != "PASS"
    else: assert result.status == status


def test_source_and_bir_identity():
    assert BoundedSemanticGate().check("different", bir(), translated(QUESTIONS[0][1])).status == "MISMATCH"
    changed = bir().model_copy(update={"metric": "order_count"})
    assert BoundedSemanticGate().check(QUESTIONS[0][0], changed, translated(QUESTIONS[0][1])).status == "MISMATCH"


def test_arabic_digits_and_reference_date():
    b, covered = parse(QUESTIONS[0][0].replace("2026", "٢٠٢٦"), "t")
    assert covered and b.date_start == "2026-10-01"
    assert parse("اجمالي المبيعات الشهر اللي فات", "t")[0].date_start == "2026-10-01"


UNSAFE = [
    "SELECT 1; DROP TABLE orders", "DELETE FROM orders", "SELECT * FROM orders", "SELECT pg_sleep(1)",
    "SELECT total_amount INTO dump FROM orders", "SELECT total_amount FROM orders FOR UPDATE",
    "WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x", "SELECT * FROM private.orders",
    "SELECT total_amount FROM orders UNION SELECT total_amount FROM orders", "SELECT (SELECT total_amount FROM orders)",
    "SELECT * FROM pg_catalog.pg_authid", "SELECT pg_read_file('/etc/passwd')", "COPY orders TO STDOUT",
    "SELECT dblink('x','y')", "SELECT total_amount FROM orders; SELECT 1", "SELECT 1 -- hi",
    "SELECT 1 /* nested /* comment */ comment */", "SELECT total_amount FROM orders WHERE true OR 1=1",
    "SELECT SUM(total_amount) AS total_sales FROM orders", "invalid SQL", "", "SELECT COUNT(*) FROM orders",
    "SELECT nextval('sequence')", "SELECT set_config('search_path','public',false)",
]


@pytest.mark.parametrize("sql", UNSAFE)
def test_unsafe_sql(sql):
    r = SqlGuard().review(sql, bir())
    assert not r.validated
    assert r.reviewed_sql == sql
    assert r.rejected_reason
    assert r.checks[-1].status in {"FAIL", "NOT_RUN"}


@pytest.mark.parametrize("i", range(5))
@pytest.mark.parametrize("mutation", ["date", "metric", "projection", "limit", "schema", "comment"])
def test_gold_mutations_rejected(i, mutation):
    b = bir(i)
    sql = reference_sql(b)
    mutations = {
        "date": sql.replace("2026-10-01", "2026-09-01"),
        "metric": sql.replace("SUM(", "MAX(").replace("COUNT(", "SUM("),
        "projection": sql.replace("SELECT ", "SELECT 42, ", 1),
        "limit": sql + " OFFSET 1",
        "schema": sql.replace("FROM orders", "FROM public.orders").replace("FROM branches", "FROM public.branches"),
        "comment": sql + " -- candidate comment",
    }
    assert not SqlGuard().review(mutations[mutation], b).validated


@pytest.mark.parametrize("i", range(5))
def test_exact_sql_preservation_and_qualification(i):
    b = bir(i)
    sql = "  \n" + reference_sql(b).replace("FROM orders", "FROM analytics.orders").replace("FROM branches", "FROM analytics.branches").replace("JOIN orders", "JOIN analytics.orders").replace("JOIN branches", "JOIN analytics.branches") + "; \n"
    r = SqlGuard().review(sql, b)
    assert r.validated, r
    assert r.reviewed_sql.encode() == sql.encode()


def test_overall_orders_qualifiers():
    b = bir(2)
    sql = reference_sql(b).replace("SUM(total_amount)", "SUM(orders.total_amount)").replace("WHERE order_date", "WHERE orders.order_date").replace("AND order_date", "AND orders.order_date")
    assert SqlGuard().review(sql, b).validated
    assert not SqlGuard().review(sql.replace("orders.total_amount", "evil.total_amount"), b).validated


def test_top_ties_and_empty_sales_required():
    assert not SqlGuard().review(reference_sql(bir(4)).replace("FETCH FIRST 1 ROWS WITH TIES", "LIMIT 1"), bir(4)).validated
    assert not SqlGuard().review(reference_sql(bir(2)).replace("COALESCE(SUM(total_amount), 0)", "SUM(total_amount)"), bir(2)).validated


@pytest.mark.parametrize("i", range(5))
def test_orchestrator_success_is_mock_only(i):
    t, g = StubTranslation(QUESTIONS[i][1]), StubGenerator()
    r = Pipeline(t, g).run(text_ar=QUESTIONS[i][0], confirmed=True)
    assert r["stop_reason"] == "REVIEWED_SQL_EXECUTION_DISABLED", r
    assert g.calls == t.calls == 1 and not r["executed"]
    assert all(s["trace_id"] == r["trace_id"] for s in r["stages"])
    assert [s["stage"] for s in r["stages"]] == ["confirmation", "bir", "decision", "translation", "semantic_gate", "sql_candidate", "inference_metadata", "sql_guard"]


@pytest.mark.parametrize("question,confirmed,translation_error,translation,expected", [
    (QUESTIONS[0][0], False, False, QUESTIONS[0][1], "TRANSCRIPT_UNCONFIRMED"),
    ("افضل فرع في اكتوبر 2026", True, False, "", "CLARIFY"),
    ("صافي الربح في اكتوبر 2026", True, False, "", "ABSTAIN"),
    (QUESTIONS[0][0], True, True, "", "TRANSLATION_FAILED"),
    (QUESTIONS[0][0], True, False, "Order count per branch in October 2026", "TRANSLATION_SEMANTIC_MISMATCH"),
    (QUESTIONS[0][0], True, False, QUESTIONS[0][1] + " excluding cancelled", "TRANSLATION_SEMANTIC_UNVERIFIABLE"),
])
def test_stop_conditions(question, confirmed, translation_error, translation, expected):
    t, g = StubTranslation(translation, translation_error), StubGenerator()
    r = Pipeline(t,g).run(text_ar=question, confirmed=confirmed)
    assert r["stop_reason"] == expected, r
    assert not g.calls and not r["executed"]
    if expected in {"CLARIFY", "ABSTAIN", "TRANSCRIPT_UNCONFIRMED"}: assert not t.calls


def test_guard_rejection_stops():
    r = Pipeline(StubTranslation(), StubGenerator("SELECT 1; DROP TABLE orders")).run(text_ar=QUESTIONS[0][0], confirmed=True)
    assert r["stop_reason"] == "UNSAFE_SQL" and not r["executed"]


def test_audio_confirmation_and_exact_raw_edit():
    class StubASR:
        def transcribe(self, audio, trace):
            return AsrResult(trace_id=trace, audio_id="TEST_STUB", transcript_ar_raw=" raw with spaces ", confidence=None, engine="TEST_STUB", error=None)
    p = Pipeline(StubTranslation(), StubGenerator(), asr=StubASR())
    pending = p.run(audio="fixture")
    assert pending["stop_reason"] == "TRANSCRIPT_UNCONFIRMED"
    r = p.run(audio="fixture", confirmation_callback=lambda raw:(True,QUESTIONS[0][0]))
    assert r["stop_reason"] == "REVIEWED_SQL_EXECUTION_DISABLED"
    c = r["stages"][1]["result"]
    assert c["transcript_ar_raw"] == " raw with spaces " and c["edited_flag"]


def test_asr_missing_file_real_adapter_failure():
    r = Pipeline(StubTranslation(), StubGenerator(), asr=WhisperASR()).run(audio="does-not-exist.wav")
    assert r["stop_reason"] == "ASR_FAILED"


def test_translation_real_adapter_missing_local_model():
    r = QwenTranslation(QwenConfig(model_id="missing-model", local_files_only=True)).translate("سؤال", "t")
    assert r.error.code == "TRANSLATION_FAILED" and r.text_en is None


def test_person1_mapping_preserves_exact_output():
    adapter = XiYanAdapter()
    old = adapter._candidate("t", schema_context_id="test", raw_output="  raw\n", candidate_sql=" SELECT 1; ")
    c, meta = map_candidate(old, "t")
    assert c.raw_model_output == "  raw\n" and c.candidate_sql == " SELECT 1; "
    assert meta.prompt_template_id.endswith("v4")
    with pytest.raises(ValueError): map_candidate(old, "wrong")


def test_unloaded_person1_adapter_real_smoke():
    c, meta = XiYanG0(load_model=False).generate(QUESTIONS[0][1], bir())
    assert c.error.code == "MODEL_NOT_LOADED" and c.candidate_sql is None


@pytest.mark.parametrize("cls,changes", [
    (BIR, {"workspace": "hr"}), (BIR, {"branch_name": "unknown"}),
    (BIR, {"date_start": "2026-11-02"}), (BIR, {"contract_version": "1.2"}),
    (BIR, {"date_start": "20261001"}),
    (BIR, {"approved": True}),
])
def test_schema_compliance(cls, changes):
    with pytest.raises(ValidationError): cls.model_validate(bir().model_dump() | changes)


def test_trace_boundary_revalidation():
    t = StubTranslation()
    original = t.translate
    t.translate = lambda question, trace: original(question, "WRONG")
    g = StubGenerator()
    r = Pipeline(t,g).run(text_ar=QUESTIONS[0][0], confirmed=True)
    assert r["stop_reason"] == "TRACE_ID_MISMATCH" and not g.calls


def test_hashes_and_frozen_contract():
    for line in (ROOT / "PERSON1_SHA256SUMS").read_text().splitlines():
        digest, relative = line.split("  ",1)
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest
    frozen = ROOT / "contracts/ClearQuery_G0_v1.1_Final_Freeze_Candidate.md"
    assert hashlib.sha256(frozen.read_bytes()).hexdigest() == "09608f6e5e5cd226eafa9120587524268e80c83f9822202b0761832d028dd733"


def test_execution_disabled():
    from clearquery.executor import execute_generated
    with pytest.raises(RuntimeError): execute_generated("SELECT 1")


@pytest.mark.parametrize("suffix", [";;", "; ;", ";; -- extra"])
def test_extra_empty_statements_rejected(suffix):
    assert not SqlGuard().review(reference_sql(bir()) + suffix, bir()).validated


def test_alias_column_renaming_rejected():
    sql = reference_sql(bir()).replace("orders o", "orders o(order_id, total_amount, order_date, branch_id)")
    assert not SqlGuard().review(sql, bir()).validated


def test_whisper_raw_text_preserved_isolated_mock(tmp_path):
    from types import SimpleNamespace
    adapter = WhisperASR()
    raw = "  نص خام بدون تعديل\n"
    adapter._model = SimpleNamespace(transcribe=lambda *args, **kwargs: {"text": raw})
    adapter._decode_audio = lambda path: path  # isolated ASR contract test, not a decoding test
    audio = tmp_path / "synthetic-unit-test.wav"
    audio.write_bytes(b"TEST_STUB_NOT_REAL_AUDIO")
    r = adapter.transcribe(audio, "t")
    assert r.transcript_ar_raw == raw and r.confidence is None and r.error is None


def test_qwen_exact_output_and_settings_isolated_mock(monkeypatch):
    import contextlib
    import sys
    from types import SimpleNamespace
    calls = {}
    class Inputs(dict):
        def to(self, device): return self
    class Tokenizer:
        def apply_chat_template(self, *args, **kw):
            calls["template"] = kw
            return "TEST_STUB_PROMPT"
        def __call__(self, *args, **kw):
            return Inputs(input_ids=SimpleNamespace(shape=(1, 2)))
        def decode(self, *args, **kw): return "  Total sales per branch in October 2026?\n"
    class Model:
        def generate(self, **kw):
            calls["generation"] = kw
            return [[1, 2, 3]]
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(inference_mode=contextlib.nullcontext))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(AutoModelForCausalLM=object, AutoTokenizer=object))
    adapter = QwenTranslation()
    adapter.model, adapter.tokenizer = Model(), Tokenizer()
    r = adapter.translate("  سؤال أصلي\n", "t")
    assert r.text_ar_original == "  سؤال أصلي\n"
    assert r.text_en == "  Total sales per branch in October 2026?\n"
    assert not calls["template"]["enable_thinking"] and not calls["generation"]["do_sample"]
    assert adapter.metadata()["status"] == "EXPLORATORY"


def test_typed_interactive_edit_keeps_raw():
    raw = " raw typed text "
    r = Pipeline(StubTranslation(), StubGenerator()).run(text_ar=raw,
        confirmation_callback=lambda value:(True, QUESTIONS[0][0]))
    c = r["stages"][0]["result"]
    assert c["transcript_ar_raw"] == raw and c["question_ar"] == QUESTIONS[0][0] and c["edited_flag"]


def test_component_contract_version_stop():
    t, g = StubTranslation(), StubGenerator()
    original = t.translate
    t.translate = lambda question, trace: original(question, trace).model_dump() | {"contract_version":"wrong"}
    r = Pipeline(t,g).run(text_ar=QUESTIONS[0][0],confirmed=True)
    assert r["stop_reason"] == "CONTRACT_VERSION_MISMATCH" and not g.calls


def test_generator_load_error_keeps_model_metadata(monkeypatch):
    g = XiYanG0()
    g.adapter = XiYanAdapter()
    def failed(): raise RuntimeError("TEST_STUB CUDA load failure")
    monkeypatch.setattr(g.adapter, "load", failed)
    c, m = g.generate(QUESTIONS[0][1], bir())
    assert c.error.code == "MODEL_NOT_LOADED" and c.candidate_sql is None
    assert c.model_revision == "afe320fcaf18bee342174b4e35e5a481ae93719f"
    assert m.prompt_template_id.endswith("v4")


def test_cli_actual_process_utf8_and_translation_failure():
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-m", "clearquery", "--text-ar", QUESTIONS[0][0], "--confirmed"],
                       capture_output=True, text=True, encoding="utf-8")
    data = json.loads(r.stdout)
    # This process uses real adapters, not a translation stub. Models are absent
    # in the minimal test environment; if configured later it may proceed.
    assert data["executed"] is False
    assert data["stages"][0]["result"]["question_ar"] == QUESTIONS[0][0]


def test_saved_real_smoke_guard_if_available():
    path = ROOT / "runtime/xiyan_real_smoke.json"
    if not path.exists(): pytest.skip("No real model smoke artifact on this checkout")
    from types import SimpleNamespace
    source = json.loads(path.read_text(encoding="utf-8"))
    assert source["inference_kind"] == "REAL_LOCAL_MODEL"
    data = source["candidate"]
    c, meta = map_candidate(SimpleNamespace(to_dict=lambda:data), data["trace_id"])
    r = SqlGuard().review(c.candidate_sql, bir(2, data["trace_id"]))
    assert r.validated and r.reviewed_sql == data["candidate_sql"]
