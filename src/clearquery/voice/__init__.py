from pathlib import Path
from clearquery.contracts import AsrResult, ConfirmationResult, Error


def confirm(raw: str, trace_id: str, *, accepted: bool, edited: str | None = None):
    return ConfirmationResult(trace_id=trace_id, transcript_ar_raw=raw,
        question_ar=(raw if edited is None else edited) if accepted else None,
        user_confirmed=accepted, edited_flag=accepted and edited is not None and edited != raw)


class WhisperASR:
    """Lazy openai-whisper adapter. Model path/name, device and cache configurable."""
    def __init__(self, model="small", device="cpu", cache=None):
        self.model_name, self.device, self.cache = model, device, cache
        self._model = None
        self.model_sha256 = None

    def unload(self):
        from clearquery.model_runtime import collect, memory
        before = memory()
        self._model = None
        return {"component": "whisper", "before": before, "after": collect()}

    def _decode_audio(self, path):
        """Decode original external file into RAM; never create an audio copy."""
        import subprocess
        import shutil
        import numpy as np
        executable = shutil.which("ffmpeg")
        if executable is None:
            import imageio_ffmpeg
            executable = imageio_ffmpeg.get_ffmpeg_exe()
        decoded = subprocess.run([executable, "-nostdin", "-threads", "0", "-i", str(path),
            "-f", "s16le", "-ac", "1", "-acodec", "pcm_s16le", "-ar", "16000", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return np.frombuffer(decoded.stdout, np.int16).flatten().astype(np.float32) / 32768.0

    def metadata(self):
        return {"model": self.model_name, "model_sha256": self.model_sha256, "device": self.device,
                "language": "ar", "task": "transcribe", "temperature": 0}

    def transcribe(self, audio_path, trace_id):
        try:
            if not Path(audio_path).is_file():
                raise FileNotFoundError(str(audio_path))
            if self._model is None:
                import whisper
                self._model = whisper.load_model(self.model_name, device=self.device, download_root=self.cache)
                self.model_sha256 = whisper._MODELS.get(self.model_name, "").split("/")[-2] if self.model_name in whisper._MODELS else None
            output = self._model.transcribe(self._decode_audio(audio_path), language="ar", task="transcribe", temperature=0,
                                            fp16=self.device.startswith("cuda"))
            # Keep raw output verbatim; no trimming, spelling correction or normalization.
            text = output["text"]
            if not isinstance(text, str) or not text.strip():
                raise ValueError("Empty Whisper transcript")
            return AsrResult(trace_id=trace_id, audio_id=str(audio_path), transcript_ar_raw=text,
                             confidence=None, engine=f"openai-whisper:{self.model_name}", error=None)
        except Exception as exc:
            return AsrResult(trace_id=trace_id, audio_id=str(audio_path), transcript_ar_raw=None,
                confidence=None, engine=f"openai-whisper:{self.model_name}",
                error=Error(code="ASR_FAILED", message=f"{type(exc).__name__}: {exc}"))
