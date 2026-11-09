"""Colab presentation/security unit tests, not hosted Colab/model claims."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import colab_database as database
from colab_presentation import result_html,database_html,exact_html
from clearquery.contracts import SqlCandidate,TranslationResult
from clearquery.orchestrator import Pipeline
from clearquery.sql_guard import reference_sql


def unit_result(ar='عدد الطلبات لكل فرع في أكتوبر 2026؟',en='How many orders per branch in October 2026?'):
    # Explicit isolated test adapters; never presented as real inference.
    translator=SimpleNamespace(translate=lambda question,trace:TranslationResult(trace_id=trace,
        text_ar_original=question,text_en=en,engine='UNIT_TEST_STUB',error=None))
    generator=SimpleNamespace(generate=lambda english,bir:(SqlCandidate(trace_id=bir.trace_id,
        raw_model_output='UNIT_TEST_FIXTURE',candidate_sql=reference_sql(bir),model_id='UNIT_TEST_STUB',
        model_revision=None,error=None),{
        'contract_version':'1.1','trace_id':bir.trace_id,'quantization':'nf4','prompt_template_id':'UNIT_TEST',
        'schema_context_id':'UNIT_TEST','prompt_sha256':None,'generation_params':{},
        'inference_ms':0.0,'prompt_tokens':0,'output_tokens':0}))
    return Pipeline(translator,generator).run(text_ar=ar,confirmed=True)


def request(tmp_path,monkeypatch,result=None,**changes):
    monkeypatch.setattr(database,'PRIVATE',tmp_path)
    value=unit_result() if result is None else result
    result_path=tmp_path/'result.json';result_path.write_text(json.dumps(value),encoding='utf-8')
    fields={'authorization':'RUN APPROVED QUERY','issued_monotonic':100.,'trace_id':value['trace_id'],
        'result_sha256':hashlib.sha256(result_path.read_bytes()).hexdigest(),**changes}
    path=tmp_path/'test-authorization.json';path.write_text(json.dumps(fields))
    return path,result_path


def test_readable_output_has_exact_sql_bir_gate_and_no_json_dump():
    result=unit_result();database.approved_inputs(result)
    html=result_html(result)
    assert 'order_count' in html and 'SQL Guard' in html and 'APPROVED' in html
    assert 'Translation Gate' in html and 'PASS' in html
    assert 'SELECT b.branch_name' in html and '2026-10-01' in html
    assert '"contract_version"' not in html
    assert 'dir="rtl"' in html


def test_tables_escape_model_text_and_preserve_source_bytes():
    result=unit_result();before=json.dumps(result,sort_keys=True)
    result['stages'][0]['result']['question_ar']='<script>alert(1)</script>'
    modified=json.dumps(result,sort_keys=True)
    html=result_html(result)
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert json.dumps(result,sort_keys=True)==modified and modified!=before
    assert '&lt;img' in exact_html('test','<img src=x>')


def test_actual_database_rows_render_as_table_without_json():
    html=database_html({'executed':True,'columns':['branch_name','order_count'],
        'rows':[['Maadi',17]],'row_count':1,'elapsed_ms':5.2,'error':None})
    assert '<table' in html and '<td>Maadi</td>' in html and '<td>17</td>' in html
    assert '"rows"' not in html


@pytest.mark.parametrize('ar,expected',[
    ('مين أحسن فرع في أكتوبر 2026؟','CLARIFY'),('صافي الربح لكل فرع كام؟','ABSTAIN')])
def test_stop_displays_reason_and_never_accepts_execution(ar,expected):
    result=unit_result(ar)
    html=result_html(result)
    assert expected in html
    if expected=='CLARIFY':assert 'مطلوب توضيح' in html and 'هل تقصد' in html
    with pytest.raises(RuntimeError):database.approved_inputs(result)


@pytest.mark.parametrize('stage,field,value',[
    ('confirmation','user_confirmed',False),('decision','decision','CLARIFY'),
    ('semantic_gate','status','UNVERIFIABLE'),('semantic_gate','status','MISMATCH'),
    ('sql_guard','validated',False),('sql_guard','reviewed_sql','SELECT 1'),
    ('bir','trace_id','wrong-trace'),('sql_candidate','candidate_sql','DELETE FROM orders'),
])
def test_execution_inputs_reject_changed_or_failed_stages(stage,field,value):
    result=unit_result()
    next(s for s in result['stages'] if s['stage']==stage)['result'][field]=value
    with pytest.raises((RuntimeError,ValueError)):database.approved_inputs(result)


def test_authorization_is_one_shot_and_hash_bound(tmp_path,monkeypatch):
    path,result=request(tmp_path,monkeypatch)
    assert database.consume_request(path,result,now=101.)['stop_reason']=='REVIEWED_SQL_EXECUTION_DISABLED'
    with pytest.raises(FileExistsError):database.consume_request(path,result,now=102.)


@pytest.mark.parametrize('change',[
    {'authorization':'CONFIRM'}, {'authorization':''}, {'issued_monotonic':-201.},
    {'issued_monotonic':102.}, {'trace_id':'wrong-trace'}, {'result_sha256':'0'*64}])
def test_invalid_expired_or_changed_approval_is_consumed(tmp_path,monkeypatch,change):
    path,result=request(tmp_path,monkeypatch,**change)
    with pytest.raises(RuntimeError):database.consume_request(path,result,now=101.)
    with pytest.raises(FileExistsError):database.consume_request(path,result,now=101.)


def test_actual_result_file_change_invalidates_authorization(tmp_path,monkeypatch):
    path,result=request(tmp_path,monkeypatch)
    result.write_text(result.read_text()+' ')
    with pytest.raises(RuntimeError,match='changed'):database.consume_request(path,result,now=101.)


def test_database_provisioning_refuses_windows_without_touching_files(monkeypatch):
    monkeypatch.setattr(database.sys,'platform','win32')
    with pytest.raises(RuntimeError,match='hosted Colab'):database.setup()


def test_notebook_confirms_then_processes_and_execution_is_separate():
    notebook=json.loads((ROOT/'ClearQuery_Solo_Demo_Colab.ipynb').read_text(encoding='utf-8'))
    code='\n'.join(''.join(c['source']) for c in notebook['cells'] if c['cell_type']=='code')
    for cell in notebook['cells']:
        source=''.join(cell['source'])
        if cell['cell_type']=='code' and "CONFIRMED_JOB={'question_ar':question" in source:
            assert source.index("input('Type CONFIRM")<source.index('process_confirmed()')
    assert "phrase != 'RUN APPROVED QUERY'" in code
    assert 'EXECUTION_ATTEMPTS.add(trace)' in code and 'LAST_JOB_KEY == key' in code
    assert "'scripts/colab_database.py','setup'" in code


def test_worker_never_prints_raw_json(capsys,tmp_path):
    from colab_backend import write
    write(tmp_path/'private.json',{'private_text':'SENSITIVE_UNIT_TEST_SENTINEL'})
    assert 'SENSITIVE_UNIT_TEST_SENTINEL' not in capsys.readouterr().out
    assert json.loads((tmp_path/'private.json').read_text())['private_text']=='SENSITIVE_UNIT_TEST_SENTINEL'
