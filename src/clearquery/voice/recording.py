"""Default microphone capture. No model logic, UI framework or background input thread."""
from array import array
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import threading
from time import monotonic, sleep
import wave

MAX_SECONDS = 30
ROOT = Path(__file__).resolve().parents[3]


class RecordingError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class Recording:
    path: Path
    sha256: str
    sample_rate: int
    frames: int
    device: str
    stopped_by: str
    backend: str = 'portaudio'
    source_format: dict | None = None

    def metadata(self):
        return {"sha256":self.sha256,"sample_rate":self.sample_rate,"channels":1,
            "sample_width_bytes":2,"duration_seconds":self.frames/self.sample_rate,
            "device":self.device,"stopped_by":self.stopped_by,"temporary_audio_deleted":True,
            "backend":self.backend,"source_format":self.source_format}


def wait_for_stop(done, seconds):
    """Poll Windows console: timeout never leaves a pending reader stealing confirmation."""
    if sys.platform != "win32" or not sys.stdin.isatty():
        raise RecordingError("RECORDING_TERMINAL_REQUIRED", "Use an interactive Windows terminal for --record.")
    import msvcrt
    deadline = monotonic()+seconds
    while not done.is_set() and monotonic()<deadline:
        if msvcrt.kbhit():
            key = msvcrt.getwch()
            if key in ("\r","\n"):
                return "enter"
            if key in ("\x03","\x1b"):
                raise RecordingError("RECORDING_CANCELLED", "Recording cancelled.")
            if key in ("\x00","\xe0"):
                msvcrt.getwch()  # Consume extended-key suffix.
        sleep(0.02)
    return "maximum_duration" if not done.is_set() else "capture_limit"


@contextmanager
def record_portaudio(*, wait=None):
    """Capture at most 30 s and yield an owned WAV outside the source tree.

    Whisper's existing FFmpeg decoder converts the mono PCM WAV to 16 kHz.
    Prefer 16 kHz capture; use the device's native rate only if necessary.
    """
    path = None
    try:
        try:
            import sounddevice as sd
        except ImportError:
            raise RecordingError("RECORDING_DEPENDENCY_MISSING", "Install the microphone extra: pip install -e .[microphone]") from None
        try:
            device = sd.query_devices(kind="input")
            if device["max_input_channels"] < 1:
                raise RecordingError("NO_MICROPHONE", "No default input microphone is available.")
            rate = 16000
            try:
                sd.check_input_settings(channels=1,dtype="int16",samplerate=rate)
            except sd.PortAudioError:
                rate = int(device["default_samplerate"])
                if not 8000 <= rate <= 192000:
                    raise RecordingError("AUDIO_DEVICE_ERROR", "Unsupported microphone sample rate.")
                sd.check_input_settings(channels=1,dtype="int16",samplerate=rate)
        except RecordingError:
            raise
        except PermissionError:
            raise RecordingError("MICROPHONE_ACCESS_DENIED", "Allow microphone access for desktop apps in Windows Settings.") from None
        except Exception:
            raise RecordingError("NO_MICROPHONE", "Default microphone unavailable. Check Windows input device and microphone permissions.") from None
        try:
            if input("Press Enter to START recording (Ctrl+C to cancel): ") != "":
                raise RecordingError("RECORDING_CANCELLED", "Recording cancelled; press Enter alone to start.")
        except (EOFError, KeyboardInterrupt):
            raise RecordingError("RECORDING_CANCELLED", "Recording cancelled.") from None
        chunks, faults = [], []
        done = threading.Event()
        frames = 0
        def callback(data, count, timing, status):
            nonlocal frames
            if status:
                faults.append(True)
                done.set()
                return
            take = min(count,rate*MAX_SECONDS-frames)
            if take>0:
                chunks.append(bytes(data)[:take*2])
                frames += take
            if frames>=rate*MAX_SECONDS:
                done.set()
        print("Recording Egyptian Arabic… Press Enter to STOP. Maximum 30 seconds.",flush=True)
        try:
            with sd.RawInputStream(samplerate=rate,channels=1,dtype="int16",callback=callback):
                reason = (wait or wait_for_stop)(done,MAX_SECONDS)
        except RecordingError:
            raise
        except (EOFError, KeyboardInterrupt):
            raise RecordingError("RECORDING_CANCELLED", "Recording cancelled.") from None
        except PermissionError:
            raise RecordingError("MICROPHONE_ACCESS_DENIED", "Allow microphone access for desktop apps in Windows Settings.") from None
        except sd.PortAudioError as exc:
            raise RecordingError("AUDIO_DEVICE_ERROR", f"Microphone stream could not open: {exc}. Check Windows default input, desktop-app microphone permission and device availability.") from None
        except Exception:
            raise RecordingError("AUDIO_DEVICE_ERROR", "Could not record. Check the default microphone, desktop-app permission, and whether another app holds the device.") from None
        if faults:
            raise RecordingError("AUDIO_DEVICE_ERROR", "Microphone stream reported lost or invalid audio; nothing was sent to Whisper.")
        pcm = b"".join(chunks)
        samples = array("h"); samples.frombytes(pcm)
        if frames<rate//5 or not samples or max(abs(s) for s in samples)<32:
            raise RecordingError("EMPTY_RECORDING", "No audible recording captured. Speak into the default microphone and try again.")
        base = Path(tempfile.gettempdir()).resolve()
        if base==ROOT or ROOT in base.parents:
            raise RecordingError("TEMP_LOCATION_REJECTED", "Temporary audio must be outside the project.")
        descriptor, name = tempfile.mkstemp(prefix="clearquery_record_",suffix=".wav",dir=base)
        path = Path(name)
        with os.fdopen(descriptor,"wb") as output:
            with wave.open(output,"wb") as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate); wav.writeframes(pcm)
        with path.open("rb") as audio:
            digest = hashlib.file_digest(audio,"sha256").hexdigest()
        print(f"Recorded {frames/rate:.2f}s · SHA-256: {digest}",flush=True)
        yield Recording(path,digest,rate,frames,str(device["name"]),reason)
    finally:
        if path is not None:
            if path.parent.resolve()!=base or not path.name.startswith("clearquery_record_"):
                raise RecordingError("TEMP_CLEANUP_REJECTED", "Refusing cleanup outside the owned temporary location.")
            path.unlink(missing_ok=True)  # Exact owned file only; never an original recording.


@contextmanager
def record_microphone(*, wait=None):
    """Prefer built-in MediaCapture on Windows; retain the existing portable path."""
    if sys.platform=='win32':
        from .windows_native import record_native
        with record_native(wait=wait) as recording:yield recording
    else:
        with record_portaudio(wait=wait) as recording:yield recording
