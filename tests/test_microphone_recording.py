"""Isolated capture tests use TEST_STUB PCM, never real microphone claims."""
from array import array
from contextlib import contextmanager
import hashlib
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import wave
import pytest
from clearquery.voice import recording as r


@pytest.fixture
def device(monkeypatch):
    # These isolated tests cover the retained PortAudio implementation.
    monkeypatch.setattr(r,'record_microphone',r.record_portaudio)
    state=SimpleNamespace(samples=16000,value=100,channels=1,status=False,rate=16000,
        fail=None,paths=[],fallback=False)
    class PortAudioError(Exception):pass
    def query(**kwargs):
        if state.fail=='query':raise PortAudioError('TEST_STUB no device')
        return {'max_input_channels':state.channels,'default_samplerate':44100,'name':'TEST_STUB'}
    def check(**kwargs):
        if state.fallback and kwargs['samplerate']==16000:raise PortAudioError('TEST_STUB rate unsupported')
    class Stream:
        def __init__(self,**kwargs):self.kwargs=kwargs;state.rate=kwargs['samplerate']
        def __enter__(self):
            if state.fail=='permission':raise PermissionError()
            if state.fail=='device':raise PortAudioError('TEST_STUB stream failure')
            pcm=array('h',[state.value]*state.samples).tobytes()
            self.kwargs['callback'](pcm,state.samples,None,state.status)
            return self
        def __exit__(self,*args):pass
    monkeypatch.setitem(sys.modules,'sounddevice',SimpleNamespace(query_devices=query,
        check_input_settings=check,RawInputStream=Stream,PortAudioError=PortAudioError))
    monkeypatch.setattr('builtins.input',lambda prompt:'')
    return state


def capture(**kwargs):return r.record_microphone(wait=lambda done,seconds:'enter',**kwargs)


def test_pcm_wav_hash_format_and_owned_cleanup(device):
    with capture() as recording:
        path=recording.path
        assert r.ROOT not in path.parents
        assert hashlib.sha256(path.read_bytes()).hexdigest()==recording.sha256
        with wave.open(str(path),'rb') as audio:
            assert (audio.getframerate(),audio.getnchannels(),audio.getsampwidth())==(16000,1,2)
            assert audio.getnframes()==16000
    assert not path.exists()


def test_temporary_audio_deleted_after_downstream_failure(device):
    with pytest.raises(RuntimeError):
        with capture() as recording:
            path=recording.path
            raise RuntimeError('TEST_STUB downstream failure')
    assert not path.exists()


@pytest.mark.parametrize('samples,value',[(0,100),(100,100),(16000,0),(16000,10)])
def test_empty_short_or_inaudible_capture_stops(device,samples,value):
    device.samples,device.value=samples,value
    with pytest.raises(r.RecordingError,match='No audible'):
        with capture():pytest.fail('Must not yield an empty recording')


@pytest.mark.parametrize('failure,code',[('query','NO_MICROPHONE'),('permission','MICROPHONE_ACCESS_DENIED'),('device','AUDIO_DEVICE_ERROR')])
def test_device_failures_are_reported(device,failure,code):
    device.fail=failure
    with pytest.raises(r.RecordingError) as error:
        with capture():pytest.fail('Must not proceed')
    assert error.value.code==code


def test_missing_input_channels_stops(device):
    device.channels=0
    with pytest.raises(r.RecordingError):
        with capture():pytest.fail('Must not proceed')


def test_stream_overflow_never_sent_to_whisper(device):
    device.status=True
    with pytest.raises(r.RecordingError,match='lost or invalid'):
        with capture():pytest.fail('Must not proceed')


def test_hard_capture_limit(device):
    device.samples=16000*45
    def wait(done,seconds):
        assert seconds==30 and done.is_set()
        return 'maximum_duration'
    with r.record_microphone(wait=wait) as recording:
        assert recording.frames==16000*30


def test_native_device_rate_fallback_is_valid_wav(device):
    device.fallback=True
    with capture() as recording:
        assert recording.sample_rate==44100
        with wave.open(str(recording.path),'rb') as audio:assert audio.getframerate()==44100


@pytest.mark.parametrize('exception',[EOFError,KeyboardInterrupt])
def test_cancel_before_start(device,monkeypatch,exception):
    def cancel(prompt):raise exception()
    monkeypatch.setattr('builtins.input',cancel)
    with pytest.raises(r.RecordingError) as error:
        with capture():pytest.fail('Must not proceed')
    assert error.value.code=='RECORDING_CANCELLED'


def test_cancel_during_recording(device):
    def cancel(*args):raise KeyboardInterrupt()
    with pytest.raises(r.RecordingError) as error:
        with r.record_microphone(wait=cancel):pytest.fail('Must not proceed')
    assert error.value.code=='RECORDING_CANCELLED'


@pytest.mark.parametrize('key,expected',[('\r','enter'),('\x1b','RECORDING_CANCELLED'),('\x03','RECORDING_CANCELLED')])
def test_windows_stop_keys_without_background_input_thread(monkeypatch,key,expected):
    monkeypatch.setattr(r.sys,'platform','win32')
    monkeypatch.setattr(r.sys,'stdin',SimpleNamespace(isatty=lambda:True))
    monkeypatch.setitem(sys.modules,'msvcrt',SimpleNamespace(kbhit=lambda:True,getwch=lambda:key))
    if expected=='enter':assert r.wait_for_stop(threading.Event(),30)=='enter'
    else:
        with pytest.raises(r.RecordingError):r.wait_for_stop(threading.Event(),30)


def test_windows_deadline_without_pending_input_reader(monkeypatch):
    monkeypatch.setattr(r.sys,'platform','win32')
    monkeypatch.setattr(r.sys,'stdin',SimpleNamespace(isatty=lambda:True))
    monkeypatch.setitem(sys.modules,'msvcrt',SimpleNamespace(kbhit=lambda:False))
    times=iter([0,31]);monkeypatch.setattr(r,'monotonic',lambda:next(times))
    assert r.wait_for_stop(threading.Event(),30)=='maximum_duration'


def test_microphone_dependency_remains_lazy(monkeypatch):
    monkeypatch.setitem(sys.modules,'sounddevice',None)
    with pytest.raises(r.RecordingError) as error:
        with r.record_portaudio(wait=lambda *args:'enter'):pytest.fail('Missing dependency must stop')
    assert error.value.code=='RECORDING_DEPENDENCY_MISSING'
