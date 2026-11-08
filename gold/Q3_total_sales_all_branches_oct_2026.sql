-- Q3. Total recorded sales across all branches, October 2026.
-- AR: إجمالي المبيعات في كل الفروع في أكتوبر 2026 كام؟
SELECT COALESCE(SUM(total_amount), 0) AS total_sales
FROM orders
WHERE order_date >= DATE '2026-10-01'
  AND order_date <  DATE '2026-11-01'
