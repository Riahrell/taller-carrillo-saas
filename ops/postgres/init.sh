#!/bin/sh
# Sourced by the official image during first initialization. Never use set -x.
set -eu

TC_OWNER_PASSWORD="$(cat /run/secrets/owner_password)"
TC_APP_PASSWORD="$(cat /run/secrets/app_password)"
export TC_OWNER_PASSWORD TC_APP_PASSWORD

psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --set ON_ERROR_STOP=1 <<'SQL'
\getenv owner_password TC_OWNER_PASSWORD
\getenv app_password TC_APP_PASSWORD
\getenv db_name POSTGRES_DB
SET log_statement = 'none';
SET log_min_error_statement = 'panic';
CREATE ROLE carrillo_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'owner_password';
CREATE ROLE carrillo_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_password';
ALTER DATABASE :"db_name" OWNER TO carrillo_owner;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO carrillo_owner;
GRANT USAGE ON SCHEMA public TO carrillo_app;
ALTER DEFAULT PRIVILEGES FOR ROLE carrillo_owner IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO carrillo_app;
ALTER DEFAULT PRIVILEGES FOR ROLE carrillo_owner IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO carrillo_app;
SQL

unset TC_OWNER_PASSWORD TC_APP_PASSWORD
