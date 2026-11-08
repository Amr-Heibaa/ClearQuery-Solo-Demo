"""Export privacy, source bundle and notebook static verification, not Colab inference."""
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile
from clearquery.evidence import public_evidence

ROOT=Path(__file__).resolve().parents[1]


def test_export_drops_free_form_outputs_audio_and_credentials():
    private='SECRET_PRIVATE_AUDIO_AND_CREDENTIAL_SENTINEL'
    result={'trace_id':'test','contract_version':'1.1','stop_reason':'TRANSLATION_FAILED','executed':False,
        'audio_sha256':private,'stages':[
        {'stage':'asr','result':{'audio_id':private,'transcript_ar_raw':private}},
        {'stage':'confirmation','result':{'transcript_ar_raw':private,'question_ar':private,'user_confirmed':True}},
        {'stage':'translation','result':{'text_en':private,'text_ar_original':private}},
        {'stage':'translation_metadata','result':{'cache_dir':private,'prompt':private,'revision':'pinned'}},
        {'stage':'bir','result':{'question_ar':private,'metric':'order_count'}},
        {'stage':'sql_candidate','result':{'raw_model_output':private,'candidate_sql':private}},
        {'stage':'sql_guard','result':{'reviewed_sql':private,'validated':False}},
        {'stage':'translation','error':{'code':'TRANSLATION_FAILED','message':private}},
    ]}
    db={'trace_id':'test','executed':False,'sql':private,'rows':[[private]],
        'error':{'code':'DB_ERROR','message':private}}
    evidence=public_evidence(result,db)
    assert private not in json.dumps(evidence)
    assert evidence['stages']['translation']['error_code']=='TRANSLATION_FAILED'
    assert evidence['database_execution']['error_code']=='DB_ERROR'
    assert result['stages'][0]['result']['transcript_ar_raw']==private


def test_notebook_all_code_cells_compile_and_are_unexecuted():
    notebook=json.loads((ROOT/'ClearQuery_Solo_Demo_Colab.ipynb').read_text(encoding='utf-8'))
    assert notebook['nbformat']==4
    assert notebook['metadata']['clearquery']['hosted_colab_verified'] is False
    for index,cell in enumerate(notebook['cells']):
        if cell['cell_type']=='code':
            assert cell['execution_count'] is None and not cell['outputs']
            compile(''.join(cell['source']),f'colab-cell-{index}','exec')


def test_source_bundle_is_hash_verified_and_excludes_private_files(tmp_path):
    spec=importlib.util.spec_from_file_location('bundle',ROOT/'scripts/build_colab_source.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    archive_path=tmp_path/'source.zip';manifest=module.build(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        assert set(archive.namelist())==set(manifest)|{'SOURCE_SHA256.json'}
        for name,digest in manifest.items():
            parts=Path(name).parts
            assert not any(p in {'.secrets','runtime','.hf-cache','.models','.venv-models','__pycache__'} for p in parts)
            assert Path(name).suffix.lower() not in {'.ogg','.wav','.mp3','.webm','.m4a','.flac','.opus','.safetensors','.pt'}
            assert hashlib.sha256(archive.read(name)).hexdigest()==digest
    assert 'src/clearquery/orchestrator/__init__.py' in manifest
    assert 'apps/clearquery_app.py' not in manifest
    assert 'scripts/colab_microphone.js' in manifest
    assert 'models/xiyan_7b_2504.lock.json' in manifest


def test_notebook_model_execution_uses_existing_cli_and_pipeline():
    notebook=json.loads((ROOT/'ClearQuery_Solo_Demo_Colab.ipynb').read_text(encoding='utf-8'))
    code='\n'.join(''.join(c['source']) for c in notebook['cells'] if c['cell_type']=='code')
    runner=(ROOT/'scripts/colab_backend.py').read_text(encoding='utf-8')
    assert 'from clearquery.orchestrator import Pipeline' in runner
    assert 'scripts/colab_backend.py' in code
    assert 'ControlledExecutor' not in runner
    assert 'from clearquery.evidence import public_evidence' in code
    assert 'class Pipeline' not in code and 'class SqlGuard' not in code
    assert 'DB_READY=False' in code and "input('Type CONFIRM" in code
    assert 'ngrok' not in code and 'cloudflared' not in code
