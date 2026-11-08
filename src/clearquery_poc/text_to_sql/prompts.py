"""Prompt construction for XiYanSQL-QwenCoder-7B-2504.

The template is the official one from the model card (Chinese instructions,
placeholders for dialect / question / db_schema / evidence). The ``evidence``
section is built *generically* from the resolved BIR (metric definition, date
range, branch filter, grouping, ranking) plus fixed demo rules. It never
contains SQL for a specific question.
"""

from __future__ import annotations

import hashlib
from typing import Any

# v2 (2026-11-08): adds the zero-not-NULL total_sales rule (COALESCE) for non-ranking
# total_sales shapes. v1 evidence is otherwise unchanged.
# v3 (2026-11-08): adds three additive lines — per-branch rows keep zero-order branches
# (LEFT JOIN from branches, date filter in ON), COUNT(orders.order_id) for order_count,
# and explicit tie-preserving ranking output. v2 lines are unchanged.
# v4 (2026-11-08): the two v3 shape lines (PER_BRANCH_RULE, rank_rule) now name the canonical
# aliases, join direction/equality, grouping (b.branch_id, b.branch_name) and per-branch ordering.
# No other line changed.
PROMPT_TEMPLATE_ID = "xiyan-nl2sql-cn-card-v1+bir-evidence-v4"

# Verbatim from the XiYanSQL-QwenCoder-7B-2504 model card ("Prompt Template").
NL2SQL_TEMPLATE_CN = """你是一名{dialect}专家，现在需要阅读并理解下面的【数据库schema】描述，以及可能用到的【参考信息】，并运用{dialect}知识生成sql语句回答【用户问题】。
【用户问题】
{question}

【数据库schema】
{db_schema}

【参考信息】
{evidence}

【用户问题】
{question}

```sql"""

METRIC_DEFINITIONS = {
    "total_sales": "total_sales = SUM(orders.total_amount)",
    "order_count": "order_count = COUNT(orders.order_id)",
}

# Approved business rule: for total_sales aggregates an empty matching order set
# returns 0, not NULL. Applies to overall, branch-filtered and per-branch shapes.
# NOT applied to ranking (top-branch) requests: the approved Gold Q5 keeps a bare
# SUM there, and changing that needs separate authorization.
TOTAL_SALES_ZERO_RULE = ("Write the total_sales aggregate as COALESCE(SUM(orders.total_amount), 0) "
                         "so that an empty set of matching orders returns 0, not NULL.")

ORDER_COUNT_RULE = "Write the order_count aggregate as COUNT(orders.order_id), not COUNT(*)."

# Per-branch (non-ranking) results must keep branches that have no matching orders.
# v4: canonical aliases, join equality direction, grouping and ordering.
PER_BRANCH_RULE = ("Keep every branch in the result, including branches with no matching orders: "
                   "select FROM analytics.branches AS b LEFT JOIN analytics.orders AS o ON o.branch_id = b.branch_id, "
                   "put the order_date filter in the JOIN ON clause (not in WHERE), refer to order columns through o, "
                   "GROUP BY b.branch_id, b.branch_name, ORDER BY b.branch_name, and return branch_name and the metric.")


def rank_rule(metric: str, order: str, limit: int) -> str:
    """Tie-preserving ranking output (approved shape: desc, top 1). v4: canonical orders-first join."""
    return (f"Return both branch_name and {metric} for each branch: select FROM analytics.orders AS o "
            f"JOIN analytics.branches AS b ON b.branch_id = o.branch_id, apply the date filter to o.order_date in WHERE, "
            f"GROUP BY b.branch_id, b.branch_name, ORDER BY {metric} {order.upper()}, "
            f"and use FETCH FIRST {limit} ROWS WITH TIES so that all tied branches are returned; do not use LIMIT.")


FIXED_RULES = (
    "Use only the tables analytics.branches and analytics.orders.",
    "Write exactly one read-only PostgreSQL SELECT statement.",
    "Use half-open date ranges: order_date >= start AND order_date < end. "
    "Never use CURRENT_DATE, NOW() or BETWEEN for dates.",
)


def build_evidence(bir: dict[str, Any]) -> str:
    lines: list[str] = []
    metric = bir["resolved_metric"]
    lines.append(f"Metric: {METRIC_DEFINITIONS[metric]}.")
    if metric == "total_sales" and not bir.get("rank"):
        lines.append(TOTAL_SALES_ZERO_RULE)
    if metric == "order_count":
        lines.append(ORDER_COUNT_RULE)
    tr = bir.get("time_range")
    if tr:
        lines.append(f"Date filter: order_date >= DATE '{tr['start']}' AND order_date < DATE '{tr['end']}'.")
    branch = (bir.get("filters") or {}).get("branch")
    if branch:
        lines.append(f"Branch filter: branch_name = '{branch}'.")
    rank = bir.get("rank")
    if "branch" in (bir.get("dimensions") or []):
        lines.append("Group the result by branch and return branch_name.")
        if not rank:
            lines.append(PER_BRANCH_RULE)
    if rank:
        lines.append(f"Order by the metric {rank['order'].upper()} and return the top {rank['limit']} branch(es), "
                     "including ties.")
        lines.append(rank_rule(metric, rank["order"], rank["limit"]))
    lines.extend(FIXED_RULES)
    return "\n".join(lines)


def build_prompt(question_en: str, bir: dict[str, Any], schema_context: str) -> str:
    return NL2SQL_TEMPLATE_CN.format(dialect="PostgreSQL", question=question_en,
                                     db_schema=schema_context, evidence=build_evidence(bir))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
