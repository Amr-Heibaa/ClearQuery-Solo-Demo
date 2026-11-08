"""Resumable bounded parallel ranges for slow/stalled bulk downloads.

Only official locked weights are downloaded. Every completed shard is checked
against the official SHA-256 before becoming visible to the model loader.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import hashlib
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

p = argparse.ArgumentParser()
p.add_argument("--workers", type=int, default=16)
p.add_argument("--chunk-mib", type=int, default=8)
p.add_argument("--limit-chunks", type=int)
p.add_argument("--lock", type=Path, default=Path("models/qwen3_4b_instruct_2507.lock.json"))
args = p.parse_args()
root = Path(__file__).resolve().parents[1]
lock = json.loads((root / args.lock).read_text())
snapshot = root / ".hf-cache" / ("models--" + lock["repo_id"].replace("/", "--")) / "snapshots" / lock["revision"]
chunks = root / ".models/qwen_download_parts"
chunks.mkdir(parents=True, exist_ok=True)
tasks = []
files = [f for f in lock["files"] if f["path"].endswith(".safetensors")]
chunk_size = args.chunk_mib * 1024**2
started = time.perf_counter()
for f in files:
    target = snapshot / f["path"]
    if target.exists() and target.stat().st_size == f["size"]:
        with target.open("rb") as stream: digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest == f["lfs"]["sha256"]:
            print("ALREADY_SHA256_VERIFIED", f["path"], flush=True)
            continue
    for start in range(0, f["size"], chunk_size):
        end = min(start + chunk_size, f["size"]) - 1
        part = chunks / (f["path"] + f".{start:012d}.part")
        if not part.exists() or part.stat().st_size != end-start+1:
            tasks.append((f, start, end, part))
if args.limit_chunks: tasks = tasks[:args.limit_chunks]


def fetch(task):
    f, start, end, part = task
    url = f"https://huggingface.co/{lock['repo_id']}/resolve/{lock['revision']}/{f['path']}?download=true&chunk={start}"
    for attempt in range(4):
        try:
            request = Request(url, headers={"Range": f"bytes={start}-{end}", "User-Agent":"ClearQuery-Solo-Model-Setup"})
            with urlopen(request, timeout=90) as response, part.open("wb") as output:
                if response.status != 206 or not response.headers.get("Content-Range", "").startswith(f"bytes {start}-{end}/"):
                    raise RuntimeError("Server did not honor exact requested range")
                while block := response.read(1024 * 1024): output.write(block)
            if part.stat().st_size != end-start+1: raise RuntimeError("Incomplete model range")
            return end-start+1
        except Exception:
            if attempt == 3: raise
            time.sleep(min(2**attempt, 4))


downloaded = 0
print("PENDING_RANGES", len(tasks), "WORKERS", args.workers, flush=True)
with ThreadPoolExecutor(max_workers=args.workers) as pool:
    futures = [pool.submit(fetch, task) for task in tasks]
    for i, future in enumerate(as_completed(futures), 1):
        downloaded += future.result()
        print(json.dumps({"completed_ranges":i,"total_ranges":len(tasks),"downloaded_mib":round(downloaded/1024**2,1),
            "elapsed_seconds":round(time.perf_counter()-started,1)}), flush=True)
if args.limit_chunks:
    print("RANGE_PROBE_ONLY_NOT_COMPLETE_MODEL", flush=True)
    raise SystemExit(0)
for f in files:
    target = snapshot / f["path"]
    if target.exists() and target.stat().st_size == f["size"]:
        with target.open("rb") as stream: digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest == f["lfs"]["sha256"]: continue
    temporary = chunks / (f["path"] + ".assembled")
    with temporary.open("wb") as output:
        for start in range(0, f["size"], chunk_size):
            with (chunks / (f["path"] + f".{start:012d}.part")).open("rb") as part:
                while block := part.read(1024*1024): output.write(block)
    with temporary.open("rb") as stream: digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != f["lfs"]["sha256"]: raise ValueError("Official model shard SHA-256 mismatch")
    temporary.replace(target)
    print("SHA256_VERIFIED", f["path"], digest, flush=True)
