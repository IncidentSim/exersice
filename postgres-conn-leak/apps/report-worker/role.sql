-- Database role of the report-worker. Safe to apply repeatedly.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'report_worker') THEN
        CREATE ROLE report_worker LOGIN;
    END IF;
END
$$;

GRANT SELECT ON orders TO report_worker;
