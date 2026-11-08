# ClearQuery Solo Demo

**A backend-focused university Sales 10% proof of concept.** This repository is
an academic demonstration of a bounded Arabic voice-to-SQL pipeline. It is not
the complete ClearQuery V3.1 graduation project, a production service or a claim
of general text-to-SQL accuracy. The demonstration uses synthetic data only.

## Implemented capabilities

| Intent | Supported behavior |
|---|---|
| Q1 | Recorded monetary sales per branch |
| Q2 | Order count per branch, including zero-order branches |
| Q3 | Overall recorded sales |
| Q4 | Recorded sales for a specified supported branch |
| Q5 | Highest-sales branch, preserving ties |

The pipeline resolves dates and the supported branch catalog, represents meaning
as a **Business Intent Representation (BIR)**, and chooses **ANSWER**, **CLARIFY**
or **ABSTAIN**. An ambiguous “best branch” requires clarification; net profit is
unsupported. Unknown or changed translation meaning stops safely.

## Backend pipeline

```text
Audio file / optional live microphone
    -> Whisper Arabic ASR
    -> explicit Arabic confirmation or editing
    -> BIR and decision policy
    -> Qwen Arabic-to-English translation, for ANSWER only
    -> Translation Semantic Gate
    -> pinned XiYanSQL V4
    -> pglast AST SQL Guard
    -> explicit RUN APPROVED QUERY authorization
    -> verified synthetic PostgreSQL, read-only transaction
```

The frozen G0 flow intentionally resolves BIR/policy before translation, allowing
CLARIFY and ABSTAIN to stop before model loading. Typed Arabic enters the same
pipeline at confirmation. The original ASR, translation and model SQL are retained
exactly in private local results; neither gate repairs an unsafe output.

The SQL Guard accepts one approved read-only SELECT shape and compares metric,
relations, columns, branch, dates, grouping, projections and ranking against BIR.
Execution uses a server-issued, single-use approval, a five-minute expiry, another
current Guard review, and a database preflight on the execution transaction.
Preflight verifies loopback identity, effective permissions, view definitions and
every exposed row against the synthetic fixture hashes. Results are bounded;
transactions are read-only and rolled back.

## Technologies and models

Python, Pydantic, pglast, PostgreSQL/psycopg, PyTorch, Transformers and
bitsandbytes underpin the backend. Whisper `small` runs on CPU by default. The
exploratory Qwen3-4B-Instruct published NF4 checkpoint and XiYanSQL 7B run
sequentially on CUDA and unload between stages. No training or fine-tuning is
performed. Exact model revisions are in `models/*.lock.json`; prompts and model
settings are in the source/configuration files. No model weights are included.

## Repository structure

```text
src/clearquery/          CLI, models, BIR/policy, semantic/SQL gates, execution
src/clearquery_poc/      Existing Person 1 generator, Gold loader and XiYan V4
contracts/              Frozen G0 v1.1 wire contract
db/                     Synthetic schema and read-only role definitions
data/                   Synthetic fixtures, seed SQL and hashes
gold/                   Trusted Q1-Q5 SQL examples
models/                 Model identity/revision/integrity metadata only
configs/                Exploratory translation configurations
scripts/                Setup, downloads, Colab helpers and bounded diagnostics
tests/, tests_person1/  Isolated regression and opt-in synthetic DB tests
```

There is no Streamlit application in this public release. Private local evidence,
microphone recordings, diagnostic outputs and historical handoff material are
excluded. Release-only changes remove old-workspace cache/credential fallback
and machine-specific setup paths; SQL/model logic is retained. The Person 1 hash
manifest covers the reviewed public subset. XiYan V4 and the frozen G0 contract
remain byte-identical; a schema comment containing a local path was removed.

## Installation

Use Python 3.11+ and work from the clone root. The code uses repository fixtures,
so install this academic checkout in editable mode:

```bash
python -m venv .venv
# Activate .venv using your platform's standard activation command.
python -m pip install -e ".[test,database]"
python -m pytest -q
```

For real local inference, a compatible CUDA GPU, sufficient RAM/disk space and
separately downloaded weights are required:

```bash
python -m pip install -r requirements-models.txt
python -m pip install -e ".[database,microphone]"
python scripts/prepare_qwen.py --quantized --download
python scripts/download_model.py download
```

These commands retain the tested local CUDA 13 package profile. They do not
guarantee compatibility with every driver/GPU. The Colab notebook uses a separate
CUDA 12.6 profile for the same Torch release and explicitly tests compatibility.
Never run the download scripts' `pin` commands to substitute another revision.

## CLI usage

```bash
python -m clearquery --demo --audio /path/to/private/question.wav
python -m clearquery --demo --text-ar "كام عدد الطلبات اللي تسجلت في كل فرع خلال شهر اكتوبر 2026"
```

Confirm the Arabic with `y`, edit with `e` and confirm the edit, or press Enter to
stop. `--confirmed` explicitly confirms typed text only; it never confirms audio.
The terminal displays trace IDs, raw ASR, confirmed Arabic, exact translation,
BIR, decision, semantic checks, exact SQL, Guard result, timing and errors.

To offer execution after Guard approval:

```bash
python -m clearquery --demo --execute --audio /path/to/private/question.wav
```

Type **`RUN APPROVED QUERY`** at the execution prompt to request one query. The
flag alone never executes SQL. Actual rows/errors are printed after execution.
Without `--execute`, the inference pipeline stops at reviewed SQL. CLARIFY,
ABSTAIN and either gate's failures cannot execute a query.

`--output runtime/new-private-trace.json` saves exact local results and refuses to
overwrite an existing evidence file. These files may contain private transcripts;
keep them private. `--record --demo --execute` is implemented for Windows, with
Enter start/stop and a 30-second cap, but **live Windows capture is currently
blocked at native audio initialization** on the tested machine. Sound Recorder
working there does not constitute a successful CLI capture. Use the verified
audio-file path for a demonstration.

## Synthetic PostgreSQL setup

The provided cluster lifecycle script is **Windows-only** and creates a separate
user-level cluster. It must not be pointed at an existing/production cluster.
Install PostgreSQL 18 separately and make its binaries available, then use
PowerShell from the clone root:

```powershell
$env:CLEARQUERY_PGBIN = Split-Path (Get-Command pg_ctl.exe).Source
python scripts/demo_cluster.py init
python scripts/demo_cluster.py start
python scripts/build_demo_db.py
```

`init` refuses an existing data directory and generates random credentials in
ignored `.secrets/demo.env`. The builder verifies the private cluster's data
directory before provisioning `clearquery_demo`. The application connects only
to `127.0.0.1:55432` as `clearquery_app_ro`, which inherits SELECT access from
non-login `analyst_ro`. Only `analytics.branches` and `analytics.orders` are
readable. Default limits are 5 seconds and 1,000 result rows. `.env.example`
documents variable names without supplying credentials. The CLI loads only its
own project's secrets; it does not search another workspace.

Provisioning changes a fresh synthetic database; runtime query execution never
writes business data. Do not use `--rebuild` against a database you need to keep.
No Linux database provisioner is claimed. **Colab SQL execution remains disabled**:
the notebook provisions no database and never exposes or connects to a laptop
database over the internet.

## Testing and verification

```bash
python -m pytest -q
```

The default suite uses labelled stubs for isolated model/device tests and skips
database integration unless explicitly enabled. It is not real inference and is
not a general accuracy benchmark. Tests cover G0, confirmation, Q1-Q5 meaning,
unsupported/ambiguous requests, translation mismatch, unsafe ASTs, SQL/BIR
consistency, output preservation, approval provenance/expiry/replay, bounded
native process cleanup and private evidence export.

After provisioning your own verified synthetic database, opt in to its regression
suite in PowerShell:

```powershell
$env:CLEARQUERY_RUN_DB_TESTS = "1"
python scripts/test_database_regressions.py --evidence-directory runtime/db-check
```

Those tests use the local generated test credentials and compare trusted guarded
SELECT fixtures to unchanged Gold SQL, including zero-order branches, row caps,
timeouts and permission denial. They are labelled fixture tests, not model output.
Original local real-model Q1-Q5 results matched Gold; private traces are not
distributed. Fresh inference is required to reproduce model behavior in a new
environment. Release verification on Python 3.12: **370 passed, 66 skipped** with
database integration disabled; **435 passed, 1 skipped** with the verified local
synthetic database explicitly enabled. The remaining skip requires a private
historical inference trace, which is intentionally not distributed. These are
test-suite results, not model accuracy measurements. The base installation and
dependency consistency check passed in a fresh environment without model or UI
packages.

## Colab

`ClearQuery_Solo_Demo_Colab.ipynb` reuses the same backend source. Build a source-only
bundle with `python scripts/build_colab_source.py runtime/colab-source.zip`, upload
the notebook and that ZIP to your Colab session, then follow the numbered cells.
Audio capture uses the browser-to-Python bridge after permission and a Start click;
it does not use remote sounddevice capture or manual audio uploads.

Hosted T4 and browser capture remain unverified. The pinned Qwen NF4 checkpoint
requests BF16: the notebook probes actual T4 BF16/NF4 support and stops on
incompatibility rather than silently changing the checkpoint settings. Notebook
outputs are empty in this repository. Browser permission/sign-in and hosted
hardware are user actions; no public tunnel or database endpoint is created.

## Limitations and privacy

This is a narrow synthetic Sales PoC. ASR errors need human correction; Qwen
translation is exploratory. Bounded grammars and AST shapes can safely reject
otherwise equivalent unfamiliar wording or SQL. Other workspaces, real business
data, credentials, recordings, transcripts, caches and model weights are not
included. Keep runtime artifacts ignored and review notebook outputs before
sharing. The safe-evidence exporter omits audio, transcripts and raw model text.

## License and acknowledgments

Included project contributions are published under **GPL-3.0-or-later**, with
project-owner authorization. See [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for dependency, model and synthetic
data acknowledgments. Third-party licenses remain their own; pretrained model
weights and dependency binaries are obtained separately, not redistributed here.
