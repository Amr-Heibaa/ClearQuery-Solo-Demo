"""Necessary adapter compatibility regressions. Stubs here are isolated tests."""
from types import SimpleNamespace
from clearquery.contracts import TranslationResult
from clearquery.intent import parse
from clearquery.translation.gate import BoundedSemanticGate
from clearquery.orchestrator import Pipeline
from clearquery.translation import QwenTranslation
from clearquery.text_to_sql import XiYanG0
from clearquery.voice import WhisperASR


def test_recorded_sales_phrasing_preserves_supported_semantics():
    for ar, en in [
        ("كام إجمالي المبيعات المسجلة لكل فرع في أكتوبر 2026؟", "What is the total recorded sales for each branch in October 2026?"),
        ("أنهي فرع حقق أعلى إجمالي مبيعات مسجلة في أكتوبر 2026؟", "Which branch achieved the highest total recorded sales in October 2026?"),
    ]:
        bir, covered = parse(ar, "test")
        assert covered
        result = BoundedSemanticGate().check(ar, bir, TranslationResult(trace_id="test", text_ar_original=ar, text_en=en, engine="TEST_STUB", error=None))
        assert result.status == "PASS"


def test_translation_released_on_adapter_exception_before_safe_stop():
    events = []
    class Broken:
        def translate(self, *args):
            events.append("translate")
            raise RuntimeError("TEST_STUB failed")
        def unload(self):
            events.append("release")
            return {"component":"TEST_STUB"}
    generator = SimpleNamespace(generate=lambda *args:events.append("MUST_NOT_GENERATE"))
    result = Pipeline(Broken(), generator).run(text_ar="اجمالي المبيعات لكل فرع في اكتوبر 2026", confirmed=True)
    assert events == ["translate", "release"]
    assert result["stop_reason"] == "INVALID_COMPONENT_OUTPUT"


def test_model_references_are_released_without_changing_person1_code():
    qwen, xiyan, whisper = QwenTranslation(), XiYanG0(), WhisperASR()
    qwen.model = qwen.tokenizer = object()
    xiyan.adapter = SimpleNamespace(model=object(), tokenizer=object())
    whisper._model = object()
    for adapter in (qwen, xiyan, whisper): adapter.unload()
    assert qwen.model is qwen.tokenizer is xiyan.adapter.model is xiyan.adapter.tokenizer is whisper._model is None


def test_audio_confirmation_file_rejects_wrong_hash_before_inference(tmp_path):
    import json
    import subprocess
    import sys
    audio = tmp_path / "synthetic.ogg"
    audio.write_bytes(b"TEST_STUB_NOT_REAL_AUDIO")
    approval = tmp_path / "confirmation.json"
    approval.write_text(json.dumps({"audio_sha256":"wrong", "user_confirmed":True}), encoding="utf-8")
    result = subprocess.run([sys.executable,"-m","clearquery","--audio",str(audio),"--confirmation-file",str(approval)],
                            capture_output=True,text=True,encoding="utf-8")
    assert result.returncode == 2
    assert "exact audio hash" in result.stderr
