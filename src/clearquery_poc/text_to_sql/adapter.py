"""Text-to-SQL adapter (PROVISIONAL contract ``p1-t2s-0.1``).

``generate_sql(english_question, resolved_bir, schema_context, trace_id) -> SqlCandidate``

* Real inference only, with the pinned XiYanSQL-QwenCoder-7B-2504 weights
  loaded in 4-bit NF4 (bitsandbytes) on CUDA.
* Deterministic decoding (greedy) so results are reproducible.
* The output is a *candidate*: never validated, never executed here.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from . import prompts
from .extract import extract_sql

CONTRACT_VERSION = "p1-t2s-0.1"
ROOT = Path(__file__).resolve().parents[3]
MODEL_LOCK = ROOT / "models" / "xiyan_7b_2504.lock.json"
HF_CACHE = ROOT / ".hf-cache"

GENERATION_PARAMS = {"max_new_tokens": 512, "do_sample": False, "num_beams": 1, "repetition_penalty": 1.0}


@dataclass(frozen=True)
class SqlCandidate:
    contract_version: str
    trace_id: str
    model_id: str
    model_revision: str
    quantization: str
    prompt_template_id: str
    schema_context_id: str
    prompt_sha256: str | None
    generation_params: dict[str, Any]
    raw_output: str | None
    candidate_sql: str | None
    inference_ms: float | None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    error: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PreconditionError(ValueError):
    pass


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# The only ranking shape approved for the demo: the single top branch.
APPROVED_RANK = {"order": "desc", "limit": 1}
RANK_INTENT = "rank"
ALLOWED_DIMENSIONS = frozenset({"branch"})


def _iso_date(value: Any, name: str) -> date:
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        raise PreconditionError(f"time_range.{name} {value!r} is not an ISO date (YYYY-MM-DD)")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise PreconditionError(f"time_range.{name} {value!r} is not a valid calendar date") from None


def _check_time_range(tr: Any) -> None:
    if tr is None:
        raise PreconditionError("time_range is not resolved")
    if not isinstance(tr, dict):
        raise PreconditionError(f"time_range must be a dict, got {type(tr).__name__}")
    for key in ("start", "end"):
        if tr.get(key) is None:
            raise PreconditionError(f"time_range.{key} is missing")
    start, end = _iso_date(tr["start"], "start"), _iso_date(tr["end"], "end")
    if not start < end:
        raise PreconditionError(f"time_range.start {tr['start']} must be earlier than end {tr['end']}")


def _check_rank(bir: dict[str, Any]) -> None:
    rank, intent = bir.get("rank"), bir.get("intent_type")
    if intent != RANK_INTENT:
        if rank is not None:
            raise PreconditionError(f"rank must be None for intent_type {intent!r}, got {rank!r}")
        return
    if not isinstance(rank, dict):
        raise PreconditionError(f"intent_type 'rank' requires a rank dict with order and limit, got {rank!r}")
    for key in ("order", "limit"):
        if key not in rank or rank[key] is None:
            raise PreconditionError(f"rank.{key} is missing")
    # type() check so that limit=True (bool is an int subclass) is not accepted as 1.
    if rank["order"] != APPROVED_RANK["order"] or type(rank["limit"]) is not int \
            or rank["limit"] != APPROVED_RANK["limit"]:
        raise PreconditionError(f"rank {{'order': {rank['order']!r}, 'limit': {rank['limit']!r}}} is not the "
                                f"approved top-branch shape {APPROVED_RANK}")
    if "branch" not in (bir.get("dimensions") or []):
        raise PreconditionError("intent_type 'rank' requires 'branch' in dimensions")


def _check_dimensions_and_filters(bir: dict[str, Any]) -> None:
    dims = bir.get("dimensions")
    if dims is not None:
        if not isinstance(dims, list) or not all(isinstance(d, str) for d in dims):
            raise PreconditionError(f"dimensions must be a list of strings, got {dims!r}")
        unknown = set(dims) - ALLOWED_DIMENSIONS
        if unknown:
            raise PreconditionError(f"unsupported dimensions: {sorted(unknown)}")
    filters = bir.get("filters")
    if filters is not None:
        if not isinstance(filters, dict):
            raise PreconditionError(f"filters must be a dict, got {type(filters).__name__}")
        branch = filters.get("branch")
        if branch is not None and (not isinstance(branch, str) or not branch.strip()):
            raise PreconditionError(f"filters.branch must be a non-empty string, got {branch!r}")


def check_preconditions(bir: dict[str, Any]) -> None:
    """generate_sql may only run for a resolved, well-formed ANSWER BIR (CLARIFY/ABSTAIN stop
    earlier). Every field the prompt builder reads is validated here, so a malformed BIR fails
    closed with PRECONDITION_FAILED before any prompt is built or model is called."""
    if not isinstance(bir, dict):
        raise PreconditionError("resolved_bir must be a dict")
    if bir.get("decision") != "ANSWER":
        raise PreconditionError(f"decision is {bir.get('decision')!r}, not ANSWER")
    metric = bir.get("resolved_metric")
    if not isinstance(metric, str) or metric not in prompts.METRIC_DEFINITIONS:
        raise PreconditionError(f"resolved_metric {metric!r} is not a catalog metric")
    if bir.get("ambiguity_flags"):
        raise PreconditionError(f"unresolved ambiguity: {bir['ambiguity_flags']}")
    _check_time_range(bir.get("time_range"))
    _check_dimensions_and_filters(bir)
    _check_rank(bir)


def load_lock() -> dict[str, Any]:
    return json.loads(MODEL_LOCK.read_text(encoding="utf-8"))


@dataclass
class XiYanAdapter:
    lock: dict[str, Any] = field(default_factory=load_lock)
    model: Any = None
    tokenizer: Any = None
    load_stats: dict[str, Any] = field(default_factory=dict)

    @property
    def model_id(self) -> str:
        return self.lock["repo_id"]

    @property
    def revision(self) -> str:
        return self.lock["revision"]

    def load(self) -> dict[str, Any]:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is not available")
        torch.cuda.reset_peak_memory_stats()
        t0 = time.perf_counter()
        qconf = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                   bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True)
        common = dict(revision=self.revision, cache_dir=str(HF_CACHE), local_files_only=True)
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_id, **common)
        self.model = AutoModelForCausalLM.from_pretrained(
            self.model_id, quantization_config=qconf, device_map={"": 0}, dtype=torch.float16, **common)
        self.model.eval()
        torch.cuda.synchronize()
        self.load_stats = {
            "load_seconds": round(time.perf_counter() - t0, 2),
            "cuda_max_allocated_mib": round(torch.cuda.max_memory_allocated() / 2**20, 1),
            "cuda_max_reserved_mib": round(torch.cuda.max_memory_reserved() / 2**20, 1),
            "model_memory_footprint_mib": round(self.model.get_memory_footprint() / 2**20, 1),
            "quantization": "bitsandbytes nf4, double quant, compute fp16",
            "device": torch.cuda.get_device_name(0),
        }
        return self.load_stats

    def _candidate(self, trace_id: str, **kw: Any) -> SqlCandidate:
        base = dict(contract_version=CONTRACT_VERSION, trace_id=trace_id, model_id=self.model_id,
                    model_revision=self.revision, quantization="nf4",
                    prompt_template_id=prompts.PROMPT_TEMPLATE_ID,
                    schema_context_id=kw.pop("schema_context_id"),
                    generation_params=dict(GENERATION_PARAMS), prompt_sha256=None,
                    raw_output=None, candidate_sql=None, inference_ms=None)
        base.update(kw)
        return SqlCandidate(**base)

    def generate_sql(self, english_question: str, resolved_bir: dict[str, Any],
                     schema_context: str, trace_id: str, *, schema_context_id: str = "unspecified") -> SqlCandidate:
        try:
            check_preconditions(resolved_bir)
        except PreconditionError as exc:
            return self._candidate(trace_id, schema_context_id=schema_context_id,
                                   error={"code": "PRECONDITION_FAILED", "message": str(exc)})
        if self.model is None or self.tokenizer is None:
            return self._candidate(trace_id, schema_context_id=schema_context_id,
                                   error={"code": "MODEL_NOT_LOADED", "message": "call load() first"})

        try:
            prompt = prompts.build_prompt(english_question, resolved_bir, schema_context)
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            # Defence in depth: check_preconditions should make this unreachable.
            return self._candidate(trace_id, schema_context_id=schema_context_id,
                                   error={"code": "PRECONDITION_FAILED",
                                          "message": f"malformed BIR: {type(exc).__name__}: {exc}"})
        psha = prompts.sha256(prompt)
        try:
            import torch
            text = self.tokenizer.apply_chat_template([{"role": "user", "content": prompt}],
                                                      tokenize=False, add_generation_prompt=True)
            inputs = self.tokenizer([text], return_tensors="pt").to(self.model.device)
            torch.cuda.synchronize()
            t0 = time.perf_counter()
            with torch.inference_mode():
                out = self.model.generate(**inputs, pad_token_id=self.tokenizer.pad_token_id,
                                          eos_token_id=self.tokenizer.eos_token_id,
                                          temperature=None, top_p=None, top_k=None, **GENERATION_PARAMS)
            torch.cuda.synchronize()
            ms = round((time.perf_counter() - t0) * 1000, 1)
            new_ids = out[0][inputs["input_ids"].shape[1]:]
            raw = self.tokenizer.decode(new_ids, skip_special_tokens=True)
        except Exception as exc:  # fail closed: visible error, no SQL
            return self._candidate(trace_id, schema_context_id=schema_context_id, prompt_sha256=psha,
                                   error={"code": "INFERENCE_FAILED", "message": f"{type(exc).__name__}: {exc}"})
        sql = extract_sql(raw)
        return self._candidate(trace_id, schema_context_id=schema_context_id, prompt_sha256=psha,
                               raw_output=raw, candidate_sql=sql, inference_ms=ms,
                               prompt_tokens=int(inputs["input_ids"].shape[1]), output_tokens=int(new_ids.shape[0]),
                               error=None if sql else {"code": "NO_SQL_IN_OUTPUT",
                                                       "message": "no SQL found in raw model output"})
