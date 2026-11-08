"""Pin and download ONLY XiYanSQL-QwenCoder-7B-2504 into the project cache.

    python scripts/download_model.py pin        # resolve current commit -> models/xiyan_7b_2504.lock.json
    python scripts/download_model.py download   # download exactly that revision, verify SHA-256
    python scripts/download_model.py verify     # re-verify local files against the lock

No other model is downloaded. Files are limited to config, tokenizer and
safetensors weights. The lock records the Hub commit SHA and the LFS SHA-256
of every weight shard; ``verify`` recomputes them locally.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ID = "XGenerationLab/XiYanSQL-QwenCoder-7B-2504"
LOCK = ROOT / "models" / "xiyan_7b_2504.lock.json"
CACHE = ROOT / ".hf-cache"
ALLOW = ["*.json", "*.safetensors", "merges.txt", "vocab.json"]


def pin() -> None:
    from huggingface_hub import HfApi
    if LOCK.exists():
        sys.exit(f"refusing: {LOCK} already exists")
    info = HfApi().model_info(REPO_ID, files_metadata=True)
    files = []
    for s in info.siblings:
        name = s.rfilename
        if name.endswith(".safetensors") or name.endswith(".json") or name in ("merges.txt", "vocab.json"):
            files.append({"path": name, "size": s.size,
                          "lfs_sha256": s.lfs.sha256 if s.lfs else None,
                          "blob_id": s.blob_id})
    lock = {"repo_id": REPO_ID, "revision": info.sha, "last_modified": str(info.last_modified),
            "license": (info.card_data or {}).get("license") if info.card_data else None,
            "files": sorted(files, key=lambda f: f["path"])}
    LOCK.parent.mkdir(exist_ok=True)
    LOCK.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    total = sum(f["size"] or 0 for f in files)
    print(f"pinned {REPO_ID}@{info.sha}: {len(files)} files, {total / 1e9:.2f} GB")


def _local_dir(lock: dict) -> Path:
    return CACHE / ("models--" + lock["repo_id"].replace("/", "--")) / "snapshots" / lock["revision"]


def download() -> None:
    from huggingface_hub import snapshot_download
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    path = snapshot_download(lock["repo_id"], revision=lock["revision"], cache_dir=str(CACHE),
                             allow_patterns=ALLOW)
    print(f"downloaded to {path}")
    verify()


def verify() -> None:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    d = _local_dir(lock)
    bad = 0
    for f in lock["files"]:
        p = d / f["path"]
        if not p.exists():
            print(f"MISSING {f['path']}"); bad += 1; continue
        if f["lfs_sha256"]:
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 24), b""):
                    h.update(chunk)
            ok = h.hexdigest() == f["lfs_sha256"]
            print(f"{'OK ' if ok else 'BAD'} sha256 {f['path']}")
            bad += not ok
        elif p.stat().st_size != f["size"]:
            print(f"BAD size {f['path']}"); bad += 1
    print("VERIFY", "PASS" if not bad else f"FAIL ({bad})")
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    cmds = {"pin": pin, "download": download, "verify": verify}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit("usage: download_model.py pin|download|verify")
    cmds[sys.argv[1]]()
