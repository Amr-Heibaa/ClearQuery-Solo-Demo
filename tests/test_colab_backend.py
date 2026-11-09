"""Colab I/O/backend unit checks. No hosted T4 or real browser capture claims."""
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import wave
import zipfile
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from colab_support import extract_source,converted_capture,run_logged


def bundle(tmp_path):
    spec=importlib.util.spec_from_file_location('colab_bundle',ROOT/'scripts/build_colab_source.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    path=tmp_path/'source.zip';module.build(path);return path.read_bytes()


def test_backend_bundle_extracts_verified_source_without_ui(tmp_path):
    data=bundle(tmp_path);destination=tmp_path/'source'
    manifest=extract_source(data,destination)
    assert 'scripts/colab_microphone.js' in manifest
    assert 'requirements-colab-t4.txt' in manifest
    assert not any(name.startswith(('apps/','src/clearquery/live_ui/')) for name in manifest)
    assert not any(part.endswith('.egg-info') for name in manifest for part in Path(name).parts)
    for name,digest in manifest.items():assert hashlib.sha256((destination/name).read_bytes()).hexdigest()==digest
    with pytest.raises(ValueError,match='already exists'):extract_source(data,destination)


def mutate_bundle(payload,change):
    with zipfile.ZipFile(io.BytesIO(payload)) as source:
        files={name:source.read(name) for name in source.namelist()}
    change(files)
    result=io.BytesIO()
    with zipfile.ZipFile(result,'w') as output:
        for name,data in files.items():output.writestr(name,data)
    return result.getvalue()


@pytest.mark.parametrize('kind',['hash','extra','traversal','frozen'])
def test_invalid_source_bundle_never_extracts(tmp_path,kind):
    payload=bundle(tmp_path)
    def mutate(files):
        if kind=='hash':files['src/clearquery/cli.py']=b'changed'
        if kind=='extra':files['unexpected.txt']=b'extra'
        if kind=='traversal':
            files['../escape.py']=b'escape'
            manifest=json.loads(files['SOURCE_SHA256.json']);manifest['../escape.py']=hashlib.sha256(b'escape').hexdigest()
            files['SOURCE_SHA256.json']=json.dumps(manifest).encode()
        if kind=='frozen':
            files['src/clearquery_poc/executor.py']=b'changed'
            manifest=json.loads(files['SOURCE_SHA256.json']);manifest['src/clearquery_poc/executor.py']=hashlib.sha256(b'changed').hexdigest()
            files['SOURCE_SHA256.json']=json.dumps(manifest).encode()
    destination=tmp_path/'unsafe'
    with pytest.raises(ValueError):extract_source(mutate_bundle(payload,mutate),destination)
    assert not destination.exists()


def test_real_subprocess_errors_and_logs_are_visible(tmp_path,capsys):
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_logged([sys.executable,'-c',"import sys;print('ACTUAL_STDOUT');print('ACTUAL_ERROR',file=sys.stderr);sys.exit(7)"],
            cwd=tmp_path,log_dir=tmp_path/'logs')
    assert error.value.returncode==7
    assert 'ACTUAL_ERROR' in capsys.readouterr().out
    assert 'ACTUAL_STDOUT' in next((tmp_path/'logs').glob('*.log')).read_text()


def test_silent_subprocess_timeout_is_bounded(tmp_path):
    with pytest.raises(RuntimeError,match='timed out'):
        run_logged([sys.executable,'-c','import time;time.sleep(20)'],cwd=tmp_path,log_dir=tmp_path/'logs',timeout=.2)


def fake_payload():
    # Isolated synthetic PCM encoded in an Ogg-labelled transport, consumed by
    # real FFmpeg's format detection. This is not presented as browser audio.
    audio=io.BytesIO()
    with wave.open(audio,'wb') as output:
        output.setnchannels(1);output.setsampwidth(2);output.setframerate(16000)
        output.writeframes(b'\x64\x00'*16000)
    return {'ok':True,'mime':'audio/ogg;codecs=opus','duration_ms':1000,
        'data':'data:audio/ogg;base64,'+base64.b64encode(audio.getvalue()).decode()}


def test_ffmpeg_real_conversion_hash_and_cleanup(tmp_path):
    import imageio_ffmpeg
    payload=fake_payload()
    with converted_capture(payload,source_root=ROOT,ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()) as (path,metadata):
        assert path.is_file() and ROOT not in path.parents
        assert metadata['sample_rate']==16000 and metadata['channels']==1
        assert metadata['wav_sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
        folder=path.parent
    assert not folder.exists()


def test_conversion_cleanup_on_downstream_failure():
    import imageio_ffmpeg
    with pytest.raises(RuntimeError,match='TEST_ONLY'):
        with converted_capture(fake_payload(),source_root=ROOT,ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()) as (path,_):
            folder=path.parent
            raise RuntimeError('TEST_ONLY downstream error')
    assert not folder.exists()


@pytest.mark.parametrize('change',[
    {'ok':False,'code':'MICROPHONE_PERMISSION_DENIED'}, {'duration_ms':33000}, {'duration_ms':0},
    {'mime':'text/html'}, {'data':'data:audio/ogg;base64,@@'}, {'data':'data:audio/ogg;base64,'},
])
def test_invalid_or_denied_browser_transfer_never_reaches_asr(change):
    payload=fake_payload();payload.update(change)
    with pytest.raises((ValueError,RuntimeError)):
        with converted_capture(payload,source_root=ROOT,ffmpeg='NEVER_CALLED'):pytest.fail('Unsafe capture accepted')


def test_notebook_bootstrap_avoids_ensurepip_and_remote_audio_library():
    notebook=json.loads((ROOT/'ClearQuery_Solo_Demo_Colab.ipynb').read_text(encoding='utf-8'))
    code='\n'.join(''.join(c['source']) for c in notebook['cells'] if c['cell_type']=='code')
    assert "'--without-pip'" in code and "'--python',PY" in code
    assert 'eval_js' in code and 'colab_microphone.js' in code
    assert 'sounddevice' not in code and 'streamlit' not in code.lower()
    assert 'DB_EXECUTION_ENABLED=False' in code and 'execute_readonly' not in code
    assert code.count('files.upload()')==1 # Source bundle only; never audio upload.


def test_worker_refuses_unconfirmed_job_before_model_loading(tmp_path):
    job=tmp_path/'job.json';job.write_text(json.dumps({'question_ar':'TEST_ONLY','user_confirmed':False}))
    process=subprocess.run([sys.executable,str(ROOT/'scripts/colab_backend.py'),'pipeline',
        '--job',str(job),'--output',str(tmp_path/'result.json')],capture_output=True,text=True,encoding='utf-8')
    assert process.returncode==2 and 'Explicit confirmation' in process.stderr
    assert not (tmp_path/'result.json').exists()


def test_browser_script_syntax_with_real_node():
    if not shutil.which('node'):pytest.skip('Node is unavailable for JS syntax verification')
    subprocess.run(['node','--check',str(ROOT/'scripts/colab_microphone.js')],check=True)
