# ClearQuery G0 interface contract v1.1

Final freeze candidate · Team sign-off pending · Sales 10% demo · Backend only · Synthetic data

This document specifies the proposed wire contract. Amr's preliminary agreement covers metric IDs, the shipped executor signature, separate Gold and Guard tests, and preservation of validated SQL. Freeze requires review by Amr, Abdallah and Selim. Phase 2b execution requires separate authorization.

## 1. Common fields

Every v1.1 object includes `contract_version: Literal["1.1"]` and `trace_id: str`. The orchestrator creates the trace ID and checks both fields before consuming component outputs. The schemas below list the remaining required keys. A nullable key is present with null when unavailable. Reject malformed values and conflicting fields before dispatch. An error object contains `code: str` and `message: str`.

## 2. Voice and confirmation

| Object | Fields |
|---|---|
| AsrResult | audio_id: str; transcript_ar_raw: str or null; confidence: float in [0,1] or null; engine: str; error: Error or null |
| ConfirmationResult | transcript_ar_raw: str; question_ar: str or null; user_confirmed: bool; edited_flag: bool |
| TranslationResult | text_ar_original: str; text_en: str or null; engine: str; error: Error or null |

ASR failure carries ASR_FAILED and null transcript. A confirmed question is nonempty and reflects the user's final edit after STT. The edited flag compares confirmed text with the raw transcript. A pending confirmation carries null question_ar and stops dispatch. Translation receives question_ar and returns it exactly in text_ar_original. Success requires nonempty text_en. Failure carries TRANSLATION_FAILED and null text_en. Selim confirmed the voice fields and supplied fresh-run records.

## 3. Business intent and decision

### Translation consistency gate

After ANSWER, the orchestrator checks the translation against the exact confirmed Arabic and its resolved BIR before calling Text-to-SQL. Matching `text_ar_original` proves source identity only. The gate also checks year and date range, numbers and their units, metric, entity and branch, aggregation and ranking direction, and meaning-bearing qualifiers such as negation or scope. Normalize equivalent representations, including Arabic digits and canonical branch aliases. Compare meaning within supported Sales phrasing. If a required comparison cannot be verified, fail closed.

`TranslationConsistencyResult` carries the common version and trace fields, `status` (PASS, MISMATCH, UNVERIFIABLE), `checks` (field and status per comparison), and nullable `error`. PASS requires every applicable check to pass. MISMATCH returns `TRANSLATION_SEMANTIC_MISMATCH` and UNVERIFIABLE returns `TRANSLATION_SEMANTIC_UNVERIFIABLE`, each with a message and affected field in the checks. Both outcomes stop before SQL generation, guard review and execution. Preserve the original Arabic and BIR. The policy decision and the translation-stage outcome remain separate in the trace. Engine failures continue to use `TRANSLATION_FAILED`.

| BIR field | Type and meaning |
|---|---|
| question_ar | str, exact confirmed Arabic |
| workspace | literal sales, assigned from authorized server context |
| entity | literal orders or null |
| metric | total_sales, order_count or null |
| aggregation_shape | overall, by_branch, top_branch or null |
| branch_name | canonical catalog string or null |
| date_start, date_end_exclusive | ISO calendar date strings or null |
| missing_slots | list of unresolved field names |
| unsupported_reason | policy reason code or null |

Use confirmed Arabic and the supplied branch catalog. Null branch_name represents all branches only if branch_name is absent from missing_slots. total_sales sums recorded order amounts in EGP. order_count counts order IDs. The configured reference date is 2026-11-01. October uses [2026-10-01, 2026-11-01). Unsupported business metrics include profit, returns, units and net sales.

Decision fields are decision (ANSWER, CLARIFY or ABSTAIN), reason_code (from §6), and clarification_question (str or null). ANSWER requires a resolved, supported intent. CLARIFY requires a nonempty question. For multiple missing slots, ask about metric, shape, branch, then time in that order, one per turn. The initial demo stops at CLARIFY. A future continuation requires an explicit user response and a fresh policy check. 

## 4. SQL candidate and validation

| Object | Fields |
|---|---|
| SqlCandidate | raw_model_output: str or null; candidate_sql: str or null; model_id: str; model_revision: str or null; error: Error or null |
| ValidationResult | reviewed_sql: str; validated: bool; rejected_reason: GuardCode or null; checks: list of Check |
| Check | name: str; status: PASS, FAIL or NOT_RUN |

Successful generation requires nonempty candidate_sql and null error. Failed generation carries a structured adapter error per C3 and null candidate_sql. The candidate schema has no approval field. The guard copies candidate_sql verbatim into reviewed_sql for both approval and rejection. Approval requires all mandatory checks to pass and null rejected_reason. Rejection requires a reason code and stops execution.

### C1–C4 alignment with the shipped adapter

1. **C1 — Generator input.** Use the current instance method `generate_sql(english_question, resolved_bir, schema_context, trace_id, *, schema_context_id=...)`. The orchestration boundary projects the authoritative BIR into the adapter shape after ANSWER and translation PASS. Map metric to resolved_metric, dates to time_range.start/end (exclusive end), canonical branch to filters.branch, branch grouping to dimensions, and top_branch to intent_type=rank, dimensions=[branch], and rank={order: desc, limit: 1}. Non-ranking projections carry null intent_type and rank. Set ambiguity_flags to an empty list only for a resolved request. The separate policy Decision remains authoritative.
2. **C2 — Inference metadata.** Map raw_output to raw_model_output without trimming it. Keep candidate_sql, model_id and model_revision in SqlCandidate. Carry quantization, prompt_template_id, schema_context_id, prompt_sha256, generation_params, inference_ms, prompt_tokens and output_tokens in a separate InferenceMetadata object with version and trace. The schema context ID is a label, so its integrity remains a separate open point.
3. **C3 — Errors.** Preserve PRECONDITION_FAILED, MODEL_NOT_LOADED, INFERENCE_FAILED and NO_SQL_IN_OUTPUT in the candidate error. SQL_GENERATION_FAILED is the outer failure category. On error, candidate_sql is null. NO_SQL_IN_OUTPUT can retain decoded raw output and observed inference metadata. Accept p1-t2s-0.1 only through the explicit compatibility mapping. Validate the trace before mapping to 1.1. Person 1’s T4 adapter validates incomplete rank/time_range before prompt construction. A future caller must fail closed on adapter exceptions.
4. **C4 — SQL syntax.** Permit one optional trailing semicolon, reject comments, and reject all CTEs. Extraction is upstream of the guard. Guard approval is for the exact candidate SQL. The validator may inspect a parsed representation, but the trusted executor adapter receives the original reviewed text.

Check order: parse and single statement, comments and statement type, reviewed SQL shape, allowed relations and columns, allowed functions, then consistency with BIR and authorized workspace. A failed prerequisite marks dependent checks NOT_RUN. Use PostgreSQL semantics. Approved relations are analytics.orders and analytics.branches. Unqualified equivalents require the controlled analytics search path. Compare metric, branch scope, joins, grouping, ranking and half-open dates. Preserve zero-order branches for per-branch queries and ties for top-branch queries where supported by the agreed shape. Reject SQL outside reviewed shapes. For overall and per-branch total_sales, require the Gold zero-on-empty COALESCE(SUM(...), 0). Accept equivalent qualified and unqualified columns in the approved single unaliased orders view shape. Preserve the exact reviewed SQL text.

## 5. Executor adapter and stage order

Authorize Sales → ASR → explicit confirmation → BIR → decision → ANSWER only: translation → translation consistency gate → PASS only: generator → guard → approved only: trusted executor adapter. CLARIFY and ABSTAIN make zero generator and executor calls.

Use the shipped signature `execute_readonly(request, settings=None, *, allowed_validators=DEFAULT_ALLOWED_VALIDATORS)`. Shipped ValidatedSql contains trace_id, sql, validated, validator and rejected_reason. The future adapter constructs it within the trusted orchestration path from an approved ValidationResult, with the same trace ID and exact reviewed SQL. External inputs cannot supply ValidatedSql, choose the validator allowlist or override approval. The existing Phase 2a executor trusts its caller. A forged validation marker therefore has to be rejected at the orchestration boundary, with DB privileges providing a separate layer.

Phase 2a accepts the gold_fixture validator by default and compares SQL to the fixture loader's text. Gold executor tests use that text including comments. Guard tests use separate comment-free fixture bodies. For Phase 2b, the owner must authorize sql_guard separately before any model-generated execution.

Shipped SqlResult fields are contract_version, trace_id, sql, validated, rejected_reason, rows, row_count, columns, elapsed_ms, executed, truncated and error. Treat its source version as 0.1-draft at the adapter boundary. Validate the trace, preserve error details and expose v1.1 in the outer integration result. Serialize Decimal as a string and counts as integers. A non-null error takes precedence over executed when reporting success. A truncated result is labelled partial.

## 6. Reason codes proposed for freeze

| Layer | Codes and meanings |
|---|---|
| ANSWER | INTENT_RESOLVED |
| CLARIFY | METRIC_UNRESOLVED, SHAPE_UNRESOLVED, UNKNOWN_ENTITY for an unresolved requested branch, TIME_UNRESOLVED |
| ABSTAIN | WRITE_REQUEST, OUT_OF_WORKSPACE, UNSUPPORTED_INTENT, MISSING_DATA for an unsupported metric that requires unavailable data |
| Guard rejection | PRECONDITION_FAILED for a non-ANSWER intent or BIR that cannot produce a supported reference query, SQL_PARSE_ERROR, MULTIPLE_STATEMENTS, SQL_COMMENTS, STATEMENT_NOT_ALLOWED, SHAPE_NOT_ALLOWED, RELATION_NOT_ALLOWED, COLUMN_NOT_ALLOWED, FUNCTION_NOT_ALLOWED, BIR_SQL_MISMATCH |
| Integration failure | CONTRACT_VERSION_MISMATCH, TRACE_ID_MISMATCH, INVALID_COMPONENT_OUTPUT, TRANSCRIPT_UNCONFIRMED, ASR_FAILED, TRANSLATION_FAILED, TRANSLATION_SEMANTIC_MISMATCH, TRANSLATION_SEMANTIC_UNVERIFIABLE, SQL_GENERATION_FAILED, UNSAFE_SQL |
| Shipped executor rejection | NOT_VALIDATED, VALIDATOR_NOT_ENABLED, NOT_A_GOLD_FIXTURE |
| Shipped database error | DB_ERROR, DB_TIMEOUT |

For a guard rejection, surface UNSAFE_SQL at the integration layer and retain the original guard code. Keep the policy decision and execution outcome separately visible. Never display a numeric answer for an execution error.

## 7. Component evidence and remaining checks

- Person 1 reported 116 adapter tests passing. Person 3 independently checked all five Gold BIR projections and both malformed BIR regressions against T4. Missing rank.order and time_range.start returned PRECONDITION_FAILED with null SQL and zero prompt calls.
- The saved real September XiYanSQL candidate passed the v1.1 mapper with matching SQL and raw-output hashes, preserved trace, and separate inference metadata. The current Guard rejects the sample's bare SUM. Review a new model candidate after prompt alignment with the agreed zero-on-empty requirement.
- Person 3 ran 14 local tests. The fixture demo verified the successful Guard path, wrong-metric rejection, and zero generation calls for failed translation, CLARIFY and ABSTAIN. Qualification tests covered accepted orders column qualifiers, invalid qualifiers, wrong dates, wrong metrics and bare SUM.
- Selim confirmed a fresh Colab run. The saved execution counts are consistent with his report. Dependency conflict warnings were reported. ASR and translation calls completed. Person 3 verified P2-A01's shared trace, version 1.1, raw transcript identity, explicit confirmation and exact translation source match. Audio and transcripts remain in the private evidence package.
- Person 3 replayed the saved P2-A01 result and five typed outputs locally. A01 and typed T01 produced MISMATCH. Typed T02 and T03 produced UNVERIFIABLE. Typed T04 produced CLARIFY and typed T05 produced ABSTAIN before the gate. Every case had zero generator spy calls. The separate earlier applications-for-orders fixture also produced MISMATCH. Fixture labels are scoped to their source package.
- The implemented translation gate covers a bounded English Sales grammar. Unrecognized phrasing stops as UNVERIFIABLE. The current local dispatch harness uses fixtures. Live authorization, real voice-to-SQL execution, executor handoff and live bypass testing are separate milestones.

## 8. Freeze review

This file, `ClearQuery_G0_v1.1_Final_Freeze_Candidate.md`, is the single authoritative candidate for sign-off by Amr, Abdallah and Selim. Use this revision when recording approval. Earlier drafts and contract copies included in component ZIPs serve as historical references. The freeze takes effect when all three confirm this revision.

At the v1.1 serialization boundary, convert each internal `ValidationResult.checks` tuple pair `(name, status)` into a Check object `{"name": name, "status": status}` in a JSON list. Preserve ordering and status values (PASS, FAIL, NOT_RUN). An empty tuple serializes as an empty list. This is a wire-format conversion and preserves the trace ID, approval result, rejection reason and exact reviewed SQL. The current fixture harness uses internal tuples. The future integration adapter applies this conversion.

Amr confirmed that overall total_sales returns zero when no orders match. Prompt alignment will request COALESCE(SUM(...), 0) where applicable and preserve the Gold top_branch behavior. The September rerun is pending. Review its exact candidate with the current Guard and preserve the observed result, including a rejection if the model emits bare SUM again.

Amr, Abdallah and Selim confirm this candidate together. Record the freeze decision and date after all three agree. Confirm the fresh Colab dependency setup and review the next model candidate before planning the live run. Phase 2b authorization remains a separate decision.
