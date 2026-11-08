"""Prompt configuration and isolated safety regressions; no real inference claims."""
import json
from pathlib import Path
import pytest
from clearquery.translation import QwenConfig, TRANSLATION_PROMPT
from clearquery.contracts import TranslationResult
from clearquery.intent import parse
from clearquery.translation.gate import BoundedSemanticGate
from clearquery.decision import decide


def test_local_configs_use_same_general_prompt_without_changing_model_settings():
    root = Path(__file__).resolve().parents[1]
    for name in ("qwen_local.json", "qwen_local_nf4.json"):
        config = QwenConfig(**json.loads((root / "configs" / name).read_text(encoding="utf-8")))
        assert config.prompt == TRANSLATION_PROMPT
        assert config.max_new_tokens == 256 and config.enable_thinking is False
    assert "{question}" in TRANSLATION_PROMPT
    assert "2026" not in TRANSLATION_PROMPT and "A07" not in TRANSLATION_PROMPT


def test_historical_closure_hallucination_still_rejected_without_output_repair():
    ar = "أنهي فرع حقق أعلى إجمالي مبيعات مسجلة في أكتوبر 2026؟"
    raw = "Did the branch that achieved the highest total sales recorded in October 2026 get closed?"
    bir, _ = parse(ar, "isolated-regression")
    value = TranslationResult(trace_id=bir.trace_id, text_ar_original=ar, text_en=raw, engine="TEST_STUB_HISTORIC_FAILURE", error=None)
    result = BoundedSemanticGate().check(ar, bir, value)
    assert result.status == "UNVERIFIABLE"
    assert value.text_en == raw


@pytest.mark.parametrize("case",json.loads((Path(__file__).parent / "fixtures/q5_new_paraphrases.json").read_text(encoding="utf-8"))["cases"])
def test_fresh_paraphrase_policy_and_bir(case):
    bir, _ = parse(case["question_ar"],"new-paraphrase-regression")
    assert decide(bir).decision == case["decision"]
    assert (bir.metric,bir.aggregation_shape,bir.branch_name,bir.date_start) == (
        case["metric"],case["shape"],case["branch"],case["start"])
