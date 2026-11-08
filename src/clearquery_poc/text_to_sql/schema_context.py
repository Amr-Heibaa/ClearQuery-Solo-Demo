"""Schema context given to the model: the two approved ``analytics`` views only.

Format follows XiYanSQL's recommended M-Schema layout (written by hand from
``db/01_schema.sql``; the model never sees ``sales_poc`` or any other object).
Example values are the four fictitious branch names (entity values, not answers).
"""

from __future__ import annotations

SCHEMA_CONTEXT_ID = "analytics-views-mschema-v1"

APPROVED_OBJECTS = frozenset({"branches", "orders", "analytics.branches", "analytics.orders"})

SCHEMA_CONTEXT = """【DB_ID】 clearquery_demo
【Schema】
# Table: analytics.branches
[
(branch_id:INTEGER, Primary Key, Examples: [1, 2, 3, 4]),
(branch_name:VARCHAR(80), branch name, Examples: [Nasr City, Heliopolis, Maadi, Alexandria])
]
# Table: analytics.orders
[
(order_id:INTEGER, Primary Key, one row per recorded order),
(branch_id:INTEGER, branch of the order),
(order_date:DATE, calendar date of the order, Examples: [2026-10-01, 2026-10-31]),
(total_amount:NUMERIC(12,2), recorded order amount in EGP)
]
【Foreign keys】
analytics.orders.branch_id=analytics.branches.branch_id"""
