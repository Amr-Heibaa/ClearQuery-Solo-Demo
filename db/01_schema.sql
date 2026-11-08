-- ClearQuery V3.1 — 10% demo (Phase 2a), Person 1 (database).
-- PoC-only SIMPLIFIED schema. SYNTHETIC data only.
-- This is NOT the canonical V3.1 six-table Sales model
-- The canonical graduation-project dataset strategy is outside this demo.
-- Run by a human operator / setup script, in the dedicated demo database only.
-- Approved decisions: B2 (schema), B5 (DATE + half-open ranges).

BEGIN;

CREATE SCHEMA sales_poc;   -- base tables: owner only
CREATE SCHEMA analytics;   -- allowlisted views: the only objects the app role can see

CREATE TABLE sales_poc.branches (
    branch_id      integer     NOT NULL,
    branch_name    varchar(80) NOT NULL,
    branch_name_ar varchar(80) NOT NULL,
    CONSTRAINT branches_pk                PRIMARY KEY (branch_id),
    CONSTRAINT branches_name_uq           UNIQUE (branch_name),
    CONSTRAINT branches_name_ar_uq        UNIQUE (branch_name_ar),
    CONSTRAINT branches_id_positive       CHECK (branch_id > 0),
    CONSTRAINT branches_name_not_blank    CHECK (btrim(branch_name) <> ''),
    CONSTRAINT branches_name_ar_not_blank CHECK (btrim(branch_name_ar) <> '')
);

CREATE TABLE sales_poc.orders (
    order_id     integer       NOT NULL,
    branch_id    integer       NOT NULL,
    order_date   date          NOT NULL,
    total_amount numeric(12,2) NOT NULL,
    CONSTRAINT orders_pk            PRIMARY KEY (order_id),
    CONSTRAINT orders_branch_fk     FOREIGN KEY (branch_id)
        REFERENCES sales_poc.branches (branch_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT orders_id_positive   CHECK (order_id > 0),
    CONSTRAINT orders_amount_nonneg CHECK (total_amount >= 0),
    -- Demo window per Phase 2a decision: Aug 2026 .. 2026-11-01 (as_of_date) inclusive.
    CONSTRAINT orders_date_in_demo_window
        CHECK (order_date >= DATE '2026-08-01' AND order_date < DATE '2026-11-02')
);

CREATE INDEX orders_branch_id_idx  ON sales_poc.orders (branch_id);
CREATE INDEX orders_order_date_idx ON sales_poc.orders (order_date);

-- Allowlisted views. security_invoker is NOT set: the views read the base
-- tables with the view owner's rights, so the read-only role needs no grant
-- on sales_poc. branch_name_ar is intentionally not exposed to Text-to-SQL;
-- Arabic aliases are handed to Person 3 via data/branch_catalog.json.
CREATE VIEW analytics.branches AS
    SELECT branch_id, branch_name
    FROM sales_poc.branches;

CREATE VIEW analytics.orders AS
    SELECT order_id, branch_id, order_date, total_amount
    FROM sales_poc.orders;

COMMENT ON VIEW   analytics.branches IS 'SYNTHETIC demo data. Fictitious branches.';
COMMENT ON VIEW   analytics.orders   IS 'SYNTHETIC demo data. One row per recorded order.';
COMMENT ON COLUMN analytics.orders.total_amount IS 'Recorded order amount in EGP. Not net of returns, discounts, cost or tax.';
COMMENT ON COLUMN analytics.orders.order_date   IS 'Calendar date of the order (DATE: no time, no timezone).';

COMMIT;
