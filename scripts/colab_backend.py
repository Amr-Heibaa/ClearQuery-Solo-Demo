"""Subprocess entry point for the notebook. Imports the existing backend only."""
import argparse
from dataclasses import replace
from importlib.metadata import version
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]


def write(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as output:json.dump(value,output,ensure_ascii=False,indent=2)
    print(json.dumps(value,ensure_ascii=False,indent=2),flush=True)


def runtime():
    import torch
    for line in (ROOT/'requirements-colab-t4.txt').read_text().splitlines():
        if '==' in line:
            name,pin=line.split('==');assert version(name)==pin,(name,version(name),pin)
    assert torch.cuda.is_available(),'CUDA unavailable; model inference disabled.'
    assert 'T4' in torch.cuda.get_device_name(0),'Select a Colab T4 runtime.'
    assert torch.cuda.get_device_capability(0)==(7,5),'Unexpected T4 compute capability.'
    assert torch.cuda.mem_get_info()[0]>=7*1024**3,'Insufficient free CUDA memory.'
    # Actual kernels, not just GPU enumeration.
    probe=torch.ones((64,64),device='cuda',dtype=torch.float16)
    value=probe@probe;torch.cuda.synchronize();assert value[0,0].item()==64
    del probe,value
    import bitsandbytes as bnb
    layer=bnb.nn.Linear4bit(64,64,bias=False,compute_dtype=torch.float16,
        compress_statistics=True,quant_type='nf4').to('cuda')
    with torch.inference_mode():
        sample=torch.ones((1,64),device='cuda',dtype=torch.float16)
        output=layer(sample);torch.cuda.synchronize();assert torch.isfinite(output).all().item()
    del layer,sample,output
    # The frozen published Qwen NF4 checkpoint declares BF16, unlike XiYan FP16.
    # Test the actual runtime, including software emulation if supported. Never
    # silently change its compute dtype or checkpoint configuration for a T4.
    try:
        bf=torch.ones((64,64),device='cuda',dtype=torch.bfloat16)
        bf_output=bf@bf;torch.cuda.synchronize();assert bf_output[0,0].item()==64
        del bf,bf_output
        bf_layer=bnb.nn.Linear4bit(64,64,bias=False,compute_dtype=torch.bfloat16,
            compress_statistics=True,quant_type='nf4').to('cuda')
        with torch.inference_mode():
            bf_sample=torch.ones((1,64),device='cuda',dtype=torch.bfloat16)
            bf_value=bf_layer(bf_sample);torch.cuda.synchronize();assert torch.isfinite(bf_value).all().item()
        del bf_layer,bf_sample,bf_value
    except Exception as exc:
        raise RuntimeError('Pinned Qwen checkpoint BF16/NF4 compute failed on this T4. Model configuration was not changed. '+str(exc)) from exc
    from clearquery.model_runtime import collect,environment
    return {'kind':'ACTUAL_T4_CUDA_AND_NF4_PROBE','environment':environment(),
        'gpu':torch.cuda.get_device_name(0),'cuda_build':torch.version.cuda,'memory_after':collect(),
        'sql_execution_enabled':False}


def main():
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['runtime','asr','pipeline'])
    parser.add_argument('--audio',type=Path);parser.add_argument('--job',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if args.output.exists():parser.error('Refusing to overwrite an earlier result.')
    if args.action=='runtime':write(args.output,runtime());return 0
    if args.action=='asr':
        if args.audio is None:parser.error('--audio is required for ASR')
        from clearquery.voice import WhisperASR
        adapter=WhisperASR('small','cpu',str(ROOT/'.models/whisper'))
        try:asr=adapter.transcribe(args.audio,str(uuid4()))
        finally:memory=adapter.unload()
        write(args.output,{'asr':asr.model_dump(),'metadata':adapter.metadata(),'memory':memory})
        return 2 if asr.error else 0
    if args.job is None:parser.error('--job is required for the pipeline')
    job=json.loads(args.job.read_text(encoding='utf-8'))
    if job.get('user_confirmed') is not True or not isinstance(job.get('question_ar'),str) or not job['question_ar'].strip():
        parser.error('Explicit confirmation of the final Arabic is required before inference.')
    from clearquery.contracts import AsrResult
    from clearquery.orchestrator import Pipeline
    from clearquery.translation import QwenConfig,QwenTranslation
    from clearquery.text_to_sql import XiYanG0
    config=QwenConfig(**json.loads((ROOT/'configs/qwen_local_nf4.json').read_text()))
    config=replace(config,cache_dir=str(ROOT/'.hf-cache'))
    pipeline=Pipeline(QwenTranslation(config),XiYanG0(cache_dir=ROOT/'.hf-cache'))
    if job.get('asr_file'):
        captured=json.loads(Path(job['asr_file']).read_text(encoding='utf-8'))
        asr=AsrResult(**captured['asr'])
        assert asr.error is None,'ASR failed; cannot confirm or continue.'
        result=pipeline.run(asr_result=asr,confirmation_callback=lambda raw:(True,job['question_ar']))
        result['stages'].insert(1,{'stage':'asr_metadata','trace_id':result['trace_id'],
            'result':captured['metadata'],'error':None})
    else:
        result=pipeline.run(text_ar=job['question_ar'],confirmed=True)
    # No executor import or execution path exists in this notebook runner.
    assert result['executed'] is False
    result['colab_context']={'kind':'REAL_MODEL_ATTEMPT','sql_execution_enabled':False,
        'browser_audio_asr':bool(job.get('asr_file'))}
    write(args.output,result)
    return 0 if result['stop_reason'] in {'CLARIFY','ABSTAIN','REVIEWED_SQL_EXECUTION_DISABLED',
        'TRANSLATION_SEMANTIC_UNVERIFIABLE','TRANSLATION_SEMANTIC_MISMATCH','UNSAFE_SQL'} else 2


if __name__=='__main__':raise SystemExit(main())
