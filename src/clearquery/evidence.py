"""Allowlisted shareable evidence; private transcripts stay in local traces."""
def public_evidence(result, database=None):
    stages = {s["stage"]:s.get("result") for s in result.get("stages",[]) if s.get("result")}
    output = {"trace_id":result.get("trace_id"),"contract_version":result.get("contract_version"),
        "stop_reason":result.get("stop_reason"),"runtime_ms":result.get("runtime_ms"),
        "pipeline_executed":result.get("executed",False),"stages":{}}
    allowed = {
        "confirmation":("user_confirmed","edited_flag"),
        "decision":("decision","reason_code"),
        "bir":("workspace","entity","metric","aggregation_shape","branch_name","date_start","date_end_exclusive","missing_slots","unsupported_reason"),
        "semantic_gate":("status",),
        "translation_metadata":("model_id","revision","quantization","status"),
        "asr_metadata":("model","model_sha256","device","language","task","temperature"),
        "sql_candidate":("model_id","model_revision"),
        "inference_metadata":("prompt_template_id","prompt_sha256","schema_context_id","generation_params","quantization","inference_ms"),
        "sql_guard":("validated","rejected_reason"),
    }
    for name,keys in allowed.items():
        if name in stages:
            output["stages"][name] = {k:stages[name][k] for k in keys if k in stages[name]}
    # Deliberately omit free-form errors, raw SQL/model text, audio IDs, file
    # paths, audio hashes, transcripts and generated translations. Even a failed
    # model can echo a secret; a blacklist cannot safely sanitize such strings.
    for stage in result.get("stages",[]):
        if stage.get("error"):
            output["stages"].setdefault(stage["stage"],{})["error_code"] = stage["error"].get("code")
    if database:
        output["database_execution"] = {k:database[k] for k in
            ("trace_id","executed","validated","row_count","truncated","elapsed_ms") if k in database}
        if database.get("error"):
            output["database_execution"]["error_code"] = database["error"].get("code")
    return output
