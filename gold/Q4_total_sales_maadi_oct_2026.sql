-- Q4. Recorded sales of the Maadi branch, October 2026.
-- AR: فرع المعادي باع بكام في أكتوبر 2026؟
SELECT COALESCE(SUM(o.total_amount), 0) AS total_sales
FROM orders o
JOIN branches b ON b.branch_id = o.branch_id
WHERE b.branch_name = 'Maadi'
  AND o.order_date >= DATE '2026-10-01'
  AND o.order_date <  DATE '2026-11-01'
