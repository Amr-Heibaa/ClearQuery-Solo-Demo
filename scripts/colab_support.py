"""Notebook I/O helpers only. The existing clearquery package owns all inference."""
import base64
from array import array
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import tempfile
import threading
from uuid import uuid4
import wave
import zipfile


def extract_source(payload, destination):
    """Verify every source entry BEFORE exclusive extraction; never overlay files."""
    destination=Path(destination)
    if destination.exists():raise ValueError('Source directory already exists; use a fresh runtime.')
    allowed={'src','scripts','tests','gold','db','data','contracts','configs','models',
        'pyproject.toml','requirements-models.txt','requirements-colab-t4.txt','PERSON1_SHA256SUMS',
        'SOURCE_SHA256.json','ClearQuery_Solo_Demo_Colab.ipynb','LICENSE','THIRD_PARTY_NOTICES.md','README.md'}
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries=archive.infolist(); names=[e.filename for e in entries]
        if len(names)!=len(set(names)) or sum(e.file_size for e in entries)>25*1024*1024:
            raise ValueError('Duplicate entries or oversized source bundle.')
        manifest=json.loads(archive.read('SOURCE_SHA256.json'))
        if set(names)!=set(manifest)|{'SOURCE_SHA256.json'}:raise ValueError('Source manifest entry mismatch.')
        for entry in entries:
            path=PurePosixPath(entry.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in entry.filename or path.parts[0] not in allowed:
                raise ValueError('Unsafe or unsupported source path.')
            if stat.S_IFMT(entry.external_attr >> 16) not in (0,stat.S_IFREG):raise ValueError('Nonregular source entry.')
            if entry.filename in manifest and hashlib.sha256(archive.read(entry)).hexdigest()!=manifest[entry.filename]:
                raise ValueError('Source SHA-256 mismatch: '+entry.filename)
        for line in archive.read('PERSON1_SHA256SUMS').decode().splitlines():
            digest,name=line.split(maxsplit=1)
            if name not in manifest or manifest[name]!=digest:raise ValueError('Frozen Person 1/G0 hash mismatch.')
        destination.mkdir(parents=True)
        for entry in entries:
            target=destination/entry.filename; target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as output:output.write(archive.read(entry))
    return manifest


def run_logged(command, *, cwd, log_dir, timeout=2400):
    """Show combined real stdout/stderr and retain a unique private log."""
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    log_dir=Path(log_dir);log_dir.mkdir(parents=True,exist_ok=True)
    path=log_dir/(uuid4().hex+'.log')
    print('Running:',subprocess.list2cmdline([str(v) for v in command]),flush=True)
    with path.open('x',encoding='utf-8') as log:
        try:os.chmod(path,0o600)
        except OSError:pass
        with subprocess.Popen([str(v) for v in command],cwd=cwd,stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace') as process:
            expired=threading.Event()
            def stop():
                expired.set();process.kill()
            timer=threading.Timer(timeout,stop);timer.daemon=True;timer.start()
            lines=[]
            try:
                for line in process.stdout:
                    lines.append(line);log.write(line);log.flush();print(line,end='',flush=True)
                process.wait()
            finally:
                timer.cancel()
                if process.poll() is None:process.kill();process.wait()
            output=''.join(lines)
            if expired.is_set():raise RuntimeError('Subprocess timed out. Actual log: '+str(path))
            print('Exit:',process.returncode,'| log:',path,flush=True)
            if process.returncode:raise subprocess.CalledProcessError(process.returncode,command,output=output)
    return output


@contextmanager
def converted_capture(payload, *, source_root, ffmpeg):
    """Validate JS transfer and convert owned media to 16kHz mono PCM WAV."""
    if not isinstance(payload,dict) or payload.get('ok') is not True:
        raise ValueError(str(payload.get('code','CAPTURE_FAILED')) if isinstance(payload,dict) else 'CAPTURE_FAILED')
    mime=payload.get('mime','').split(';')[0]
    suffix={'audio/webm':'.webm','audio/ogg':'.ogg','audio/mp4':'.mp4'}.get(mime)
    if suffix is None:raise ValueError('Unsupported browser audio type.')
    duration=payload.get('duration_ms')
    if not isinstance(duration,(int,float)) or not 200<=duration<=32000:raise ValueError('Invalid capture duration.')
    data=payload.get('data','')
    if not isinstance(data,str) or len(data)>35*1024*1024:raise ValueError('Audio transfer exceeds limit.')
    header,separator,encoded=data.partition(',')
    if not separator or not header.startswith('data:') or header[5:].split(';')[0]!=mime or not header.endswith(';base64'):
        raise ValueError('Invalid audio transfer encoding.')
    raw=base64.b64decode(encoded,validate=True)
    if not raw or len(raw)>25*1024*1024:raise ValueError('Empty or oversized audio transfer.')
    base=Path(tempfile.gettempdir()).resolve(); source_root=Path(source_root).resolve()
    if base==source_root or source_root in base.parents:raise ValueError('Private audio must stay outside source.')
    folder=Path(tempfile.mkdtemp(prefix='clearquery_colab_capture_',dir=base))
    original=folder/('capture'+suffix);converted=folder/'capture.wav'
    try:
        original.write_bytes(raw);os.chmod(original,0o600)
        proc=subprocess.run([str(ffmpeg),'-nostdin','-v','error','-i',str(original),'-t','30',
            '-ac','1','-ar','16000','-c:a','pcm_s16le','-f','wav',str(converted)],
            capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
        if proc.returncode:raise RuntimeError('FFmpeg conversion failed: '+proc.stderr.strip())
        os.chmod(converted,0o600)
        with wave.open(str(converted),'rb') as wav:
            frames=wav.getnframes()
            if (wav.getframerate(),wav.getnchannels(),wav.getsampwidth())!=(16000,1,2) or not 3200<=frames<=480000:
                raise ValueError('Invalid converted WAV format or duration.')
            samples=array('h');samples.frombytes(wav.readframes(frames))
            if not samples or max(abs(value) for value in samples)<32:raise ValueError('Empty or inaudible browser recording.')
        yield converted,{'raw_sha256':hashlib.sha256(raw).hexdigest(),
            'wav_sha256':hashlib.sha256(converted.read_bytes()).hexdigest(),
            'duration_seconds':frames/16000,'sample_rate':16000,'channels':1}
    finally:
        # Only these two owned files are removed. No user upload or source tree.
        if folder.parent.resolve()!=base or not folder.name.startswith('clearquery_colab_capture_'):
            raise RuntimeError('Temporary cleanup target rejected.')
        original.unlink(missing_ok=True);converted.unlink(missing_ok=True);folder.rmdir()
