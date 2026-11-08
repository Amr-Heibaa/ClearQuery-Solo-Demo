-- ClearQuery 10% demo — least-privilege read-only access (rule R1).
-- Run as the demo database owner, connected to the DEDICATED demo database.
-- The login password for clearquery_app_ro is NOT set here: the setup script
-- sets it from an environment variable (never stored in source files).
-- Database-level statements (CONNECT/TEMP) are issued by the setup script
-- because they need the database name.

BEGIN;

-- Group role holding the read grants (cannot log in).
CREATE ROLE analyst_ro NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;

-- Nobody but the owner may create objects in public in this demo DB.
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA sales_poc FROM PUBLIC;
REVOKE ALL ON SCHEMA analytics FROM PUBLIC;

GRANT USAGE  ON SCHEMA analytics TO analyst_ro;
GRANT SELECT ON analytics.branches, analytics.orders TO analyst_ro;

-- Application login role. INHERIT so it gets analyst_ro's SELECT grants.
CREATE ROLE clearquery_app_ro LOGIN INHERIT
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 10;
GRANT analyst_ro TO clearquery_app_ro;

-- Defaults (NOT security boundaries: the real boundary is the absence of
-- write grants; these are defence in depth).
ALTER ROLE clearquery_app_ro SET default_transaction_read_only = on;
ALTER ROLE clearquery_app_ro SET statement_timeout = '5s';
ALTER ROLE clearquery_app_ro SET search_path = analytics;

COMMIT;
