-- Q2. Order count per branch, October 2026.
-- AR: كل فرع عمل كام أوردر في أكتوبر 2026؟
-- Metric order_count = COUNT(order_id); dimension branch; [2026-10-01, 2026-11-01).
SELECT b.branch_name,
       COUNT(o.order_id) AS order_count
FROM branches b
LEFT JOIN orders o
       ON o.branch_id = b.branch_id
      AND o.order_date >= DATE '2026-10-01'
      AND o.order_date <  DATE '2026-11-01'
GROUP BY b.branch_id, b.branch_name
ORDER BY b.branch_name
