"""Build the dedicated ClearQuery demo database (schema, roles, seed data).

Safety rails:
* Target database name must be exactly ``clearquery_demo``.
* The server must be the private demo instance of this project (its
  ``data_directory`` must be ``<project>/.pgdemo/data``); otherwise the script
  refuses, so it can never create roles or databases on another server.
* ``--rebuild`` drops and recreates ONLY ``clearquery_demo`` and the two demo
  roles, and only on that private instance.
* Credentials come from environment variables (CLEARQUERY_ADMIN_*, CLEARQUERY_APP_*).

Usage::

    python scripts/build_demo_db.py [--rebuild]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _env  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DB_NAME = "clearquery_demo"
DEMO_ROLES = ("clearquery_app_ro", "analyst_ro")
EXPECTED_DATA_DIR = (ROOT / ".pgdemo" / "data").resolve()


def admin_conn(dbname: str) -> psycopg.Connection:
    return psycopg.connect(host=os.environ["CLEARQUERY_DB_HOST"], port=int(os.environ["CLEARQUERY_DB_PORT"]),
                           user=os.environ["CLEARQUERY_ADMIN_USER"],
                           password=os.environ["CLEARQUERY_ADMIN_PASSWORD"],
                           dbname=dbname, autocommit=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true")
    args = ap.parse_args()
    _env.load()
    if os.environ.get("CLEARQUERY_DB_NAME") != DB_NAME:
        sys.exit(f"refusing: CLEARQUERY_DB_NAME must be {DB_NAME}")

    with admin_conn("postgres") as conn:
        data_dir = Path(conn.execute("SHOW data_directory").fetchone()[0]).resolve()
        if data_dir != EXPECTED_DATA_DIR:
            sys.exit(f"refusing: server data_directory {data_dir} is not the private demo instance")
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,)).fetchone()
        if exists and not args.rebuild:
            sys.exit(f"{DB_NAME} already exists; use --rebuild to recreate it")
        if exists:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(DB_NAME)))
        for role in DEMO_ROLES:
            conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
        conn.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0")
                     .format(sql.Identifier(DB_NAME)))
        # Private instance only (checked above): the app role may connect to
        # the demo database and nothing else.
        conn.execute("REVOKE CONNECT ON DATABASE postgres FROM PUBLIC")
        conn.execute("REVOKE CONNECT ON DATABASE template1 FROM PUBLIC")

    with admin_conn(DB_NAME) as conn:
        conn.execute((ROOT / "db" / "01_schema.sql").read_text(encoding="utf-8"))
        conn.execute((ROOT / "db" / "02_roles.sql").read_text(encoding="utf-8"))
        conn.execute(sql.SQL("ALTER ROLE clearquery_app_ro PASSWORD {}")
                     .format(sql.Literal(os.environ["CLEARQUERY_APP_PASSWORD"])))
        db = sql.Identifier(DB_NAME)
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(db))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO clearquery_app_ro").format(db))
        conn.execute((ROOT / "data" / "seed.sql").read_text(encoding="utf-8"))
        b = conn.execute("SELECT count(*) FROM sales_poc.branches").fetchone()[0]
        o = conn.execute("SELECT count(*) FROM sales_poc.orders").fetchone()[0]
        print(f"built {DB_NAME}: branches={b} orders={o}")


if __name__ == "__main__":
    main()
