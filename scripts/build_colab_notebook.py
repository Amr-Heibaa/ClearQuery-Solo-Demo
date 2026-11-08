"""Build the backend-only Colab notebook from the same verified source modules."""
import inspect
import json
from pathlib import Path
from textwrap import dedent
from colab_support import extract_source

ROOT=Path(__file__).resolve().parents[1]
cells=[]
def md(text):cells.append({'cell_type':'markdown','metadata':{},'source':dedent(text).strip().splitlines(True)})
def code(text):cells.append({'cell_type':'code','execution_count':None,'metadata':{},'outputs':[],
    'source':dedent(text).strip().splitlines(True)})

md('''# ClearQuery — Colab backend demonstration
Run numbered sections in order on a **hosted T4 GPU runtime**. This notebook imports the existing backend; it does not implement another pipeline or launch a frontend.

1. Upload the **source ZIP only** and verify hashes.
2. Build an isolated environment without `ensurepip`; install the pinned T4 dependency profile.
3. Run real CUDA, FP16 and pinned-Qwen BF16/NF4 probes.
4. Download/hash-check the unchanged model revisions and run component tests.
5. Choose confirmed text, or capture live browser microphone audio and run Whisper.
6. Explicitly confirm Arabic, then invoke the existing Pipeline and inspect every stage.
7. Export privacy-filtered evidence. SQL execution remains disabled.

No hosted T4, browser microphone or Colab inference success is claimed in this saved notebook: all code cells are unexecuted. Current local verification is provided separately. Microphone recording requires your browser permission and a Start click. Audio goes directly from the browser to your private Colab runtime; no manual audio upload is used.
''')
code('''
import base64, hashlib, io, json, os, pathlib, stat, subprocess, sys, tempfile, zipfile
from pathlib import Path, PurePosixPath
from uuid import uuid4
from google.colab import files
assert Path('/content').is_dir(), 'Use a hosted Colab runtime.'
READY=MODELS_READY=False
DB_READY=False
DB_EXECUTION_ENABLED=False
ROOT=Path('/content/ClearQuery-Solo-Demo')
'''+inspect.getsource(extract_source)+'''
uploaded=files.upload()  # SOURCE BUNDLE ONLY, never a recording.
assert len(uploaded)==1, 'Select the verified backend source ZIP.'
name=next(iter(uploaded));assert Path(name).suffix.lower()=='.zip'
print('Actual source ZIP SHA-256:',hashlib.sha256(uploaded[name]).hexdigest())
manifest=extract_source(uploaded[name],ROOT)
del uploaded
print('Verified source files:',len(manifest),'including frozen Person 1/G0 hashes.')
sys.path.insert(0,str(ROOT/'scripts'))
from colab_support import run_logged, converted_capture
PRIVATE=Path('/content/clearquery-private');PRIVATE.mkdir(mode=0o700,exist_ok=True)
os.chmod(PRIVATE,0o700)
LOGS=PRIVATE/'logs'
os.environ.update(HF_HUB_DISABLE_TELEMETRY='1',HF_HUB_DISABLE_IMPLICIT_TOKEN='1',PYTHONUTF8='1')
def command(argv, timeout=3600):
    return run_logged(argv,cwd=ROOT,log_dir=LOGS,timeout=timeout)
''')
md('''## 2 — Isolated setup, without ensurepip
The kernel stays unchanged. Standard `venv --without-pip` creates the environment; the kernel's pip uses its supported `--python` option to install into it. Actual subprocess output, exit codes and private logs are shown, including failures. Rerun this cell after a failed installation to retry the same pins.

The Colab-only profile uses **Torch 2.14.1+cu126**, the same release as local Torch with an official CUDA 12.6 build. Local CUDA 13 requirements, all model revisions, generation settings, Qwen configuration and XiYan V4 remain unchanged. No version fallback is performed.
''')
code('''
READY=MODELS_READY=False
ENV=Path('/content/clearquery-env');PY=str(ENV/'bin/python')
if not ENV.exists():command([sys.executable,'-m','venv','--without-pip',str(ENV)])
assert Path(PY).is_file(), 'Incomplete environment; select a fresh Colab runtime.'
# Kernel pip >=22.3 can manage a pip-less virtualenv. Do not upgrade kernel packages.
command([sys.executable,'-m','pip','--version'])
command([sys.executable,'-m','pip','--python',PY,'install','pip==26.3'])
command([PY,'-m','pip','install','-r','requirements-colab-t4.txt'])
command([PY,'-m','pip','install','--no-deps','--no-build-isolation','-e','.'])
command([PY,'-m','pip','check'])
command([PY,'-m','pip','freeze'])
''')
md('''## 3 — Actual T4/CUDA compatibility test
This runs real FP16 CUDA matrix multiplication and bitsandbytes NF4 operations. It also tests BF16/NF4 because the pinned published Qwen checkpoint requests BF16. If that combination is unsupported by your T4/software stack, this cell stops with its actual error. Model settings are never silently changed to make the probe pass.
''')
code('''
READY=MODELS_READY=False
command(['nvidia-smi'])
RUNTIME_FILE=PRIVATE/(uuid4().hex+'-runtime.json')
command([PY,'scripts/colab_backend.py','runtime','--output',str(RUNTIME_FILE)])
RUNTIME=json.loads(RUNTIME_FILE.read_text());print(json.dumps(RUNTIME,indent=2))
READY=True
''')
md('''## 4 — Pinned models and existing component tests
Downloads can be large. Failures show actual subprocess errors/logs. Check disk space and retry this cell; existing snapshot downloads resume. Never run a model `pin` command or optimize a prompt. The Python tests use labelled isolated stubs; they do not count as real inference.
''')
code('''
assert READY, 'CUDA/T4 compatibility must pass first.'
MODELS_READY=False
import shutil
assert shutil.disk_usage('/content').free>=25*1024**3, 'Need at least 25 GiB free before model downloads.'
q=json.loads((ROOT/'models/qwen3_4b_instruct_2507_nf4.lock.json').read_text())
x=json.loads((ROOT/'models/xiyan_7b_2504.lock.json').read_text())
c=json.loads((ROOT/'configs/qwen_local_nf4.json').read_text())
assert q['revision']==c['revision']=='f12db89cd5156e090618dded9b4367f23f8f3b33'
assert x['revision']=='afe320fcaf18bee342174b4e35e5a481ae93719f'
assert c['quantization']=='checkpoint_nf4' and c['enable_thinking'] is False and c['max_new_tokens']==256
command([PY,'scripts/prepare_qwen.py','--quantized','--download'])
command([PY,'scripts/download_model.py','download'])
command([PY,'-c',"""
import hashlib,json
from pathlib import Path
q=json.loads(Path('models/qwen3_4b_instruct_2507_nf4.lock.json').read_text())
snapshot=Path('.hf-cache')/('models--'+q['repo_id'].replace('/','--'))/'snapshots'/q['revision']
for item in q['files']:
    if item.get('lfs'):
        with (snapshot/item['path']).open('rb') as stream:
            assert hashlib.file_digest(stream,'sha256').hexdigest()==item['lfs']['sha256'],item['path']
print('Qwen pinned weight hashes verified.')
# This is actual CPU Whisper model loading, not ASR or a fake audio inference.
import whisper
from clearquery.voice import WhisperASR
adapter=WhisperASR('small','cpu','.models/whisper')
adapter._model=whisper.load_model('small',device='cpu',download_root='.models/whisper')
print('Whisper small loaded from its checksum-verified pretrained checkpoint.')
print(adapter.unload())
"""])
command([PY,'-m','pytest','-q','-rs','-p','no:cacheprovider','tests'])
MODELS_READY=True
CONFIRMED_JOB=None
LAST_RESULT=None
''')
md('''## 5 — Readable exact stage output
Arabic is rendered right-to-left. Text is HTML-escaped, never corrected or translated by the display helper. Private result files preserve complete raw model outputs, model revisions, timings and errors.
''')
code('''
from IPython.display import display,HTML,Markdown
import html
def exact(title,value,rtl=False):
    display(Markdown('**'+title+'**'))
    text=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2)
    display(HTML('<pre dir="'+('rtl' if rtl else 'ltr')+'" style="white-space:pre-wrap;text-align:'+('right' if rtl else 'left')+'">'+html.escape(text)+'</pre>'))
def show_result(result):
    exact('Trace / stop / processing time', {k:result.get(k) for k in ('trace_id','stop_reason','runtime_ms','executed')})
    keys={'asr':'transcript_ar_raw','confirmation':'question_ar','translation':'text_en','sql_candidate':'candidate_sql'}
    for stage in result.get('stages',[]):
        value=stage.get('result')
        if value is not None:
            exact(stage['stage']+' · '+stage['trace_id'],value.get(keys[stage['stage']]) if stage['stage'] in keys else value,
                rtl=stage['stage'] in {'asr','confirmation'})
            if stage['stage']=='sql_candidate':exact('Exact raw XiYan model output',value.get('raw_model_output'))
        if stage.get('error'):exact('Actual stage error',stage['error'])
''')
md('''## 6A — Optional real typed question
Choose a sample or type Arabic. Explicit confirmation is required before any Qwen/SQL generation. For microphone input use **6B and 6C instead**. Both paths then use section 7.
''')
code('''
assert MODELS_READY
CONFIRMED_JOB=None;LAST_RESULT=None
questions={
 'Q1':'كام إجمالي المبيعات المسجلة لكل فرع في أكتوبر 2026؟',
 'Q2':'كام عدد الطلبات اللي تسجلت في كل فرع خلال شهر اكتوبر 2026',
 'Q3':'إجمالي المبيعات في كل الفروع في أكتوبر 2026 كام؟',
 'Q4':'فرع المعادي باع بكام في أكتوبر 2026؟',
 'Q5':'أنهي فرع حقق أعلى إجمالي مبيعات مسجلة في أكتوبر 2026؟',
 'CLARIFY':'مين أحسن فرع في أكتوبر 2026؟','ABSTAIN':'صافي الربح لكل فرع كام؟'}
exact('Supported examples',questions,rtl=True)
choice=input('Case key or your Arabic question: ')
question=questions.get(choice,choice);exact('Final Arabic',question,rtl=True)
assert question.strip() and input('Type CONFIRM to process this exact Arabic: ')=='CONFIRM'
CONFIRMED_JOB={'question_ar':question,'user_confirmed':True}
''')
md('''## 6B — Direct laptop microphone → Colab → real Whisper
Run this cell, click **Start microphone**, grant the browser microphone permission, speak Egyptian Arabic, then click **Stop**. Recording stops automatically at 30 seconds. Cancel/denial/no device/empty recording fail closed. This is the Colab `eval_js` browser-to-Python bridge, not sounddevice or a remote-machine microphone.

Captured bytes are private, converted by FFmpeg to 16 kHz mono PCM, SHA-256 hashed, and passed to the existing Whisper adapter. The two temporary audio files are deleted even if ASR fails. Audio/base64 is never printed or exported. No manual audio file upload is used.
''')
code('''
assert MODELS_READY
CONFIRMED_JOB=None;LAST_RESULT=None;ASR_PATH=None
from google.colab import output as colab_output
payload=None
try:
    payload=colab_output.eval_js((ROOT/'scripts/colab_microphone.js').read_text(),timeout_sec=180)
    if not isinstance(payload,dict) or payload.get('ok') is not True:
        raise RuntimeError('Browser microphone: '+str(payload.get('code') if isinstance(payload,dict) else 'NO_RESPONSE')+
            ' '+str(payload.get('message','') if isinstance(payload,dict) else ''))
    ffmpeg=command([PY,'-c','import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())']).strip()
    ASR_PATH=PRIVATE/(uuid4().hex+'-asr.json')
    with converted_capture(payload,source_root=ROOT,ffmpeg=ffmpeg) as (wav,capture_metadata):
        command([PY,'scripts/colab_backend.py','asr','--audio',str(wav),'--output',str(ASR_PATH)],timeout=300)
    print('Owned temporary audio deleted. Actual capture metadata:',capture_metadata)
    ASR=json.loads(ASR_PATH.read_text())
    assert ASR['asr']['error'] is None,ASR['asr']['error']
    exact('Raw Whisper Arabic — unchanged',ASR['asr']['transcript_ar_raw'],rtl=True)
finally:
    payload=None
''')
md('''## 6C — Mandatory Arabic confirmation for microphone input
Run only after successful 6B. Keep the exact raw transcript or edit it. Neither translation nor SQL generation runs without the explicit CONFIRM response.
''')
code('''
CONFIRMED_JOB=None
assert ASR_PATH is not None and ASR_PATH.is_file(), 'Capture and ASR must succeed first.'
ASR=json.loads(ASR_PATH.read_text());assert ASR['asr']['error'] is None
raw=ASR['asr']['transcript_ar_raw'];exact('Unmodified raw Arabic',raw,rtl=True)
edit=input('Final Arabic edit (Enter keeps raw Arabic exactly): ')
question=raw if edit=='' else edit;exact('Final Arabic',question,rtl=True)
assert question.strip() and input('Type CONFIRM to process this exact Arabic: ')=='CONFIRM'
CONFIRMED_JOB={'question_ar':question,'user_confirmed':True,'asr_file':str(ASR_PATH)}
''')
md('''## 7 — Existing backend Pipeline: actual Qwen → BIR/policy → Gate → XiYan V4 → Guard
The actual frozen flow resolves BIR/policy before translating; CLARIFY and ABSTAIN stop early. ANSWER proceeds through the real adapters and gates. Models unload sequentially. A mismatch, unverifiable translation, model error or Guard rejection is a valid visible safety outcome. No result or SQL is substituted.
''')
code('''
assert MODELS_READY and CONFIRMED_JOB and CONFIRMED_JOB['user_confirmed'] is True
LAST_RESULT=None
job_path=PRIVATE/(uuid4().hex+'-job.json');result_path=PRIVATE/(uuid4().hex+'-result.json')
with job_path.open('x',encoding='utf-8') as stream:json.dump(CONFIRMED_JOB,stream,ensure_ascii=False)
try:
    command([PY,'scripts/colab_backend.py','pipeline','--job',str(job_path),'--output',str(result_path)])
finally:
    if result_path.exists():
        LAST_RESULT=json.loads(result_path.read_text());show_result(LAST_RESULT)
if LAST_RESULT:assert LAST_RESULT['executed'] is False
''')
md('''## 8 — Database execution DISABLED
No fresh synthetic PostgreSQL instance is provisioned by this notebook, so there is no executor cell or database connection. It never connects to your laptop database. Enabling execution would require separate safe provisioning and the same existing `ControlledExecutor` preflight, role/fixture checks and explicit authorization. Do not expose a database or create a tunnel.
''')
code('''
assert DB_READY is False and DB_EXECUTION_ENABLED is False
print('SQL execution DISABLED: no independently provisioned and verified synthetic Colab database.')
''')
md('''## 9 — Export safe evidence only
The existing evidence exporter omits audio, paths, hashes, transcripts, translations, raw SQL/model text, database rows and free-form errors. Actual raw outputs remain privately in this runtime. Do not share a notebook saved with private output cells. Nothing automatically downloads logs or archives the runtime.
''')
code('''
assert LAST_RESULT is not None, 'Run a real backend attempt first.'
safe_path=PRIVATE/(uuid4().hex+'-safe-evidence.json')
command([PY,'-c',"""
import json,sys
from pathlib import Path
from clearquery.evidence import public_evidence
result=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
with Path(sys.argv[2]).open('x',encoding='utf-8') as output:
    json.dump(public_evidence(result),output,ensure_ascii=False,indent=2)
print('Safe evidence written; raw audio, transcripts and model text omitted.')
""",str(result_path),str(safe_path)])
files.download(str(safe_path))
''')

for index,cell in enumerate(cells):cell['id']=f'clearquery-backend-{index:02d}'
notebook={'cells':cells,'metadata':{'kernelspec':{'display_name':'Python 3','language':'python','name':'python3'},
    'language_info':{'name':'python'},'colab':{'name':'ClearQuery_Solo_Demo_Colab.ipynb','provenance':[]},
    'clearquery':{'hosted_colab_verified':False,'database_provisioning_supported':False,'source_reuse':True,
        'local_backend_changed':False,'sql_execution_enabled':False,'microphone':'google.colab.output.eval_js',
        'torch_colab_build':'2.14.1+cu126','model_settings_changed':False}},'nbformat':4,'nbformat_minor':5}
(ROOT/'ClearQuery_Solo_Demo_Colab.ipynb').write_text(json.dumps(notebook,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('Built',len(cells),'backend-only notebook cells. Hosted execution not claimed.')
