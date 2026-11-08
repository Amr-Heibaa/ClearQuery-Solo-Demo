"""Resolve official model revision, record lock, download exactly that revision."""
import argparse
import json
import os
from pathlib import Path
from urllib.request import urlopen

p = argparse.ArgumentParser()
p.add_argument("--download", action="store_true")
p.add_argument("--quantized", action="store_true", help="Published NF4 representation of the same Qwen3-4B-Instruct base")
args = p.parse_args()
root = Path(__file__).resolve().parents[1]
repo = "unsloth/Qwen3-4B-Instruct-2507-bnb-4bit" if args.quantized else "Qwen/Qwen3-4B-Instruct-2507"
lock_path = root / ("models/qwen3_4b_instruct_2507_nf4.lock.json" if args.quantized else "models/qwen3_4b_instruct_2507.lock.json")
if lock_path.exists():
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
else:
    with urlopen(f"https://huggingface.co/api/models/{repo}?blobs=true", timeout=60) as response:
        info = json.load(response)
    lock = {"repo_id": repo, "revision": info["sha"], "status": "EXPLORATORY",
            "base_model": info.get("cardData", {}).get("base_model"),
            "representation": "PUBLISHED_NF4_QUANTIZATION" if args.quantized else "ORIGINAL_CHECKPOINT",
            "files": [{"path": f["rfilename"], "lfs": f.get("lfs"), "size": f.get("size")} for f in info["siblings"]]}
    lock_path.write_text(json.dumps(lock, indent=2), encoding="utf-8")
print(json.dumps({"model_id":repo,"revision":lock["revision"]}), flush=True)
if args.download:
    os.environ["HF_XET_CACHE"] = str(root / ".hf-cache/xet")
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    from huggingface_hub import snapshot_download
    result = snapshot_download(repo, revision=lock["revision"], cache_dir=str(root / ".hf-cache"),
        allow_patterns=["*.json", "*.safetensors", "*.jinja", "vocab.*", "merges.txt", "README.md"], max_workers=3, token=False)
    print(result, flush=True)
