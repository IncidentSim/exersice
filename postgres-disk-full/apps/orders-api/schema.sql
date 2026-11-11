-- Schema of the orders-api application. Safe to apply repeatedly.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'orders_api') THEN
        CREATE ROLE orders_api LOGIN;
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS orders (
    id         bigserial PRIMARY KEY,
    item       text NOT NULL,
    note       text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS order_history (
    id         bigserial PRIMARY KEY,
    item       text NOT NULL,
    note       text NOT NULL,
    created_at timestamptz NOT NULL
);

GRANT SELECT, INSERT ON orders TO orders_api;
GRANT USAGE ON SEQUENCE orders_id_seq TO orders_api;
GRANT SELECT ON order_history TO orders_api;
