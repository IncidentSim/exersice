-- Sales history: :rows rows spread over :customers customers.
INSERT INTO sales (customer_id, product, amount, sold_at)
SELECT 1 + (g::bigint * 7919) % :customers,
       'sku-' || lpad((g % 5000)::text, 5, '0'),
       ((g % 997) + 1) / 10.0,
       now() - (g % 525600) * interval '1 minute'
FROM generate_series(1, :rows) AS g;
