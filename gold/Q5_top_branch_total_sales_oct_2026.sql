-- Q5. Branch with the highest total recorded sales, October 2026.
-- AR: أنهي فرع عنده أعلى إجمالي مبيعات في أكتوبر 2026؟
SELECT b.branch_name,
       SUM(o.total_amount) AS total_sales
FROM orders o
JOIN branches b ON b.branch_id = o.branch_id
WHERE o.order_date >= DATE '2026-10-01'
  AND o.order_date <  DATE '2026-11-01'
GROUP BY b.branch_id, b.branch_name
ORDER BY total_sales DESC
FETCH FIRST 1 ROWS WITH TIES
