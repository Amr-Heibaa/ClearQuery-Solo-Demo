"""Private, dedicated PostgreSQL instance for the ClearQuery 10% demo.

Why a private instance: the machine's existing PostgreSQL 18 service on port
5432 is NOT touched (no connection, no roles, no databases created there).
This script runs a separate user-level PostgreSQL server (no Windows service,
no admin rights) from the installed binaries, with its data directory inside
this project and listening on 127.0.0.1:55432 only.

Commands::

    python scripts/demo_cluster.py init     # initdb + generate local secrets
    python scripts/demo_cluster.py start
    python scripts/demo_cluster.py status
    python scripts/demo_cluster.py stop

Secrets: ``init`` generates random passwords and writes them ONLY to
``.secrets/demo.env`` (git-ignored, outside source code). Code reads them from
environment variables; ``scripts/_env.py`` loads that file into the
environment for local runs.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if not os.environ.get("CLEARQUERY_PGBIN"):
    raise SystemExit("Set CLEARQUERY_PGBIN to your PostgreSQL bin directory before using this Windows-only setup script.")
PGBIN = Path(os.environ["CLEARQUERY_PGBIN"])
DATA = ROOT / ".pgdemo" / "data"
LOG = ROOT / ".pgdemo" / "server.log"
SECRETS = ROOT / ".secrets" / "demo.env"
PORT = 55432
ADMIN_USER = "clearquery_demo_admin"
DB_NAME = "clearquery_demo"
APP_USER = "clearquery_app_ro"


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([str(a) for a in args], check=check, text=True, capture_output=True)


def init() -> None:
    if DATA.exists():
        sys.exit(f"refusing: {DATA} already exists (delete it manually to re-init)")
    SECRETS.parent.mkdir(parents=True, exist_ok=True)
    admin_pw = secrets.token_urlsafe(24)
    app_pw = secrets.token_urlsafe(24)
    pwfile = SECRETS.parent / "initdb.pw"
    pwfile.write_text(admin_pw, encoding="utf-8")
    try:
        DATA.parent.mkdir(parents=True, exist_ok=True)
        r = _run(PGBIN / "initdb.exe", "-D", DATA, "-U", ADMIN_USER, "--pwfile", pwfile,
                 "--auth=scram-sha-256", "--encoding=UTF8", "--locale=C", "--no-instructions")
        print(r.stdout[-400:])
    finally:
        pwfile.unlink(missing_ok=True)
    with open(DATA / "postgresql.conf", "a", encoding="utf-8") as f:
        f.write(f"\n# ClearQuery demo\nlisten_addresses = '127.0.0.1'\nport = {PORT}\n")
    SECRETS.write_text(
        "# Local-only secrets for the ClearQuery demo instance. Never commit.\n"
        f"CLEARQUERY_DB_HOST=127.0.0.1\nCLEARQUERY_DB_PORT={PORT}\n"
        f"CLEARQUERY_DB_NAME={DB_NAME}\n"
        f"CLEARQUERY_ADMIN_USER={ADMIN_USER}\nCLEARQUERY_ADMIN_PASSWORD={admin_pw}\n"
        f"CLEARQUERY_APP_USER={APP_USER}\nCLEARQUERY_APP_PASSWORD={app_pw}\n",
        encoding="utf-8")
    print(f"initialized {DATA}; secrets written to {SECRETS}")


def start() -> None:
    # Do not capture output: the detached server would inherit the pipe and
    # keep it open, so the caller would never see EOF.
    # Own hidden console + process group so the server survives the calling
    # terminal; try to break away from the caller's job object if allowed.
    cmd = [str(PGBIN / "pg_ctl.exe"), "-D", str(DATA), "-l", str(LOG), "-w", "start"]
    base = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        r = subprocess.run(cmd, creationflags=base | subprocess.CREATE_BREAKAWAY_FROM_JOB, **kw)
    except OSError:
        r = subprocess.run(cmd, creationflags=base, **kw)
    print("started" if r.returncode == 0 else f"pg_ctl start failed ({r.returncode}); see {LOG}")
    if r.returncode:
        sys.exit(r.returncode)


def stop() -> None:
    r = _run(PGBIN / "pg_ctl.exe", "-D", DATA, "-m", "fast", "-w", "stop", check=False)
    print(r.stdout or r.stderr)


def status() -> None:
    r = _run(PGBIN / "pg_ctl.exe", "-D", DATA, "status", check=False)
    print(r.stdout or r.stderr)


if __name__ == "__main__":
    cmds = {"init": init, "start": start, "stop": stop, "status": status}
    if len(sys.argv) != 2 or sys.argv[1] not in cmds:
        sys.exit("usage: demo_cluster.py init|start|status|stop")
    cmds[sys.argv[1]]()
