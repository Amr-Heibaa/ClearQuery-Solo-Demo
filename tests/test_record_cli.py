"""Microphone CLI wiring tests; TEST_STUB adapters/audio, not real capture claims."""
from contextlib import contextmanager
import hashlib
import json
import pytest
from clearquery import cli
from clearquery.voice import recording as r
from test_backend_cli import models


@pytest.fixture
def capture(monkeypatch,tmp_path):
    path=tmp_path/'owned.wav'
    @contextmanager
    def record():
        path.write_bytes(b'TEST_STUB_PCM')
        try:yield r.Recording(path,hashlib.sha256(path.read_bytes()).hexdigest(),16000,16000,'TEST_STUB','enter')
        finally:path.unlink()
    monkeypatch.setattr(r,'record_microphone',record)
    return path


def test_record_wires_real_pipeline_contract_and_cleans_audio(models,capture,monkeypatch,tmp_path):
    answers=iter(['y','RUN APPROVED QUERY'])
    monkeypatch.setattr('builtins.input',lambda prompt:next(answers))
    output=tmp_path/'trace.json'
    assert cli.main(['--record','--demo','--execute','--output',str(output)])==0
    result=json.loads(output.read_text(encoding='utf-8'))
    assert not capture.exists() and result['recording']['temporary_audio_deleted']
    assert result['recording']['sha256']==hashlib.sha256(b'TEST_STUB_PCM').hexdigest()
    assert models.asr==models.translation==models.sql==models.db==1


def test_record_confirmed_flag_cannot_confirm_audio(models,capture,monkeypatch):
    monkeypatch.setattr('builtins.input',lambda prompt:'')
    assert cli.main(['--record','--demo','--execute','--confirmed'])==0
    assert not capture.exists() and models.asr==1 and models.translation==models.sql==models.db==0


def test_record_failure_stops_before_any_inference(models,monkeypatch,capsys):
    @contextmanager
    def failed():
        raise r.RecordingError('NO_MICROPHONE','No default microphone.')
        yield
    monkeypatch.setattr(r,'record_microphone',failed)
    assert cli.main(['--record','--demo','--execute'])==2
    assert models.asr==models.translation==models.sql==models.db==0
    assert 'NO_MICROPHONE' in capsys.readouterr().err


@pytest.mark.parametrize('other',[['--audio','original.wav'],['--text-ar','question']])
def test_record_is_exclusive_input_mode(models,other):
    with pytest.raises(SystemExit):cli.main(['--record',*other])
    assert models.asr==0


def test_record_cannot_reuse_file_approval(models):
    with pytest.raises(SystemExit):cli.main(['--record','--confirmation-file','old.json'])
    assert models.asr==0
