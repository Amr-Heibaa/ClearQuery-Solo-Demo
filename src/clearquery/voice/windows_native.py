"""Built-in Windows MediaCapture bridge; FFmpeg is used only for file conversion."""
from array import array
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
from time import monotonic
import wave


def _powershell():
    return Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'


@contextmanager
def record_native(*, wait=None):
    from .recording import Recording,RecordingError,ROOT,wait_for_stop
    helper=Path(__file__).with_name('windows_capture.ps1')
    if not _powershell().is_file() or not helper.is_file():
        raise RecordingError('NATIVE_CAPTURE_UNAVAILABLE','Built-in Windows PowerShell/MediaCapture helper is unavailable.')
    try:
        if input('Press Enter to START recording (Ctrl+C to cancel): ')!='':
            raise RecordingError('RECORDING_CANCELLED','Recording cancelled.')
    except (EOFError,KeyboardInterrupt):
        raise RecordingError('RECORDING_CANCELLED','Recording cancelled.') from None
    base=Path(tempfile.gettempdir()).resolve()
    if base==ROOT or ROOT in base.parents:
        raise RecordingError('TEMP_LOCATION_REJECTED','Temporary microphone audio must be outside the repo.')
    folder=Path(tempfile.mkdtemp(prefix='clearquery_native_',dir=base))
    original=folder/'native.wav';converted=folder/'capture.wav'
    process=None;reader=None;error_reader=None;guard=None
    messages=queue.Queue();finished=threading.Event();stderr=[]
    def send(value):
        if process.poll() is None:
            process.stdin.write(value+'\n');process.stdin.flush()
    def next_event(expected,seconds=25):
        deadline=monotonic()+seconds
        while monotonic()<deadline:
            try:event=messages.get(timeout=max(.01,deadline-monotonic()))
            except queue.Empty:break
            if event.get('event')==expected:return event
            if event.get('event')=='ERROR':
                code='MICROPHONE_ACCESS_DENIED' if '80070005' in event.get('message','') else 'AUDIO_DEVICE_ERROR'
                raise RecordingError(code,'Windows MediaCapture: '+event.get('message','Unknown error'))
            if event.get('event')=='PROCESS_EXIT':
                if guard and guard.expired.is_set():
                    raise RecordingError('NATIVE_CAPTURE_TIMEOUT','The external native capture deadline expired; its process tree was terminated.')
                raise RecordingError('AUDIO_DEVICE_ERROR','Native capture exited: '+''.join(stderr)[-1500:])
        raise RecordingError('AUDIO_DEVICE_ERROR','Windows MediaCapture did not complete '+expected+' within its timeout.')
    try:
        process=subprocess.Popen([str(_powershell()),'-NoLogo','-NoProfile','-NonInteractive','-File',str(helper),
            '-OutputWav',str(original)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        from .native_process import NativeProcessGuard
        try:guard=NativeProcessGuard(process,90)
        except OSError:
            raise RecordingError('NATIVE_CAPTURE_UNAVAILABLE','Cannot enforce the native process-tree timeout; capture was stopped.') from None
        def consume():
            for line in process.stdout:
                try:event=json.loads(line)
                except (ValueError,TypeError):continue
                messages.put(event)
                if event.get('event') in {'DONE','ERROR'}:finished.set()
            finished.set();messages.put({'event':'PROCESS_EXIT'})
        def consume_errors():
            for line in process.stderr:
                if sum(map(len,stderr))<8000:stderr.append(line)
        reader=threading.Thread(target=consume,daemon=True);reader.start()
        error_reader=threading.Thread(target=consume_errors,daemon=True);error_reader.start()
        ready=next_event('READY')
        print('Windows default microphone:',ready['device'],flush=True)
        print('Native format:',json.dumps(ready['native_format']),'| access:',ready['access'],flush=True)
        send('START');next_event('RECORDING')
        print('Recording Egyptian Arabic… Press Enter to STOP. Maximum 30 seconds.',flush=True)
        (wait or wait_for_stop)(finished,30)
        if not finished.is_set():send('STOP')
        done=next_event('DONE',15)
        process.wait(timeout=10)
        if process.returncode or done['stopped_by']=='cancelled':
            raise RecordingError('RECORDING_CANCELLED','Native recording cancelled or failed.')
        print(f"Native capture stopped: {done['stopped_by']} | {done['elapsed_seconds']:.2f}s | {original.stat().st_size} bytes",flush=True)
        if original.stat().st_size<=44:
            raise RecordingError('EMPTY_RECORDING','Windows MediaCapture initialized but returned an empty recording; no audio was sent to Whisper.')
        import imageio_ffmpeg
        conversion=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-v','error','-i',str(original),
            '-t','30','-ac','1','-ar','16000','-c:a','pcm_s16le',str(converted)],
            capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=45)
        if conversion.returncode:
            raise RecordingError('AUDIO_CONVERSION_FAILED','Captured audio conversion failed: '+conversion.stderr.strip())
        with wave.open(str(converted),'rb') as wav:
            frames=wav.getnframes()
            if (wav.getframerate(),wav.getnchannels(),wav.getsampwidth())!=(16000,1,2) or not 3200<=frames<=480000:
                raise RecordingError('EMPTY_RECORDING','Captured WAV is empty or has an invalid format.')
            samples=array('h');samples.frombytes(wav.readframes(frames))
        peak=max((abs(value) for value in samples),default=0)
        if peak<32:raise RecordingError('EMPTY_RECORDING','Native capture contains no audible signal. Check hardware mute and the Windows default input.')
        digest=hashlib.sha256(converted.read_bytes()).hexdigest()
        details={**ready['native_format'],'access':ready['access'],'signal_peak_pcm16':peak,
            'raw_sha256':hashlib.sha256(original.read_bytes()).hexdigest()}
        print(f'Recorded {frames/16000:.2f}s · SHA-256: {digest}',flush=True)
        yield Recording(converted,digest,16000,frames,ready['device'],done['stopped_by'],
            backend='windows_mediacapture',source_format=details)
    except (KeyboardInterrupt,EOFError):
        raise RecordingError('RECORDING_CANCELLED','Recording cancelled.') from None
    except subprocess.TimeoutExpired:
        raise RecordingError('AUDIO_DEVICE_ERROR','Native capture or conversion timed out.') from None
    finally:
        if process is not None:
            # Kill the owned child tree before closing pipes. A descendant holding
            # stdout must never make pipe cleanup wait indefinitely.
            if guard:guard.close()
            if process.poll() is None:
                process.kill();process.wait(timeout=5)
            if reader:reader.join(timeout=2)
            if error_reader:error_reader.join(timeout=2)
            # Never close a pipe while another thread still holds its read lock.
            streams=[process.stdin]
            if not reader or not reader.is_alive():streams.append(process.stdout)
            if not error_reader or not error_reader.is_alive():streams.append(process.stderr)
            for stream in streams:
                if stream:
                    try:stream.close()
                    except OSError:pass
        if folder.parent.resolve()!=base or not folder.name.startswith('clearquery_native_'):
            raise RecordingError('TEMP_CLEANUP_REJECTED','Native temporary cleanup target rejected.')
        original.unlink(missing_ok=True);converted.unlink(missing_ok=True);folder.rmdir()
