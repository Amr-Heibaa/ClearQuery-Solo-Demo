"""Trusted orchestration; no database executor is imported or dispatched."""
from uuid import uuid4
from time import perf_counter
from clearquery.contracts import (AsrResult, ConfirmationResult, TranslationResult, BIR, Decision,
    SqlCandidate, InferenceMetadata, ValidationResult, TranslationConsistencyResult, consume)
from clearquery.voice import confirm
from clearquery.intent import parse
from clearquery.decision import decide
from clearquery.translation.gate import BoundedSemanticGate
from clearquery.sql_guard import SqlGuard


class Pipeline:
    def __init__(self, translator, generator, *, asr=None, gate=None, guard=None):
        self.translator, self.generator, self.asr = translator, generator, asr
        self.gate = gate or BoundedSemanticGate()
        self.guard = guard or SqlGuard()

    def run(self, *, text_ar=None, confirmed=False, audio=None, confirmation_callback=None, asr_result=None):
        # UI continuation consumes a server-owned, previously produced ASR result.
        # It revalidates G0 and still requires explicit confirmation; no ASR replay
        # adapter, transcript rewriting, or second model inference is involved.
        trace = asr_result.trace_id if isinstance(asr_result, AsrResult) else str(uuid4())
        result = {"contract_version": "1.1", "trace_id": trace, "stages": [], "stop_reason": None, "executed": False}
        active = "input"
        started = last_stage = perf_counter()
        result["model_memory"] = []
        from clearquery.model_runtime import environment
        result["runtime_environment"] = environment()
        def release(component):
            if hasattr(component, "unload"):
                result["model_memory"].append(component.unload())
        def stage(name, cls, value):
            nonlocal active, last_stage
            active = name
            obj = consume(cls, value, trace)
            result["stages"].append({"stage": name, "trace_id": trace, "result": obj.model_dump(),
                                     "error": obj.model_dump().get("error"), "elapsed_ms": round((perf_counter() - last_stage) * 1000, 2)})
            last_stage = perf_counter()
            return obj
        def stop(reason):
            result["stop_reason"] = reason
            result["runtime_ms"] = round((perf_counter() - started) * 1000, 2)
            return result
        try:
            if sum(value is not None for value in (text_ar, audio, asr_result)) != 1:
                raise ValueError("Choose exactly one text, audio, or existing ASR result")
            if audio is not None or asr_result is not None:
                active = "asr"
                if asr_result is not None:
                    if not isinstance(asr_result, AsrResult):
                        raise ValueError("Existing ASR must be a validated server-owned AsrResult")
                    asr = stage("asr", AsrResult, asr_result)
                    result["asr_resumed"] = True
                else:
                    if self.asr is None:
                        raise ValueError("ASR adapter is not configured")
                    try:
                        asr = stage("asr", AsrResult, self.asr.transcribe(audio, trace))
                    finally:
                        release(self.asr)
                if asr_result is None and hasattr(self.asr, "metadata"):
                    result["stages"].append({"stage": "asr_metadata", "trace_id": trace,
                        "result": self.asr.metadata(), "error": None})
                if asr.error: return stop("ASR_FAILED")
                raw = asr.transcript_ar_raw
                accepted, edited = confirmation_callback(raw) if confirmation_callback else (False, None)
                confirmation = confirm(raw, trace, accepted=accepted, edited=edited)
            else:
                accepted, edited = (True, None) if confirmed else confirmation_callback(text_ar) if confirmation_callback else (False, None)
                confirmation = confirm(text_ar, trace, accepted=accepted, edited=edited)
            confirmation = stage("confirmation", ConfirmationResult, confirmation)
            if not confirmation.user_confirmed: return stop("TRANSCRIPT_UNCONFIRMED")
            question = confirmation.question_ar
            bir = stage("bir", BIR, parse(question, trace)[0])
            decision = stage("decision", Decision, decide(bir))
            # Frozen G0: CLARIFY/ABSTAIN stop before translation and generation.
            if decision.decision != "ANSWER": return stop(decision.decision)
            active = "translation"
            try:
                translation = stage("translation", TranslationResult, self.translator.translate(question, trace))
            finally:
                release(self.translator)
            if hasattr(self.translator, "metadata"):
                result["stages"].append({"stage": "translation_metadata", "trace_id": trace,
                    "result": self.translator.metadata(), "error": None})
            if translation.error: return stop("TRANSLATION_FAILED")
            consistency = stage("semantic_gate", TranslationConsistencyResult, self.gate.check(question, bir, translation))
            if consistency.status != "PASS": return stop(consistency.error.code)
            active = "sql_generation"
            try:
                candidate, metadata = self.generator.generate(translation.text_en, bir)
            finally:
                release(self.generator)
            candidate = stage("sql_candidate", SqlCandidate, candidate)
            stage("inference_metadata", InferenceMetadata, metadata)
            if candidate.error: return stop("SQL_GENERATION_FAILED")
            validation = stage("sql_guard", ValidationResult, self.guard.review(candidate.candidate_sql, bir))
            if validation.reviewed_sql != candidate.candidate_sql:
                raise ValueError("Guard changed candidate SQL")
            if not validation.validated: return stop("UNSAFE_SQL")
            return stop("REVIEWED_SQL_EXECUTION_DISABLED")
        except Exception as exc:
            code = str(exc) if str(exc) in {"TRACE_ID_MISMATCH", "CONTRACT_VERSION_MISMATCH"} else (
                "SQL_GENERATION_FAILED" if active == "sql_generation" else "INVALID_COMPONENT_OUTPUT")
            result["stages"].append({"stage": active, "trace_id": trace, "result": None,
                "error": {"code": code, "message": f"{type(exc).__name__}: {exc}"}})
            return stop(code)
