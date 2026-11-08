"""Real owned-process timeout checks; these tests do not record audio."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows Job Object tests require Windows')
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from bounded_audio_probe import run_probe
from clearquery.voice.native_process import NativeProcessGuard


def test_owned_silent_native_child_is_killed_by_external_deadline(tmp_path):
    started=time.monotonic()
    result=run_probe([sys.executable,'-c','import time;time.sleep(60)'],seconds=.5,output=tmp_path/'timeout.json')
    assert result['timed_out'] and result['exit_code']==124
    assert time.monotonic()-started<5


def test_native_stage_before_failure_is_preserved(tmp_path):
    result=run_probe([sys.executable,'-u','-c',
        "import sys;print('STAGE COMPILE_END');print('STAGE START_RECORDING_BEGIN');sys.exit(9)"],
        seconds=5,output=tmp_path/'stages.json')
    assert result['exit_code']==9 and not result['timed_out']
    assert result['stages'][-1]['line']=='STAGE START_RECORDING_BEGIN'


def test_watchdog_kills_descendant_holding_an_inherited_pipe(tmp_path):
    child_pid=tmp_path/'child.pid'
    code="import subprocess,sys,time,pathlib; child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);pathlib.Path(sys.argv[1]).write_text(str(child.pid));time.sleep(60)"
    result=run_probe([sys.executable,'-c',code,str(child_pid)],seconds=1,output=tmp_path/'tree.json')
    assert result['timed_out'] and result['elapsed_seconds']<6
    pid=int(child_pid.read_text())
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_bool,ctypes.c_uint32];kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32];kernel.WaitForSingleObject.restype=ctypes.c_uint32
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.OpenProcess(0x100000,False,pid)
    if handle:
        try:assert kernel.WaitForSingleObject(handle,2000)==0
        finally:kernel.CloseHandle(handle)


def test_guard_close_stops_only_the_assigned_child():
    owned=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
    other=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
    guard=None
    try:
        guard=NativeProcessGuard(owned,5);guard.close()
        assert owned.wait(timeout=3)==1 or owned.returncode is not None
        assert other.poll() is None
    finally:
        if guard:guard.close()
        for process in (owned,other):
            if process.poll() is None:process.kill()
            process.wait(timeout=3)


def test_probe_refuses_existing_evidence_before_starting_child(tmp_path):
    result=tmp_path/'history.json';result.write_text('HISTORY')
    with pytest.raises(ValueError):run_probe(['MUST_NOT_RUN'],seconds=2,output=result)
    assert result.read_text()=='HISTORY'


def test_probe_refuses_unbounded_deadline_before_starting_child(tmp_path):
    with pytest.raises(ValueError):run_probe(['MUST_NOT_RUN'],seconds=0,output=tmp_path/'unused.json')
