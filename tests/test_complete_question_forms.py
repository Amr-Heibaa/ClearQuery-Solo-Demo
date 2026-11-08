"""Isolated semantic validation tests; no model inference is claimed here."""
import pytest
from clearquery.contracts import TranslationResult
from clearquery.intent import parse
from clearquery.decision import decide
from clearquery.translation.gate import BoundedSemanticGate

MONEY = "فرع المعادي باع بكام في أكتوبر 2026؟"
COUNT = "كل فرع عمل كام أوردر في أكتوبر 2026؟"


def check(ar, en):
    bir, _ = parse(ar,"semantic-unit")
    value = TranslationResult(trace_id=bir.trace_id,text_ar_original=ar,text_en=en,engine="TEST_STUB",error=None)
    result = BoundedSemanticGate().check(ar,bir,value)
    assert value.text_en == en and value.text_ar_original == ar
    return result


@pytest.mark.parametrize("ar,en",[
    (MONEY,"How much did the Maadi branch sell in October 2026?"),
    (MONEY,"How much did Maadi branch sell during October 2026?"),
    ("فرع مدينة نصر باع بكام في ديسمبر ٢٠٢٦؟","How much did the Nasr City branch sell in December 2026?"),
    ("فرع مصر الجديدة باع بكام في سبتمبر 2027؟","How much did the Heliopolis branch sell in September 2027?"),
    ("فرع إسكندرية باع بكام في يناير 2026؟","How much did the Alexandria branch sell in January 2026?"),
    (COUNT,"How many orders did each branch record in October 2026?"),
    (COUNT,"How many orders were recorded per branch in October 2026?"),
    (COUNT,"How many recorded orders are there for each branch in October 2026?"),
    ("كام عدد الطلبات لكل فرع في نوفمبر ٢٠٢٦؟","How many orders per branch in November 2026?"),
    (COUNT,"What was the recorded order count per branch in October 2026?"),
])
def test_equivalent_complete_questions(ar,en):
    assert check(ar,en).status == "PASS"


@pytest.mark.parametrize("en",[
    "How many units did the Maadi branch sell in October 2026?",
    "How many items did the Maadi branch sell in October 2026?",
    "How much oil did the Maadi branch sell in October 2026?",
    "How much money did the Maadi branch sell in October 2026?",
    "How many orders did each branch record in October 2026?",
    "How much did the Nasr City branch sell in October 2026?",
    "How much did the Maadi branch sell in September 2026?",
    "How much did the Maadi branch sell in October 2025?",
    "How much net profit did the Maadi branch sell in October 2026?",
    "How much did the Maadi branch sell in October 2026 excluding returns?",
    "How much did the Maadi branch sell in October 2026 in USD?",
    "How much did the Maadi branch sell in October 2026 above 500 EGP?",
    "How much did the Maadi branch sell per item in October 2026?",
    "How much did the Maadi branch not sell in October 2026?",
    "How much did the Maadi branch sell in October 2026 and November 2026?",
    "Did the Maadi branch sell in October 2026?",
    "Sell for the Maadi branch in October 2026.",
    "How much did the best branch sell in October 2026?",
])
def test_monetary_near_misses_never_approved(en):
    assert check(MONEY,en).status != "PASS"


@pytest.mark.parametrize("en",[
    "How many orders did each branch operate in October 2026?",
    "How many orders did each branch operate on in October 2026?",
    "How many orders did each branch complete in October 2026?",
    "How many cancelled orders did each branch record in October 2026?",
    "How many orders did each branch record in September 2026?",
    "How many orders did each branch record in October 2027?",
    "How many units did each branch record in October 2026?",
    "How much did the Maadi branch sell in October 2026?",
    "How many orders did each branch record in October 2026 excluding refunds?",
    "How many orders did each branch record in October 2026 and close?",
])
def test_order_count_near_misses_never_approved(en):
    assert check(COUNT,en).status != "PASS"


@pytest.mark.parametrize("ar,en",[
    ("فرع المعادي باع بكام في أكتوبر 2026 من غير المرتجعات؟","How much did the Maadi branch sell in October 2026?"),
    ("فرع المعادي باع بكام بالدولار في أكتوبر 2026؟","How much did the Maadi branch sell in October 2026?"),
    ("كل فرع عمل كام أوردر مكتمل في أكتوبر 2026؟","How many orders did each branch record in October 2026?"),
    ("مين أحسن فرع في أكتوبر 2026؟","How much did the Maadi branch sell in October 2026?"),
    ("صافي الربح لكل فرع كام؟","How many orders did each branch record in October 2026?"),
])
def test_missing_constraints_unsupported_and_ambiguity_stay_blocked(ar,en):
    assert check(ar,en).status != "PASS"


def test_policy_remains_independent_of_english_question_interpretation():
    assert decide(parse("مين أحسن فرع في أكتوبر 2026؟","t")[0]).decision == "CLARIFY"
    assert decide(parse("صافي الربح لكل فرع كام؟","t")[0]).decision == "ABSTAIN"


@pytest.mark.parametrize("change",[
    {"metric":"order_count"}, {"branch_name":"Nasr City"}, {"date_start":"2026-09-01"},
])
def test_complete_form_cannot_override_authoritative_bir(change):
    bir = parse(MONEY,"authoritative-bir")[0].model_copy(update=change)
    raw = "How much did the Maadi branch sell in October 2026?"
    value = TranslationResult(trace_id=bir.trace_id,text_ar_original=MONEY,text_en=raw,engine="TEST_STUB",error=None)
    result = BoundedSemanticGate().check(MONEY,bir,value)
    assert result.status == "MISMATCH" and value.text_en == raw
