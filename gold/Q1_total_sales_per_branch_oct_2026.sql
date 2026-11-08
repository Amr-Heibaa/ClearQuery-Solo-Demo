-- Q1. Total recorded sales per branch, October 2026.
-- AR: إجمالي المبيعات لكل فرع في أكتوبر 2026 كام؟
-- Metric total_sales = SUM(total_amount); dimension branch; [2026-10-01, 2026-11-01).
SELECT b.branch_name,
       COALESCE(SUM(o.total_amount), 0) AS total_sales
FROM branches b
LEFT JOIN orders o
       ON o.branch_id = b.branch_id
      AND o.order_date >= DATE '2026-10-01'
      AND o.order_date <  DATE '2026-11-01'
GROUP BY b.branch_id, b.branch_name
ORDER BY b.branch_name
