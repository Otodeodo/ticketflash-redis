-- =============================================================================
-- TicketFlash · Usuarios, roles y privilegios (PostgreSQL)
-- 02_roles_privileges.sql  -  Modelo de seguridad por mínimo privilegio (RBAC).
--
-- Ejecutar DESPUÉS de 01_schema.sql y con el usuario dueño de la base.
-- ANTES de ejecutarlo cambia las tres contraseñas marcadas con CAMBIA_ESTA_CLAVE_*
-- (o usa  python scripts/init_db.py  que las recibe por parámetro).
--
--   Roles de grupo (NOLOGIN)        Usuarios de conexión (LOGIN)
--   ------------------------        ----------------------------
--   tf_role_app      ------------>  tf_app       (la aplicación web / API)
--   tf_role_readonly ------------>  tf_analyst   (reportes y consultas)
--   tf_role_backup   ------------>  tf_backup    (pg_dump del respaldo remoto)
-- =============================================================================

-- Permite ejecutar este archivo por separado (p. ej. tras restaurar un respaldo).
SET search_path = ticketflash, pg_temp;

-- Los roles son globales en el servidor: se crean solo si no existen.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tf_role_app') THEN
        CREATE ROLE tf_role_app NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tf_role_readonly') THEN
        CREATE ROLE tf_role_readonly NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tf_role_backup') THEN
        CREATE ROLE tf_role_backup NOLOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tf_app') THEN
        CREATE ROLE tf_app LOGIN PASSWORD 'CAMBIA_ESTA_CLAVE_APP'
            NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tf_analyst') THEN
        CREATE ROLE tf_analyst LOGIN PASSWORD 'CAMBIA_ESTA_CLAVE_ANALYST'
            NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'tf_backup') THEN
        CREATE ROLE tf_backup LOGIN PASSWORD 'CAMBIA_ESTA_CLAVE_BACKUP'
            NOSUPERUSER NOCREATEDB NOCREATEROLE;
    END IF;
END $$;

GRANT tf_role_app      TO tf_app;
GRANT tf_role_readonly TO tf_analyst;
GRANT tf_role_backup   TO tf_backup;

-- Límites de abuso: conexiones simultáneas, consultas lentas y transacciones olvidadas.
ALTER ROLE tf_app     CONNECTION LIMIT 20;
ALTER ROLE tf_analyst CONNECTION LIMIT 5;
ALTER ROLE tf_backup  CONNECTION LIMIT 2;
ALTER ROLE tf_app     SET statement_timeout = '10s';
ALTER ROLE tf_analyst SET statement_timeout = '30s';
ALTER ROLE tf_app     SET idle_in_transaction_session_timeout = '15s';
-- Las conexiones por pool (Neon/pgbouncer) no siempre conservan SET de sesión:
-- el search_path se fija a nivel de rol.
ALTER ROLE tf_app     SET search_path = ticketflash, public;
ALTER ROLE tf_analyst SET search_path = ticketflash, public;
ALTER ROLE tf_backup  SET search_path = ticketflash, public;

-- Nadie, salvo quien se otorgue explícitamente, toca el esquema.
REVOKE ALL ON SCHEMA ticketflash FROM PUBLIC;
REVOKE ALL ON ALL TABLES    IN SCHEMA ticketflash FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA ticketflash FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA ticketflash FROM PUBLIC;

GRANT USAGE ON SCHEMA ticketflash TO tf_role_app, tf_role_readonly, tf_role_backup;

-- ----------------------------------------------------------------------------
-- tf_role_app: lo estrictamente necesario para operar el negocio.
-- ----------------------------------------------------------------------------
GRANT SELECT, INSERT          ON categories, venues TO tf_role_app;
GRANT SELECT, INSERT, DELETE  ON users, events      TO tf_role_app;
GRANT SELECT, INSERT          ON orders             TO tf_role_app;
GRANT SELECT                  ON audit_log          TO tf_role_app;   -- lectura; escribe el trigger
-- UPDATE por columna: las órdenes solo pueden cancelarse; el precio unitario,
-- el usuario y el evento de una orden confirmada son inmutables.
GRANT UPDATE (status, cancelled_at) ON orders TO tf_role_app;
GRANT UPDATE (name, email, password_hash, active) ON users TO tf_role_app;
GRANT UPDATE (name, venue_id, category_id, starts_at, price, capacity, sold, version)
    ON events TO tf_role_app;
GRANT SELECT ON v_event_catalog, v_sales_by_event, v_sold_consistency TO tf_role_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA ticketflash TO tf_role_app;
-- (No se concede DELETE sobre orders ni audit_log, ni TRUNCATE, ni DDL.)

-- ----------------------------------------------------------------------------
-- tf_role_readonly: reportes. No ve password_hash ni la auditoría.
-- ----------------------------------------------------------------------------
GRANT SELECT ON categories, venues, events, orders TO tf_role_readonly;
GRANT SELECT (id, name, email, role, active, created_at) ON users TO tf_role_readonly;
GRANT SELECT ON v_event_catalog, v_sales_by_event, v_sold_consistency TO tf_role_readonly;

-- ----------------------------------------------------------------------------
-- tf_role_backup: lectura total, necesaria para pg_dump (tablas y secuencias).
-- ----------------------------------------------------------------------------
GRANT SELECT ON ALL TABLES    IN SCHEMA ticketflash TO tf_role_backup;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA ticketflash TO tf_role_backup;

-- Objetos que se creen más adelante heredan los mismos permisos.
ALTER DEFAULT PRIVILEGES IN SCHEMA ticketflash GRANT SELECT ON TABLES    TO tf_role_backup;
ALTER DEFAULT PRIVILEGES IN SCHEMA ticketflash GRANT SELECT ON SEQUENCES TO tf_role_backup;
