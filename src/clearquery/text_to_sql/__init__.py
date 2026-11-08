"""Explicit G0 compatibility boundary around unchanged Person 1 V4 adapter."""
from clearquery.contracts import SqlCandidate, InferenceMetadata
from clearquery.decision import decide
from clearquery_poc.text_to_sql.adapter import XiYanAdapter
from clearquery_poc.text_to_sql.schema_context import SCHEMA_CONTEXT, SCHEMA_CONTEXT_ID


def project(bir):
    if decide(bir).decision != "ANSWER":
        raise ValueError("PRECONDITION_FAILED")
    rank = bir.aggregation_shape == "top_branch"
    return {"decision": "ANSWER", "resolved_metric": bir.metric,
        "time_range": {"start": bir.date_start, "end": bir.date_end_exclusive},
        "dimensions": ["branch"] if bir.aggregation_shape in {"by_branch", "top_branch"} else [],
        "filters": {"branch": bir.branch_name} if bir.branch_name else {},
        "intent_type": "rank" if rank else None,
        "rank": {"order": "desc", "limit": 1} if rank else None, "ambiguity_flags": []}


def map_candidate(value, trace_id):
    data = value.to_dict()
    if data["trace_id"] != trace_id:
        raise ValueError("TRACE_ID_MISMATCH")
    if data["contract_version"] != "p1-t2s-0.1":
        raise ValueError("CONTRACT_VERSION_MISMATCH")
    candidate = SqlCandidate(trace_id=trace_id, raw_model_output=data["raw_output"],
        **{k: data[k] for k in ("candidate_sql", "model_id", "model_revision", "error")})
    metadata = InferenceMetadata(trace_id=trace_id, **{k: data[k] for k in InferenceMetadata.model_fields
        if k not in {"contract_version", "trace_id"}})
    return candidate, metadata


class XiYanG0:
    def __init__(self, *, cache_dir=None, load_model=True):
        self.cache_dir, self.load_model = cache_dir, load_model
        self.adapter = None

    def unload(self):
        from clearquery.model_runtime import collect, memory
        before = memory()
        if self.adapter is not None:
            self.adapter.model = self.adapter.tokenizer = None
        return {"component": "xiyan", "before": before, "after": collect()}

    def generate(self, text_en, bir):
        projected = project(bir)
        if self.adapter is None:
            self.adapter = XiYanAdapter()
        if self.cache_dir is not None:
            # Configure the unmodified adapter's cache, never its prompt or lock.
            from pathlib import Path
            from clearquery_poc.text_to_sql import adapter
            adapter.HF_CACHE = Path(self.cache_dir)
        if self.load_model and self.adapter.model is None:
            try:
                self.adapter.load()
            except Exception as exc:
                failed = self.adapter._candidate(bir.trace_id, schema_context_id=SCHEMA_CONTEXT_ID,
                    error={"code": "MODEL_NOT_LOADED", "message": f"{type(exc).__name__}: {exc}"})
                return map_candidate(failed, bir.trace_id)
        result = self.adapter.generate_sql(text_en, projected, SCHEMA_CONTEXT, bir.trace_id,
                                           schema_context_id=SCHEMA_CONTEXT_ID)
        return map_candidate(result, bir.trace_id)
