"""Notebook interactions only. Executed in notebook globals by its builder."""
from IPython.display import display, HTML
from time import monotonic
from colab_presentation import exact_html, result_html, database_html

LAST_JOB_KEY = None
LAST_DATABASE_RESULT = None
EXECUTION_ATTEMPTS = set()


def exact(title, value, rtl=False):
    display(HTML(exact_html(title, value, rtl)))


def show_result(result):
    display(HTML(result_html(result)))


def run_approved_query():
    global LAST_DATABASE_RESULT
    if not LAST_RESULT or LAST_RESULT.get('stop_reason') != 'REVIEWED_SQL_EXECUTION_DISABLED':
        print('No approved SQL: clarify the question or resolve the displayed safety stop first.')
        return
    if not DB_READY or not DB_EXECUTION_ENABLED:
        print('No database rows: run section 4B to provision the isolated synthetic database.')
        return
    trace = LAST_RESULT['trace_id']
    if trace in EXECUTION_ATTEMPTS:
        print('Execution already attempted for this approval; no replay.')
        if LAST_DATABASE_RESULT:
            display(HTML(database_html(LAST_DATABASE_RESULT)))
        return
    digest = hashlib.sha256(result_path.read_bytes()).hexdigest()
    issued = monotonic()
    phrase = input('Type RUN APPROVED QUERY to execute the displayed SQL on synthetic data (Enter cancels): ')
    if phrase != 'RUN APPROVED QUERY':
        print('Execution cancelled. No query executed.')
        return
    if monotonic() - issued > 300:
        print('Approval expired. No query executed.')
        return
    request = PRIVATE / (uuid4().hex + '-authorization.json')
    fd = os.open(request, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump({'authorization': phrase, 'trace_id': trace, 'issued_monotonic': issued,
            'result_sha256': digest}, stream)
    EXECUTION_ATTEMPTS.add(trace)
    db_path = PRIVATE / (uuid4().hex + '-database-result.json')
    try:
        command([PY, 'scripts/colab_database.py', 'execute', '--result', str(result_path),
            '--authorization', str(request), '--output', str(db_path)], timeout=90)
    finally:
        request.unlink(missing_ok=True)
        if db_path.exists():
            LAST_DATABASE_RESULT = json.loads(db_path.read_text())
            display(HTML(database_html(LAST_DATABASE_RESULT)))


def process_confirmed():
    global LAST_RESULT, LAST_JOB_KEY, LAST_DATABASE_RESULT, result_path
    assert MODELS_READY and CONFIRMED_JOB and CONFIRMED_JOB['user_confirmed'] is True
    key = hashlib.sha256(json.dumps(CONFIRMED_JOB, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if LAST_RESULT is not None and LAST_JOB_KEY == key:
        show_result(LAST_RESULT)
        print('Existing result shown; no repeated inference. Use section 8 if execution was skipped.')
        return
    LAST_RESULT = LAST_DATABASE_RESULT = LAST_JOB_KEY = None
    job_path = PRIVATE / (uuid4().hex + '-job.json')
    result_path = PRIVATE / (uuid4().hex + '-result.json')
    fd = os.open(job_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(CONFIRMED_JOB, stream, ensure_ascii=False)
    try:
        command([PY, 'scripts/colab_backend.py', 'pipeline', '--job', str(job_path), '--output', str(result_path)])
    finally:
        if result_path.exists():
            LAST_RESULT = json.loads(result_path.read_text())
            LAST_JOB_KEY = key
            show_result(LAST_RESULT)
    if LAST_RESULT:
        assert LAST_RESULT['executed'] is False
        if LAST_RESULT['stop_reason'] == 'REVIEWED_SQL_EXECUTION_DISABLED':
            run_approved_query()
