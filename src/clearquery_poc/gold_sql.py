"""The five approved Gold SQL fixtures (Phase 1 design review, Q1–Q5).

The fixtures live as files under ``gold/``. They are written against the
allowlisted ``analytics`` views with unqualified names (the read-only role's
``search_path`` is ``analytics``). Metric definitions are fixed (B3):
``total_sales = SUM(total_amount)``, ``order_count = COUNT(order_id)``.
"""

from __future__ import annotations

from pathlib import Path

GOLD_DIR = Path(__file__).resolve().parents[2] / "gold"

GOLD_FILES = {
    "Q1": "Q1_total_sales_per_branch_oct_2026.sql",
    "Q2": "Q2_order_count_per_branch_oct_2026.sql",
    "Q3": "Q3_total_sales_all_branches_oct_2026.sql",
    "Q4": "Q4_total_sales_maadi_oct_2026.sql",
    "Q5": "Q5_top_branch_total_sales_oct_2026.sql",
}


def load(qid: str) -> str:
    """Return the exact fixture text for ``qid`` (comments included)."""
    return (GOLD_DIR / GOLD_FILES[qid]).read_text(encoding="utf-8")


def load_all() -> dict[str, str]:
    return {qid: load(qid) for qid in GOLD_FILES}
