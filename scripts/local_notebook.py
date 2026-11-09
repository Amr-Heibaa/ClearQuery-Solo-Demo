"""Local notebook interactions around the existing backend; no model logic."""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from clearquery.contracts import AsrResult
from clearquery.orchestrator import Pipeline
from clearquery.translation import QwenConfig, QwenTranslation
from clearquery.text_to_sql import XiYanG0
from clearquery.voice import WhisperASR
from clearquery.executor.safe_demo import ControlledExecutor
from colab_database import approved_inputs

ROOT = Path(__file__).resolve().parents[1]


class LocalNotebookDemo:
    def __init__(self, root=ROOT, *, pipeline=None, asr=None):
        self.root = Path(root).resolve()
        self.asr = asr or WhisperASR('small', 'cpu', str(self.root/'.models/whisper'))
        if pipeline is None:
            config = QwenConfig(**json.loads((self.root/'configs/qwen_local_nf4.json').read_text(encoding='utf-8')))
            if config.cache_dir and not Path(config.cache_dir).is_absolute():
                config = replace(config, cache_dir=str(self.root/config.cache_dir))
            cache = self.root/'.hf-cache'
            if not (cache/'models--XGenerationLab--XiYanSQL-QwenCoder-7B-2504').is_dir():
                raise RuntimeError('Pinned XiYan cache is unavailable. Download the locked model into .hf-cache first.')
            pipeline = Pipeline(QwenTranslation(config), XiYanG0(cache_dir=cache))
        self.pipeline = pipeline
        self.reset()

    def reset(self):
        """Invalidate old input and approvals; never delete a user's audio file."""
        self._capture = self._asr_metadata = self._audio_hash = None
        self._result = self._key = self._approval = self._database = None
        self._attempted = False
        self._executor = ControlledExecutor()

    @property
    def result(self):
        return deepcopy(self._result)

    def transcribe(self, audio_path):
        self.reset()
        path = Path(audio_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError('Audio file not found. Supply the path to an existing recording.')
        with path.open('rb') as stream:
            before = hashlib.file_digest(stream, 'sha256').hexdigest()
        try:
            captured = self.asr.transcribe(path, str(uuid4()))
        finally:
            self.asr.unload()
        captured = AsrResult.model_validate(captured.model_dump())
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != before:
                raise RuntimeError('Audio changed during ASR. Fresh transcription/confirmation required.')
        if captured.error:
            raise RuntimeError('ASR_FAILED: '+captured.error.message)
        self._capture = captured.model_copy(deep=True)
        self._asr_metadata = self.asr.metadata()
        self._audio_hash = before
        return captured.model_copy(deep=True)

    def process(self, question_ar, *, confirmed=False, use_audio=False):
        if confirmed is not True or not isinstance(question_ar, str) or not question_ar.strip():
            raise ValueError('Explicit confirmation of nonempty final Arabic is required.')
        if use_audio and self._capture is None:
            raise ValueError('Run real ASR successfully before confirming audio.')
        key = (question_ar, self._capture.trace_id if use_audio else 'text')
        if self._result is not None and self._key == key:
            return self.result  # Redisplay, never repeat inference implicitly.
        self._result = self._key = self._approval = self._database = None
        self._attempted = False
        self._executor = ControlledExecutor()
        if use_audio:
            result = self.pipeline.run(asr_result=self._capture.model_copy(deep=True),
                confirmation_callback=lambda raw: (True, question_ar))
            result['stages'].insert(1, {'stage':'asr_metadata','trace_id':result['trace_id'],
                'result':deepcopy(self._asr_metadata),'error':None})
            result['audio_sha256'] = self._audio_hash
        else:
            result = self.pipeline.run(text_ar=question_ar, confirmed=True)
        self._result, self._key = deepcopy(result), key
        return self.result

    def prepare_execution(self):
        if self._result is None:
            raise RuntimeError('Process a confirmed question first.')
        candidate, bir = approved_inputs(self._result)
        if self._attempted:
            raise RuntimeError('Execution already attempted for this approval. No replay.')
        if self._approval is None:
            # Capability issued BEFORE the user execution prompt: original TTL applies.
            self._approval = self._executor.approve(candidate, bir)
        return candidate.candidate_sql

    def execute(self, authorization):
        if self._approval is None:
            raise RuntimeError('Review approved SQL and prepare execution first.')
        if self._attempted:
            raise RuntimeError('Execution already attempted for this approval. No replay.')
        if authorization != 'RUN APPROVED QUERY':
            return {'executed':False,'error':{'code':'NOT_AUTHORIZED','message':'Execution cancelled. No query executed.'}}
        self._attempted = True
        try:
            self._database = asdict(self._executor.execute(self._approval))
        except Exception:
            # Never display credentials or raw connection exceptions in notebook output.
            self._database = {'executed':False,'error':{'code':'EXECUTION_REJECTED',
                'message':'Approval expired or was rejected; database execution stopped.'}}
        return deepcopy(self._database)
