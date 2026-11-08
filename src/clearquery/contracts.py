"""Strict G0 v1.1 wire objects; extra metadata lives in separate trace stages."""
from datetime import date
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    contract_version: Literal["1.1"] = "1.1"
    trace_id: str = Field(min_length=1)


class Error(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: str
    message: str


class AsrResult(Wire):
    audio_id: str
    transcript_ar_raw: str | None
    confidence: float | None = Field(ge=0, le=1)
    engine: str
    error: Error | None

    @model_validator(mode="after")
    def valid(self):
        if self.error is not None:
            if self.error.code != "ASR_FAILED" or self.transcript_ar_raw is not None:
                raise ValueError("ASR failure requires ASR_FAILED and null transcript")
        elif not self.transcript_ar_raw or not self.transcript_ar_raw.strip():
            raise ValueError("ASR success requires transcript")
        return self


class ConfirmationResult(Wire):
    transcript_ar_raw: str
    question_ar: str | None
    user_confirmed: bool
    edited_flag: bool

    @model_validator(mode="after")
    def valid(self):
        if self.user_confirmed:
            if not self.question_ar or not self.question_ar.strip():
                raise ValueError("Confirmed question must be nonempty")
            if self.edited_flag != (self.question_ar != self.transcript_ar_raw):
                raise ValueError("edited_flag must compare exact strings")
        elif self.question_ar is not None or self.edited_flag:
            raise ValueError("Pending confirmation carries null question and no edit")
        return self


class TranslationResult(Wire):
    text_ar_original: str
    text_en: str | None
    engine: str
    error: Error | None

    @model_validator(mode="after")
    def valid(self):
        if self.error:
            if self.error.code != "TRANSLATION_FAILED" or self.text_en is not None:
                raise ValueError("Translation failure must carry null text")
        elif not self.text_en or not self.text_en.strip():
            raise ValueError("Translation success requires nonempty text")
        return self


class BIR(Wire):
    question_ar: str = Field(min_length=1)
    workspace: Literal["sales"] = "sales"
    entity: Literal["orders"] | None
    metric: Literal["total_sales", "order_count"] | None
    aggregation_shape: Literal["overall", "by_branch", "top_branch"] | None
    branch_name: str | None
    date_start: str | None
    date_end_exclusive: str | None
    missing_slots: list[str]
    unsupported_reason: Literal["WRITE_REQUEST", "OUT_OF_WORKSPACE", "UNSUPPORTED_INTENT", "MISSING_DATA"] | None

    @model_validator(mode="after")
    def valid(self):
        if self.branch_name not in (None, "Maadi", "Nasr City", "Heliopolis", "Alexandria"):
            raise ValueError("Branch outside synthetic catalog")
        if (self.date_start is None) != (self.date_end_exclusive is None):
            raise ValueError("Incomplete time range")
        if self.date_start is not None:
            if not all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) for v in (self.date_start, self.date_end_exclusive)):
                raise ValueError("Dates must use YYYY-MM-DD")
            if date.fromisoformat(self.date_start) >= date.fromisoformat(self.date_end_exclusive):
                raise ValueError("Invalid half-open dates")
        if self.branch_name and self.aggregation_shape != "overall":
            raise ValueError("Branch filter only supported for overall")
        if set(self.missing_slots) - {"metric", "aggregation_shape", "branch_name", "time"}:
            raise ValueError("Unknown missing slot")
        return self


class Decision(Wire):
    decision: Literal["ANSWER", "CLARIFY", "ABSTAIN"]
    reason_code: Literal["INTENT_RESOLVED", "METRIC_UNRESOLVED", "SHAPE_UNRESOLVED", "UNKNOWN_ENTITY", "TIME_UNRESOLVED", "WRITE_REQUEST", "OUT_OF_WORKSPACE", "UNSUPPORTED_INTENT", "MISSING_DATA"]
    clarification_question: str | None

    @model_validator(mode="after")
    def valid(self):
        if self.decision == "ANSWER" and (self.reason_code != "INTENT_RESOLVED" or self.clarification_question is not None):
            raise ValueError("Conflicting ANSWER")
        clarify_codes = {"METRIC_UNRESOLVED", "SHAPE_UNRESOLVED", "UNKNOWN_ENTITY", "TIME_UNRESOLVED"}
        if self.decision == "CLARIFY" and (self.reason_code not in clarify_codes or not self.clarification_question or not self.clarification_question.strip()):
            raise ValueError("CLARIFY requires reason and question")
        if self.decision == "ABSTAIN" and (self.reason_code not in {"WRITE_REQUEST", "OUT_OF_WORKSPACE", "UNSUPPORTED_INTENT", "MISSING_DATA"} or self.clarification_question is not None):
            raise ValueError("Conflicting ABSTAIN")
        return self


class SemanticCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    field: str
    status: Literal["PASS", "MISMATCH", "UNVERIFIABLE"]


class TranslationConsistencyResult(Wire):
    status: Literal["PASS", "MISMATCH", "UNVERIFIABLE"]
    checks: list[SemanticCheck]
    error: Error | None

    @model_validator(mode="after")
    def valid(self):
        if self.status == "PASS":
            if not self.checks or any(c.status != "PASS" for c in self.checks) or self.error:
                raise ValueError("PASS requires all checks")
        elif not self.error or self.error.code != "TRANSLATION_SEMANTIC_" + self.status:
            raise ValueError("Semantic failure requires matching error")
        elif not any(c.status == self.status for c in self.checks):
            raise ValueError("Semantic failure must identify an affected field")
        return self


class SqlCandidate(Wire):
    raw_model_output: str | None
    candidate_sql: str | None
    model_id: str
    model_revision: str | None
    error: Error | None

    @model_validator(mode="after")
    def valid(self):
        if self.error and self.candidate_sql is not None:
            raise ValueError("Failed candidate must have null SQL")
        if self.error is None and (not self.candidate_sql or not self.candidate_sql.strip()):
            raise ValueError("Successful candidate needs SQL")
        return self


class InferenceMetadata(Wire):
    quantization: str
    prompt_template_id: str
    schema_context_id: str
    prompt_sha256: str | None
    generation_params: dict
    inference_ms: float | None
    prompt_tokens: int | None
    output_tokens: int | None


class Check(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str
    status: Literal["PASS", "FAIL", "NOT_RUN"]


class ValidationResult(Wire):
    reviewed_sql: str
    validated: bool
    rejected_reason: Literal["PRECONDITION_FAILED", "SQL_PARSE_ERROR", "MULTIPLE_STATEMENTS", "SQL_COMMENTS", "STATEMENT_NOT_ALLOWED", "SHAPE_NOT_ALLOWED", "RELATION_NOT_ALLOWED", "COLUMN_NOT_ALLOWED", "FUNCTION_NOT_ALLOWED", "BIR_SQL_MISMATCH"] | None
    checks: list[Check]

    @model_validator(mode="after")
    def valid(self):
        if self.validated:
            if self.rejected_reason or not self.checks or any(c.status != "PASS" for c in self.checks):
                raise ValueError("Approval requires every check to pass")
        elif self.rejected_reason is None:
            raise ValueError("Rejection requires reason")
        return self


def consume(cls, value, trace_id):
    """Revalidate even existing model instances at each component boundary."""
    data = value.model_dump() if isinstance(value, BaseModel) else value
    if isinstance(data, dict) and data.get("contract_version") != "1.1":
        raise ValueError("CONTRACT_VERSION_MISMATCH")
    if isinstance(data, dict) and data.get("trace_id") != trace_id:
        raise ValueError("TRACE_ID_MISMATCH")
    obj = cls.model_validate(data)
    if obj.trace_id != trace_id:
        raise ValueError("TRACE_ID_MISMATCH")
    return obj
