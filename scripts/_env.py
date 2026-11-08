"""Load ``.secrets/demo.env`` into ``os.environ`` (existing variables win).
Local convenience only; application code reads plain environment variables."""

from __future__ import annotations

import os
from pathlib import Path

SECRETS = Path(__file__).resolve().parents[1] / ".secrets" / "demo.env"


def load(path: Path = SECRETS) -> bool:
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())
    return True
