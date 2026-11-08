"""External Windows Job Object deadline for an owned native audio child tree."""
import ctypes
from ctypes import wintypes as w
import os
import threading


class _BasicLimits(ctypes.Structure):
    _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',w.DWORD),
        ('minimum_working_set',ctypes.c_size_t),('maximum_working_set',ctypes.c_size_t),
        ('active_process_limit',w.DWORD),('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]


class _Counters(ctypes.Structure):
    _fields_=[(name,ctypes.c_uint64) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]


class _ExtendedLimits(ctypes.Structure):
    _fields_=[('basic',_BasicLimits),('io',_Counters),('process_memory',ctypes.c_size_t),
        ('job_memory',ctypes.c_size_t),('peak_process_memory',ctypes.c_size_t),('peak_job_memory',ctypes.c_size_t)]


class NativeProcessGuard:
    """Attach only our Popen child; kill its descendants on timeout OR guard close.

    Unlike communicate-after-kill, this does not wait indefinitely for compiler
    grandchildren that inherited an output pipe. No existing process is attached.
    """
    def __init__(self,process,seconds):
        if os.name!='nt':raise RuntimeError('Windows process guard requires Windows.')
        if not 0<seconds<=120:raise ValueError('Native deadline must be between 0 and 120 seconds.')
        self.expired=threading.Event();self._lock=threading.Lock();self._handle=None
        self._kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        k=self._kernel
        k.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];k.CreateJobObjectW.restype=w.HANDLE
        k.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD];k.SetInformationJobObject.restype=w.BOOL
        k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];k.AssignProcessToJobObject.restype=w.BOOL
        k.TerminateJobObject.argtypes=[w.HANDLE,w.UINT];k.TerminateJobObject.restype=w.BOOL
        k.CloseHandle.argtypes=[w.HANDLE];k.CloseHandle.restype=w.BOOL
        handle=k.CreateJobObjectW(None,None)
        if not handle:process.kill();raise ctypes.WinError(ctypes.get_last_error())
        limits=_ExtendedLimits();limits.basic.flags=0x2000 # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not k.AssignProcessToJobObject(handle,w.HANDLE(int(process._handle))):
            error=ctypes.get_last_error();k.CloseHandle(handle);process.kill()
            raise OSError(error,'Cannot enforce the native child-process deadline; capture was not allowed.')
        self._handle=handle
        def deadline():
            self.expired.set()
            self.terminate()
        self._timer=threading.Timer(seconds,deadline);self._timer.daemon=True;self._timer.start()

    def terminate(self):
        with self._lock:
            if self._handle:self._kernel.TerminateJobObject(self._handle,124)

    def close(self):
        self._timer.cancel()
        with self._lock:
            if self._handle:
                self._kernel.CloseHandle(self._handle);self._handle=None
