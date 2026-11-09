"""Build a separate VS Code notebook; the Colab notebook is never opened for writing."""
import json
from pathlib import Path
from textwrap import dedent

ROOT=Path(__file__).resolve().parents[1]


def build():
    cells=[]
    def md(source):cells.append({'cell_type':'markdown','metadata':{},'source':dedent(source).strip().splitlines(True)})
    def code(source):cells.append({'cell_type':'code','metadata':{},'execution_count':None,'outputs':[],
        'source':dedent(source).strip().splitlines(True)})
    md('''# ClearQuery — local VS Code backend demo
    Open this notebook in the project folder and select **`.venv/Scripts/python.exe`** using **Select Kernel → Python Environments**. Run cells in order. No Colab imports, uploads, Linux setup, or new model downloads.

    Uses the existing Whisper → Arabic confirmation → BIR/policy → Qwen → Translation Gate → XiYan V4 → SQL Guard backend. Displays exact SQL and real synthetic database rows as readable tables. SQL execution requires a separate **RUN APPROVED QUERY** confirmation and the existing read-only database checks.

    Choose typed Arabic or an existing recording outside the repository. **Live Windows microphone capture remains blocked**; use Windows Sound Recorder, save privately, then supply its path. This notebook does not claim to fix microphone hardware. The separate Colab notebook is preserved.
    ''')
    code('''
    import sys, json, importlib.util
    from pathlib import Path
    candidates=[Path.cwd(),*Path.cwd().parents]
    ROOT=next((p for p in candidates if (p/'src/clearquery/orchestrator/__init__.py').is_file()),None)
    assert ROOT is not None, 'Open this notebook from the ClearQuery-Solo-Demo workspace.'
    print('Selected Python:',sys.executable)
    required=['torch','transformers','bitsandbytes','whisper','psycopg','pydantic','pglast','IPython']
    missing=[name for name in required if importlib.util.find_spec(name) is None]
    assert not missing, 'Select the existing .venv kernel. Missing packages: '+', '.join(missing)
    sys.path.insert(0,str(ROOT/'src'));sys.path.insert(0,str(ROOT/'scripts'))
    import torch
    assert torch.cuda.is_available(), 'The existing pinned NF4 models require an available CUDA GPU.'
    print('CUDA GPU:',torch.cuda.get_device_name(0))
    config=json.loads((ROOT/'configs/qwen_local_nf4.json').read_text(encoding='utf-8'))
    lock=json.loads((ROOT/'models/xiyan_7b_2504.lock.json').read_text(encoding='utf-8'))
    assert config['revision']=='f12db89cd5156e090618dded9b4367f23f8f3b33'
    assert lock['revision']=='afe320fcaf18bee342174b4e35e5a481ae93719f'
    from IPython.display import display,HTML
    from colab_presentation import exact_html,result_html,database_html
    from local_notebook import LocalNotebookDemo
    def exact(title,value,rtl=False):display(HTML(exact_html(title,value,rtl)))
    demo=LocalNotebookDemo(ROOT)
    print('Existing backend ready. Models load only when needed; no inference has run yet.')
    ''')
    md('''## 2 — Choose text or recorded audio
    `text`: enter your Arabic question. `audio`: supply an existing audio path. Original recordings are only read; they are never copied, renamed or deleted. No translation or SQL generation runs in this cell. Re-running it invalidates previous input/approval.
    ''')
    code('''
    demo.reset();RAW_ARABIC=None;USE_AUDIO=False;LAST_RESULT=None;LAST_DATABASE_RESULT=None
    mode=input('Input mode: text or audio [text]: ').strip().lower() or 'text'
    if mode=='audio':
        audio_path=input('Full path to your private audio file: ').strip().strip('"')
        asr=demo.transcribe(audio_path)
        RAW_ARABIC=asr.transcript_ar_raw;USE_AUDIO=True
        exact('Raw Whisper Arabic — unchanged',RAW_ARABIC,True)
    elif mode=='text':
        RAW_ARABIC=input('Arabic question: ')
        exact('Entered Arabic',RAW_ARABIC,True)
    else:
        raise ValueError('Choose text or audio. No inference was authorized.')
    ''')
    md('''## 3 — Confirm Arabic → run real models → show SQL
    Keep the original Arabic or edit it, then type **CONFIRM**. The raw ASR remains unchanged. CLARIFY/ABSTAIN stop early; unsafe/unverifiable translation or rejected SQL also stop safely. Re-running with the same confirmed input redisplays the existing result without inference.

    ANSWER example: **كام عدد الطلبات اللي تسجلت في كل فرع خلال شهر أكتوبر 2026؟**

    CLARIFY example: **مين أحسن فرع في أكتوبر 2026؟**
    ''')
    code('''
    assert RAW_ARABIC is not None, 'Run the input cell first.'
    exact('Original Arabic',RAW_ARABIC,True)
    edit=input('Final Arabic edit (Enter keeps original exactly): ')
    question=RAW_ARABIC if edit=='' else edit
    exact('Final Arabic to process',question,True)
    if input('Type CONFIRM to process this exact Arabic: ')=='CONFIRM':
        LAST_RESULT=demo.process(question,confirmed=True,use_audio=USE_AUDIO)
        display(HTML(result_html(LAST_RESULT)))
    else:
        LAST_RESULT=None;demo.reset()
        print('Not confirmed. Translation and SQL generation were not started. Start again from section 2.')
    ''')
    md('''## 4 — Optional actual read-only database results
    Uses only the already configured local synthetic PostgreSQL instance, **127.0.0.1:55432 / clearquery_demo**, with its existing restricted role. Does not create, start, modify or expose a database. If it is unavailable or its security preflight fails, execution stops and no results are invented.

    Review the exact SQL below, then type **RUN APPROVED QUERY**. Approval expires after five minutes and executes at most once. CLARIFY, ABSTAIN, failed translation or Guard rejection cannot execute.
    ''')
    code('''
    if not LAST_RESULT or LAST_RESULT.get('stop_reason')!='REVIEWED_SQL_EXECUTION_DISABLED':
        print('No approved SQL. Resolve the displayed clarification or safety stop first.')
    else:
        try:
            sql=demo.prepare_execution()
            exact('Approved SQL — not executed yet',sql)
            authorization=input('Type RUN APPROVED QUERY to execute once (Enter cancels): ')
            LAST_DATABASE_RESULT=demo.execute(authorization)
            display(HTML(database_html(LAST_DATABASE_RESULT)))
        except RuntimeError as error:
            print(str(error))
    ''')
    md('''## 5 — New question / reset
    Re-run section 2 to ask another question. Optional cell below clears the in-memory input, model results and execution approvals. It never deletes your recordings or changes database data. Raw outputs are held privately in this kernel; clear notebook outputs before sharing the notebook.
    ''')
    code('''
    # Run only when you want to clear the current question/results.
    demo.reset();RAW_ARABIC=None;LAST_RESULT=None;LAST_DATABASE_RESULT=None
    print('Reset. Start a new question from section 2.')
    ''')
    for i,cell in enumerate(cells):cell['id']=f'clearquery-vscode-{i:02d}'
    value={'nbformat':4,'nbformat_minor':5,'cells':cells,'metadata':{
        'kernelspec':{'name':'python3','display_name':'Python (.venv)','language':'python'},
        'language_info':{'name':'python'},'clearquery':{'target':'local_vscode','mock_outputs':False,
            'live_microphone_supported':False,'execution':'explicit_existing_ControlledExecutor'}}}
    path=ROOT/'ClearQuery_Solo_Demo_VSCode.ipynb'
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return path


if __name__=='__main__':print(build())
