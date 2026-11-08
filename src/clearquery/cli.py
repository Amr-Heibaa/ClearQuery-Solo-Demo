import argparse
import json
import sys
import hashlib
from pathlib import Path
from clearquery.orchestrator import Pipeline
from clearquery.translation import QwenConfig, QwenTranslation
from clearquery.text_to_sql import XiYanG0
from clearquery.voice import WhisperASR

ROOT = Path(__file__).resolve().parents[2]


def ask(prompt):
    """A closed stdin or cancelled prompt never grants authorization."""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return ""


def print_stages(result):
    """Render actual contract objects without editing any model output."""
    titles = {"asr":"Raw ASR", "confirmation":"Confirmed Arabic", "translation":"English translation",
        "bir":"Business Intent Representation (BIR)", "decision":"Decision policy",
        "semantic_gate":"Translation Gate", "sql_candidate":"Exact generated SQL", "sql_guard":"SQL Guard"}
    fields = {"asr":"transcript_ar_raw", "confirmation":"question_ar", "translation":"text_en", "sql_candidate":"candidate_sql"}
    for stage in result["stages"]:
        name = stage["stage"]
        print(f"\n--- {titles.get(name, name)} | trace={stage['trace_id']} ---")
        value = stage.get("result")
        if value is not None:
            if name in fields:
                # print adds a display newline; the string itself is unchanged.
                print(value.get(fields[name]))
            else:
                print(json.dumps(value, ensure_ascii=False, indent=2))
        if stage.get("error"):
            print("ERROR:",json.dumps(stage["error"],ensure_ascii=False))
    print(f"\nPipeline stopped: {result['stop_reason']} | {result['runtime_ms']:.2f} ms")


def execute_after_authorization(result):
    """Only the actual successful pipeline may request a fresh Guard capability."""
    if result["stop_reason"] != "REVIEWED_SQL_EXECUTION_DISABLED":
        return {"trace_id":result["trace_id"],"executed":False,"status":"PIPELINE_NOT_APPROVED"}
    from clearquery.contracts import BIR, SqlCandidate
    from clearquery.executor.safe_demo import ControlledExecutor
    stages = {item["stage"]:item.get("result") for item in result["stages"]}
    try:
        confirmation, gate, policy = (stages[name] for name in ("confirmation","semantic_gate","decision"))
        candidate, validation, bir = (stages[name] for name in ("sql_candidate","sql_guard","bir"))
        if not (confirmation["user_confirmed"] and gate["status"] == "PASS" and policy["decision"] == "ANSWER"
            and validation["validated"] and validation["reviewed_sql"] == candidate["candidate_sql"]
            and all(stages[name]["trace_id"] == result["trace_id"] for name in
                ("confirmation","semantic_gate","decision","sql_candidate","sql_guard","bir"))):
            raise ValueError("Execution preconditions are not satisfied")
        executor = ControlledExecutor()
        approval = executor.approve(SqlCandidate(**candidate),BIR(**bir))
        print("\nSQL has NOT been executed. Target: verified local synthetic PostgreSQL only.")
        choice = ask("Type RUN APPROVED QUERY to execute this exact SQL once, or Enter to stop: ")
        if choice != "RUN APPROVED QUERY":
            return {"trace_id":result["trace_id"],"executed":False,"status":"EXECUTION_NOT_AUTHORIZED"}
        value = executor.execute(approval).to_contract_dict()
        return {**value,"status":"DB_ERROR" if value["error"] else "EXECUTED" if value["executed"] else "EXECUTION_REJECTED"}
    except Exception:
        # Do not expose connection details or caller-supplied exception strings.
        return {"trace_id":result["trace_id"],"executed":False,"status":"EXECUTION_REJECTED",
            "error":{"code":"EXECUTION_REJECTED","message":"Approval is missing, expired or invalid; database execution stopped."}}


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="ClearQuery backend: real models, strict gates, optional explicitly authorized read-only demo query")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--text-ar", help="Arabic question; requires --confirmed or interactive confirmation")
    mode.add_argument("--audio", type=Path)
    mode.add_argument("--record", action="store_true", help="Record the Windows default microphone: Enter starts/stops, maximum 30 seconds")
    p.add_argument("--confirmed", action="store_true", help="Explicitly confirm typed Arabic (never confirms audio)")
    p.add_argument("--demo", action="store_true", help="Readable backend stages using the existing pinned local model configuration")
    p.add_argument("--execute", action="store_true", help="Offer an explicit execution prompt AFTER Guard approval; never auto-executes")
    p.add_argument("--translation-config", type=Path, help="JSON QwenConfig: exact revision, prompt, device and local cache")
    p.add_argument("--translation-model", default=QwenConfig.model_id)
    p.add_argument("--translation-revision", default=QwenConfig.revision)
    p.add_argument("--model-cache")
    p.add_argument("--xiyan-cache")
    p.add_argument("--device", default="cpu")
    p.add_argument("--asr-device", default="cpu", help="Independent Whisper device; CPU avoids VRAM contention")
    p.add_argument("--whisper-model", default="small")
    p.add_argument("--whisper-cache")
    p.add_argument("--output", type=Path, help="Save actual stage results locally (may contain private transcripts)")
    p.add_argument("--confirmation-file", type=Path, help="Explicit prior human confirmation bound to exact raw ASR and original audio SHA-256")
    args = p.parse_args(argv)
    if args.output and args.output.exists():
        p.error("Refusing to overwrite existing evidence; choose a new --output path")
    if args.demo or args.record:
        if args.translation_config is None:
            args.translation_config = ROOT / "configs/qwen_local_nf4.json"
        if args.xiyan_cache is None:
            cache = ROOT / ".hf-cache"
            args.xiyan_cache = str(cache)
        if args.whisper_cache is None:
            args.whisper_cache = str(ROOT / ".models/whisper")
    if args.xiyan_cache:
        from clearquery.model_runtime import protect_readonly_tree
        external_cache = Path(args.xiyan_cache).resolve()
        if external_cache != (ROOT / ".hf-cache").resolve():
            protect_readonly_tree(external_cache)
    approval = None
    if args.confirmation_file:
        if args.audio is None:
            p.error("--confirmation-file requires --audio")
        approval = json.loads(args.confirmation_file.read_text(encoding="utf-8"))
        with args.audio.open("rb") as original:
            audio_hash = hashlib.file_digest(original, "sha256").hexdigest()
        if approval.get("audio_sha256") != audio_hash or approval.get("user_confirmed") is not True:
            p.error("Confirmation is not explicitly approved for this exact audio hash")
    def interactive(raw):
        print("Raw Arabic transcript (unchanged):", raw)
        if approval is not None:
            if approval.get("transcript_ar_raw") != raw:
                raise ValueError("Raw ASR changed since human review; fresh explicit confirmation is required")
            final = approval.get("question_ar")
            if not isinstance(final, str) or not final.strip():
                raise ValueError("Confirmed Arabic must be nonempty")
            return True, final
        choice = ask("Confirm [y], edit [e], or stop [Enter]: ").strip().lower()
        if choice == "y": return True, None
        if choice == "e":
            edited = ask("Final Arabic text: ")
            if edited.strip() and ask("Confirm this exact edit [y/N]: ").strip().lower() == "y":
                return True, edited
        return False, None
    text = args.text_ar
    confirmed = args.confirmed
    config = QwenConfig(**json.loads(args.translation_config.read_text(encoding="utf-8"))) if args.translation_config else QwenConfig(
        model_id=args.translation_model, revision=args.translation_revision, device=args.device, cache_dir=args.model_cache)
    if (args.demo or args.record) and config.cache_dir and not Path(config.cache_dir).is_absolute():
        from dataclasses import replace
        config = replace(config,cache_dir=str(ROOT/config.cache_dir))
    pipeline = Pipeline(QwenTranslation(config), XiYanG0(cache_dir=args.xiyan_cache),
                        asr=WhisperASR(args.whisper_model, args.asr_device, args.whisper_cache))
    if args.record:
        from clearquery.voice.recording import record_microphone, RecordingError
        try:
            with record_microphone() as recording:
                result = pipeline.run(audio=recording.path,confirmation_callback=interactive)
                metadata = recording.metadata()
            result["recording"] = metadata
            print("Temporary microphone audio deleted.")
        except RecordingError as exc:
            print(f"{exc.code}: {exc}",file=sys.stderr)
            return 2
        except OSError:
            print("RECORDING_IO_ERROR: Temporary audio could not be written or cleaned up safely.",file=sys.stderr)
            return 2
    else:
        result = pipeline.run(text_ar=text, confirmed=confirmed, audio=args.audio, confirmation_callback=interactive)
    if args.demo:
        print_stages(result)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    database = None
    if args.execute:
        database = execute_after_authorization(result)
        result["database_execution"] = database
        print("\n--- PostgreSQL execution (separate from model pipeline) ---")
        print(json.dumps(database,ensure_ascii=False,indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x",encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2))
    if database and database.get("error"):
        return 2
    return 0 if result["stop_reason"] in {"CLARIFY", "ABSTAIN", "REVIEWED_SQL_EXECUTION_DISABLED", "TRANSCRIPT_UNCONFIRMED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
