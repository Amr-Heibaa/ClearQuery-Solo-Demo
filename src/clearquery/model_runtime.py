"""Release each model before the next adapter is allowed to allocate VRAM."""
import gc
import sys
import os
from importlib.metadata import version, PackageNotFoundError


def protect_readonly_tree(directory):
    """Enforce read-only access when reusing an external model cache."""
    root = os.path.normcase(os.path.abspath(directory))
    def protected(path):
        if not isinstance(path, (str, bytes, os.PathLike)): return False
        path = os.path.normcase(os.path.abspath(os.fsdecode(path)))
        return path == root or path.startswith(root + os.sep)
    def audit(event, values):
        if event == "open":
            path, mode, flags = values
            writing = (mode and any(char in mode for char in "wax+")) or ((flags or 0) & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if writing and protected(path): raise PermissionError("External source/cache is read-only")
        elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.rename", "os.link", "os.symlink", "os.utime", "os.truncate"}:
            if any(protected(v) for v in values[:2]): raise PermissionError("External source/cache is read-only")
    sys.addaudithook(audit)


def environment():
    result = {"python": sys.version.split()[0]}
    for package in ("torch", "transformers", "accelerate", "bitsandbytes", "openai-whisper", "imageio-ffmpeg", "pglast", "pydantic"):
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = None
    return result


def memory():
    torch = sys.modules.get("torch")
    if torch is None or not hasattr(torch, "cuda") or not torch.cuda.is_available():
        return {"cuda_available": False}
    free, total = torch.cuda.mem_get_info()
    return {"cuda_available": True, "device_name": torch.cuda.get_device_name(0), "allocated_bytes": torch.cuda.memory_allocated(),
            "reserved_bytes": torch.cuda.memory_reserved(), "peak_allocated_bytes": torch.cuda.max_memory_allocated(),
            "free_bytes": free, "total_bytes": total}


def collect():
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None and hasattr(torch, "cuda") and torch.cuda.is_available():
        torch.cuda.synchronize()
        torch.cuda.empty_cache()
    return memory()
