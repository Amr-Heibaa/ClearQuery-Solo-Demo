"""Semantic regression tests only; no model inference claims."""
import pytest
from clearquery.contracts import TranslationResult
from clearquery.intent import parse
from clearquery.translation.gate import BoundedSemanticGate

AR = " كام عدد الطلبات اللي تسجلت في كل فرع خلال شهر اكتوبر 2026"
EN = "How many orders were registered in each branch during October 2026?"


def check(ar=AR, en=EN, changes=None):
    bir = parse(ar, "registered-orders-unit")[0]
    if changes:
        bir = bir.model_copy(update=changes)
    tr = TranslationResult(trace_id=bir.trace_id, text_ar_original=ar,
        text_en=en, engine="TEST_ONLY", error=None)
    result = BoundedSemanticGate().check(ar, bir, tr)
    assert tr.text_en == en and tr.text_ar_original == ar
    return result


@pytest.mark.parametrize("ar,en", [
    (AR, EN),
    ("عدد الأوردرات اللي اتسجلت في كل فرع في نوفمبر ٢٠٢٧؟",
     "How many orders were registered in every branch in November 2027?"),
    ("كام عدد الطلبات اللي اتسجلت في كل فرع خلال ديسمبر 2028؟",
     "How many orders were recorded in each branch during December 2028?"),
    ("كل فرع عمل كام أوردر في أكتوبر 2026؟", EN),
    (AR, "How many orders did each branch record in October 2026?"),
])
def test_complete_registered_count_equivalence(ar, en):
    assert check(ar, en).status == "PASS"


@pytest.mark.parametrize("en", [
    EN.replace("orders", "units"), EN.replace("orders", "sales"),
    EN.replace("orders", "net profit"), EN.replace("orders", "cancelled orders"),
    EN.replace("registered", "operated"), EN.replace("registered", "completed"),
    EN.replace("in each branch", "in the Maadi branch"),
    EN.replace("in each branch", "in the Nasr City branch"),
    EN.replace("in each branch", "in all branches combined"),
    EN.replace("October", "September"), EN.replace("2026", "2027"),
    EN.replace("How many orders", "Which best branch's orders"),
    EN[:-1] + " excluding returns?", EN[:-1] + " above 500 EGP?",
    EN[:-1] + " in USD?", EN[:-1] + " and November 2026?",
    EN.replace("were registered", "were not registered"),
    EN.replace("How many orders were registered", "Were orders registered"),
])
def test_registered_count_changed_meaning_stays_blocked(en):
    assert check(en=en).status != "PASS"


@pytest.mark.parametrize("constraint", [
    " من غير المرتجعات", " المكتملة", " فوق 500 جنيه", " بالدولار",
    " و نوفمبر 2026", " لفرع المعادي", " مش المسجلة",
])
def test_missing_source_constraints_stay_blocked(constraint):
    assert check(ar=AR + constraint).status != "PASS"


@pytest.mark.parametrize("changes", [
    {"metric": "total_sales"}, {"aggregation_shape": "overall"},
    {"branch_name": "Maadi"}, {"date_start": "2026-09-01"},
    {"date_end_exclusive": "2026-12-01"},
])
def test_registered_form_never_overrides_bir(changes):
    assert check(changes=changes).status == "MISMATCH"


@pytest.mark.parametrize("ar", ["مين أحسن فرع في أكتوبر 2026؟", "صافي الربح لكل فرع كام؟"])
def test_ambiguity_and_unsupported_metric_stay_blocked(ar):
    assert check(ar=ar).status != "PASS"
