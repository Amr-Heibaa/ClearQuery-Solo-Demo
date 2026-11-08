from typing import Protocol
from clearquery.contracts import BIR, TranslationResult, TranslationConsistencyResult, SemanticCheck, Error
from clearquery.intent import parse
from clearquery.translation.question_forms import match_question


class SemanticGate(Protocol):
    def check(self, question_ar: str, bir: BIR, translation: TranslationResult) -> TranslationConsistencyResult: ...


class BoundedSemanticGate:
    """Compare both languages independently; unfamiliar phrasing fails closed."""
    def check(self, question_ar, bir, translation):
        checks = []
        def add(field, status):
            checks.append(SemanticCheck(field=field, status=status))
        add("source_identity", "PASS" if translation.text_ar_original == question_ar == bir.question_ar else "MISMATCH")
        if translation.error or not translation.text_en:
            add("translation", "UNVERIFIABLE")
        else:
            original, ar_covered = parse(question_ar, bir.trace_id)
            translated, en_covered = parse(translation.text_en, bir.trace_id, english=True)
            # A narrow bilingual whole-question interpretation, not new global
            # word aliases. Both sides must be fully understood; the authoritative
            # BIR and every existing slot/source check still have to agree.
            source_form = match_question(question_ar, bir.trace_id, english=False)
            target_form = match_question(translation.text_en, bir.trace_id, english=True)
            if source_form is not None and target_form is not None:
                original, translated = source_form, target_form
                ar_covered = en_covered = True
            for field in ["workspace", "entity", "metric", "aggregation_shape", "branch_name", "date_start", "date_end_exclusive", "missing_slots", "unsupported_reason"]:
                if getattr(original, field) != getattr(bir, field):
                    add("bir." + field, "MISMATCH")
                add(field, "PASS" if getattr(original, field) == getattr(translated, field) else "MISMATCH")
            add("qualifiers_numbers_units_and_scope", "PASS" if ar_covered and en_covered else "UNVERIFIABLE")
        status = "MISMATCH" if any(c.status == "MISMATCH" for c in checks) else "UNVERIFIABLE" if any(c.status == "UNVERIFIABLE" for c in checks) else "PASS"
        return TranslationConsistencyResult(trace_id=bir.trace_id, status=status, checks=checks,
            error=None if status == "PASS" else Error(code="TRANSLATION_SEMANTIC_" + status,
                message="Could not verify: " + ", ".join(c.field for c in checks if c.status != "PASS")))
