-- Database role of the nightly-fix job. Safe to apply repeatedly.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'nightly_fix') THEN
        CREATE ROLE nightly_fix LOGIN;
    END IF;
END
$$;

GRANT SELECT, UPDATE ON orders TO nightly_fix;
