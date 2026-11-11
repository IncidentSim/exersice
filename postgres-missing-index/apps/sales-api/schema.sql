-- Schema of the sales-api application. Safe to apply repeatedly.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'sales_api') THEN
        CREATE ROLE sales_api LOGIN;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS sales (
    id          bigserial PRIMARY KEY,
    customer_id integer NOT NULL,
    product     text NOT NULL,
    amount      numeric(10, 2) NOT NULL,
    sold_at     timestamptz NOT NULL
);

GRANT SELECT ON sales TO sales_api;
