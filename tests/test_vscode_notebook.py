"""Local notebook boundary tests; stubs here are not real inference claims."""
from copy import deepcopy
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from local_notebook import LocalNotebookDemo
from clearquery.contracts import AsrResult,Error
from test_colab_results import unit_result


class StubPipeline:
    def __init__(self):self.calls=[]
    def run(self,**kwargs):
        self.calls.append(kwargs)
        if 'asr_result' in kwargs:
            raw=kwargs['asr_result'].transcript_ar_raw
            accepted,ar=kwargs['confirmation_callback'](raw)
            assert accepted
        else:ar=kwargs['text_ar']
        return unit_result(ar)


def controller():
    pipeline=StubPipeline()
    return LocalNotebookDemo(ROOT,pipeline=pipeline),pipeline


def test_new_notebook_compiles_without_colab_or_json_output():
    value=json.loads((ROOT/'ClearQuery_Solo_Demo_VSCode.ipynb').read_text(encoding='utf-8'))
    code='\n'.join(''.join(c['source']) for c in value['cells'] if c['cell_type']=='code')
    for i,c in enumerate(value['cells']):
        if c['cell_type']=='code':
            compile(''.join(c['source']),f'vscode-cell-{i}','exec')
            assert c['execution_count'] is None and not c['outputs']
    assert 'google.colab' not in code and '/content' not in code
    assert 'json.dumps' not in code and 'apt-get' not in code
    assert "input('Type CONFIRM" in code and "input('Type RUN APPROVED QUERY" in code
    assert 'result_html' in code and 'database_html' in code


def test_builder_does_not_modify_colab_notebook():
    from build_vscode_notebook import build
    path=ROOT/'ClearQuery_Solo_Demo_Colab.ipynb'
    before=sha256(path.read_bytes()).hexdigest()
    assert build().name=='ClearQuery_Solo_Demo_VSCode.ipynb'
    assert sha256(path.read_bytes()).hexdigest()==before


@pytest.mark.parametrize('confirmed',[False,None,1,'CONFIRM'])
def test_no_processing_without_explicit_confirmation(confirmed):
    demo,pipeline=controller()
    with pytest.raises(ValueError):demo.process('عدد الطلبات لكل فرع في أكتوبر 2026؟',confirmed=confirmed)
    assert not pipeline.calls


def test_redisplay_does_not_repeat_inference_and_result_is_defensive_copy():
    demo,pipeline=controller();ar='عدد الطلبات لكل فرع في أكتوبر 2026؟'
    first=demo.process(ar,confirmed=True);second=demo.process(ar,confirmed=True)
    assert first==second and len(pipeline.calls)==1
    second['stop_reason']='changed'
    assert demo.result['stop_reason']=='REVIEWED_SQL_EXECUTION_DISABLED'
    demo.reset()
    assert demo.result is None


def test_audio_needs_actual_asr_before_confirmation():
    demo,pipeline=controller()
    with pytest.raises(ValueError):demo.process('عدد الطلبات لكل فرع في أكتوبر 2026؟',confirmed=True,use_audio=True)
    assert not pipeline.calls


def test_asr_preserves_original_file_and_raw_text_then_waits_for_confirmation(tmp_path):
    path=tmp_path/'test-only.wav';path.write_bytes(b'LABELLED_TEST_STUB_NOT_REAL_AUDIO')
    before=path.read_bytes();raw='  عدد الطلبات لكل فرع في أكتوبر 2026؟  '
    unloaded=[]
    adapter=SimpleNamespace(transcribe=lambda p,t:AsrResult(trace_id=t,audio_id=str(p),
        transcript_ar_raw=raw,confidence=None,engine='UNIT_TEST_STUB',error=None),
        unload=lambda:unloaded.append(True),metadata=lambda:{'model':'UNIT_TEST_STUB'})
    pipeline=StubPipeline();demo=LocalNotebookDemo(ROOT,pipeline=pipeline,asr=adapter)
    result=demo.transcribe(path)
    assert result.transcript_ar_raw==raw and path.read_bytes()==before and unloaded==[True]
    assert not pipeline.calls and demo.result is None
    demo.process(raw,confirmed=True,use_audio=True)
    assert pipeline.calls[0]['asr_result'].transcript_ar_raw==raw
    assert path.read_bytes()==before


@pytest.mark.parametrize('ar',['مين أحسن فرع في أكتوبر 2026؟','صافي الربح لكل فرع كام؟'])
def test_nonanswer_cannot_prepare_execution(ar):
    demo,_=controller();demo.process(ar,confirmed=True)
    with pytest.raises(RuntimeError):demo.prepare_execution()


def test_execution_requires_exact_phrase_and_is_one_shot(monkeypatch):
    demo,_=controller();demo.process('عدد الطلبات لكل فرع في أكتوبر 2026؟',confirmed=True)
    assert 'COUNT(o.order_id)' in demo.prepare_execution()
    calls=[]
    # Isolated executor stub only, never claimed as real DB access.
    from clearquery_poc.executor import SqlResult
    def execute(approval):
        calls.append(approval)
        return SqlResult(trace_id=approval.trace_id,sql='UNIT_TEST',validated=True,
            executed=True,columns=['order_count'],rows=[[2]],row_count=1)
    monkeypatch.setattr(demo._executor,'execute',execute)
    assert demo.execute('CONFIRM')['executed'] is False and not calls
    assert demo.execute('RUN APPROVED QUERY')['rows']==[[2]] and len(calls)==1
    with pytest.raises(RuntimeError):demo.execute('RUN APPROVED QUERY')
    with pytest.raises(RuntimeError):demo.prepare_execution()


def test_expired_approval_cannot_execute_or_leak_credentials(monkeypatch):
    demo,_=controller();demo.process('عدد الطلبات لكل فرع في أكتوبر 2026؟',confirmed=True)
    demo.prepare_execution();monkeypatch.setattr(demo._executor,'TTL_SECONDS',-1)
    result=demo.execute('RUN APPROVED QUERY')
    assert not result['executed'] and result['error']['code']=='EXECUTION_REJECTED'
    with pytest.raises(RuntimeError):demo.execute('RUN APPROVED QUERY')
