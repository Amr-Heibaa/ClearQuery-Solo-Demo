"""Isolated CLI tests use labelled model/DB stubs; real gates remain enabled."""
import json
from types import SimpleNamespace
import pytest
from clearquery import cli
from clearquery.contracts import AsrResult,TranslationResult,SqlCandidate,InferenceMetadata
from clearquery.sql_guard import reference_sql
from clearquery.executor.safe_demo import ControlledExecutor
from clearquery_poc.executor import SqlResult

AR='كام إجمالي المبيعات المسجلة لكل فرع في أكتوبر 2026؟'
EN='Total recorded sales per branch in October 2026?'


@pytest.fixture
def models(monkeypatch):
    calls=SimpleNamespace(asr=0,translation=0,sql=0,db=0,en=EN,unsafe=False,configs=[])
    class ASR:
        def __init__(self,*args):pass
        def transcribe(self,path,trace):
            calls.asr+=1
            return AsrResult(trace_id=trace,audio_id=str(path),transcript_ar_raw=' '+AR,
                confidence=None,engine='TEST_STUB',error=None)
        def unload(self):return {}
    class Translation:
        def __init__(self,config):calls.configs.append(config)
        def translate(self,question,trace):
            calls.translation+=1
            return TranslationResult(trace_id=trace,text_ar_original=question,text_en=calls.en,engine='TEST_STUB',error=None)
        def unload(self):return {}
    class Generator:
        def __init__(self,**kwargs):pass
        def generate(self,en,bir):
            calls.sql+=1
            sql='SELECT 1; DROP TABLE orders' if calls.unsafe else reference_sql(bir)
            return (SqlCandidate(trace_id=bir.trace_id,raw_model_output=sql,candidate_sql=sql,
                model_id='TEST_STUB',model_revision=None,error=None),InferenceMetadata(trace_id=bir.trace_id,
                quantization='none',prompt_template_id='TEST_STUB',schema_context_id='TEST_STUB',
                generation_params={},prompt_sha256=None,inference_ms=None,prompt_tokens=None,output_tokens=None))
        def unload(self):return {}
    def execute(self,approval):
        calls.db+=1
        return SqlResult(trace_id=approval.trace_id,sql='TEST_STUB',validated=True,
            columns=['TEST_ONLY'],rows=[[1]],row_count=1,executed=True)
    monkeypatch.setattr(cli,'WhisperASR',ASR)
    monkeypatch.setattr(cli,'QwenTranslation',Translation)
    monkeypatch.setattr(cli,'XiYanG0',Generator)
    monkeypatch.setattr(ControlledExecutor,'execute',execute)
    return calls


def test_demo_prints_actual_stages_and_requires_execution_phrase(models,monkeypatch,capsys,tmp_path):
    answers=iter(['y','RUN APPROVED QUERY'])
    monkeypatch.setattr('builtins.input',lambda prompt:next(answers))
    output=tmp_path/'trace.json'
    assert cli.main(['--demo','--execute','--audio','TEST_STUB.wav','--output',str(output)])==0
    printed=capsys.readouterr().out
    for label in ['Raw ASR','Confirmed Arabic','English translation','BIR','Decision policy','Translation Gate',
        'Exact generated SQL','SQL Guard','PostgreSQL execution']:
        assert label in printed
    result=json.loads(output.read_text(encoding='utf-8'))
    assert (models.asr,models.translation,models.sql,models.db)==(1,1,1,1)
    assert result['database_execution']['executed'] and result['executed'] is False
    assert next(s for s in result['stages'] if s['stage']=='asr')['result']['transcript_ar_raw']==' '+AR
    assert models.configs[0].quantization=='checkpoint_nf4'
    assert models.configs[0].revision=='f12db89cd5156e090618dded9b4367f23f8f3b33'


@pytest.mark.parametrize('answer',['','yes','run approved query'])
def test_execution_is_never_implicit(models,monkeypatch,answer):
    monkeypatch.setattr('builtins.input',lambda prompt:answer)
    assert cli.main(['--demo','--execute','--text-ar',AR,'--confirmed'])==0
    assert models.sql==1 and not models.db


def test_without_execute_flag_never_offers_db(models,monkeypatch):
    monkeypatch.setattr('builtins.input',lambda prompt:pytest.fail('No execution prompt expected'))
    cli.main(['--demo','--text-ar',AR,'--confirmed'])
    assert models.sql==1 and not models.db


def test_unconfirmed_audio_never_translates_or_executes(models,monkeypatch):
    monkeypatch.setattr('builtins.input',lambda prompt:'')
    cli.main(['--demo','--execute','--audio','TEST_STUB.wav','--confirmed'])
    assert models.asr==1 and models.translation==models.sql==models.db==0


def test_closed_stdin_fails_closed(models,monkeypatch):
    def closed(prompt):raise EOFError()
    monkeypatch.setattr('builtins.input',closed)
    cli.main(['--demo','--execute','--text-ar',AR])
    assert models.translation==models.sql==models.db==0


@pytest.mark.parametrize('ar',['مين أحسن فرع في أكتوبر 2026؟','صافي الربح لكل فرع كام؟'])
def test_policy_stops_never_offer_execution(models,monkeypatch,ar):
    monkeypatch.setattr('builtins.input',lambda prompt:pytest.fail('Unsafe execution prompt'))
    cli.main(['--demo','--execute','--text-ar',ar,'--confirmed'])
    assert models.translation==models.sql==models.db==0


@pytest.mark.parametrize('en,unsafe',[("Order count per branch in October 2026?",False),
    (EN+' excluding cancelled',False),(EN,True)])
def test_gate_or_guard_rejection_never_offers_execution(models,monkeypatch,en,unsafe):
    models.en,models.unsafe=en,unsafe
    monkeypatch.setattr('builtins.input',lambda prompt:pytest.fail('Unsafe execution prompt'))
    assert cli.main(['--demo','--execute','--text-ar',AR,'--confirmed'])==2
    assert models.db==0


def test_expiry_error_is_redacted_and_nonzero(models,monkeypatch,capsys):
    def rejected(*args):raise RuntimeError('PRIVATE_SECRET')
    monkeypatch.setattr(ControlledExecutor,'execute',rejected)
    monkeypatch.setattr('builtins.input',lambda prompt:'RUN APPROVED QUERY')
    assert cli.main(['--demo','--execute','--text-ar',AR,'--confirmed'])==2
    assert 'PRIVATE_SECRET' not in capsys.readouterr().out


def test_existing_output_evidence_is_never_overwritten(models,tmp_path):
    path=tmp_path/'existing.json';path.write_text('HISTORY')
    with pytest.raises(SystemExit):cli.main(['--demo','--text-ar',AR,'--confirmed','--output',str(path)])
    assert path.read_text()=='HISTORY' and models.translation==0


@pytest.mark.parametrize('ar,en',[
    (AR,EN),
    ('كام عدد الطلبات اللي تسجلت في كل فرع خلال شهر اكتوبر 2026','How many orders were registered in each branch during October 2026?'),
    ('إجمالي المبيعات في كل الفروع في أكتوبر 2026 كام؟','Overall total sales in October 2026?'),
    ('فرع المعادي باع بكام في أكتوبر 2026؟','How much did the Maadi branch sell in October 2026?'),
    ('أنهي فرع حقق أعلى إجمالي مبيعات مسجلة في أكتوبر 2026؟','Which branch achieved the highest total recorded sales in October 2026?'),
])
def test_all_five_cli_intents_reach_guard_with_isolated_adapters(models,monkeypatch,ar,en,tmp_path):
    models.en=en
    monkeypatch.setattr('builtins.input',lambda prompt:'')
    output=tmp_path/'case.json'
    assert cli.main(['--demo','--execute','--text-ar',ar,'--confirmed','--output',str(output)])==0
    result=json.loads(output.read_text(encoding='utf-8'))
    assert result['stop_reason']=='REVIEWED_SQL_EXECUTION_DISABLED'
    assert result['database_execution']['status']=='EXECUTION_NOT_AUTHORIZED'
    assert models.sql==1 and models.db==0
