-- Remove per-database and per-role setting overrides (ALTER DATABASE/ROLE ... SET).
-- The healthy baseline keeps every server setting in conf.d.
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT s.setdatabase, s.setrole, d.datname, a.rolname
        FROM pg_db_role_setting AS s
        LEFT JOIN pg_database AS d ON d.oid = s.setdatabase
        LEFT JOIN pg_roles AS a ON a.oid = s.setrole
    LOOP
        IF r.setrole = 0 THEN
            EXECUTE format('ALTER DATABASE %I RESET ALL', r.datname);
        ELSIF r.setdatabase = 0 THEN
            EXECUTE format('ALTER ROLE %I RESET ALL', r.rolname);
        ELSE
            EXECUTE format('ALTER ROLE %I IN DATABASE %I RESET ALL', r.rolname, r.datname);
        END IF;
    END LOOP;
END
$$;
