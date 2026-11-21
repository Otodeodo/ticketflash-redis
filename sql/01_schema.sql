-- =============================================================================
-- TicketFlash · Base de datos RELACIONAL (PostgreSQL 16/17)
-- 01_schema.sql  -  Creación del esquema, restricciones, índices, vistas y triggers.
--
-- Ejecutar como el DUEÑO de la base (p. ej. neondb_owner en Neon, postgres en local):
--     psql "$ADMIN_DATABASE_URL" -f sql/01_schema.sql
-- o con:  python scripts/init_db.py
--
-- Rol de cada base de datos en el sistema:
--   PostgreSQL = fuente de verdad (usuarios, eventos, órdenes, auditoría) con ACID.
--   Redis      = estado efímero y de alta velocidad (stock en caliente, reservas
--                con TTL, sesiones, ranking, caché). Se puede reconstruir desde aquí.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS ticketflash;
SET search_path = ticketflash, pg_temp;

-- ----------------------------------------------------------------------------
-- Catálogos (3FN: el nombre de la categoría/recinto se guarda una sola vez)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS categories (
    id    smallint    GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name  varchar(40) NOT NULL UNIQUE,
    CONSTRAINT ck_categories_lower CHECK (name = lower(name))
);

CREATE TABLE IF NOT EXISTS venues (
    id    integer      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name  varchar(120) NOT NULL UNIQUE,
    city  varchar(80)
);

-- ----------------------------------------------------------------------------
-- Usuarios. La contraseña se guarda como hash scrypt con sal (nunca en claro).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id             bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name           varchar(80)  NOT NULL,
    email          varchar(254) NOT NULL,
    password_hash  text         NOT NULL,
    role           varchar(10)  NOT NULL DEFAULT 'customer',
    active         boolean      NOT NULL DEFAULT true,
    created_at     timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT ck_users_role  CHECK (role IN ('customer', 'admin')),
    CONSTRAINT ck_users_email CHECK (position('@' IN email) > 1)
);
-- Unicidad del correo sin distinguir mayúsculas.
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email ON users (lower(email));

-- ----------------------------------------------------------------------------
-- Eventos.
--   sold    : entradas vendidas (confirmadas). Invariante: 0 <= sold <= capacity.
--   version : bloqueo optimista para ediciones concurrentes del administrador.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    id           bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name         varchar(120)  NOT NULL,
    venue_id     integer       NOT NULL REFERENCES venues (id)     ON DELETE RESTRICT,
    category_id  smallint      NOT NULL REFERENCES categories (id) ON DELETE RESTRICT,
    starts_at    timestamp     NOT NULL,
    price        numeric(10,2) NOT NULL,
    capacity     integer       NOT NULL,
    sold         integer       NOT NULL DEFAULT 0,
    version      integer       NOT NULL DEFAULT 1,
    created_at   timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT ck_events_price    CHECK (price > 0),
    CONSTRAINT ck_events_capacity CHECK (capacity > 0),
    -- Última línea de defensa contra la sobreventa, aunque falle la lógica de la app.
    CONSTRAINT ck_events_sold     CHECK (sold >= 0 AND sold <= capacity)
);
CREATE INDEX IF NOT EXISTS ix_events_category_date ON events (category_id, starts_at);
CREATE INDEX IF NOT EXISTS ix_events_venue         ON events (venue_id);

-- ----------------------------------------------------------------------------
-- Órdenes. Registro contable: la aplicación no tiene permiso de DELETE sobre ella.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders (
    id            bigint        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       bigint        NOT NULL REFERENCES users (id)  ON DELETE RESTRICT,
    event_id      bigint        NOT NULL REFERENCES events (id) ON DELETE RESTRICT,
    qty           smallint      NOT NULL,
    unit_price    numeric(10,2) NOT NULL,
    total         numeric(12,2) GENERATED ALWAYS AS (qty * unit_price) STORED,
    status        varchar(10)   NOT NULL DEFAULT 'CONFIRMED',
    hold_id       varchar(20),
    created_at    timestamptz   NOT NULL DEFAULT now(),
    cancelled_at  timestamptz,
    CONSTRAINT ck_orders_qty    CHECK (qty BETWEEN 1 AND 10),
    CONSTRAINT ck_orders_price  CHECK (unit_price > 0),
    CONSTRAINT ck_orders_status CHECK (status IN ('CONFIRMED', 'CANCELLED')),
    CONSTRAINT ck_orders_cancel CHECK ((status = 'CANCELLED') = (cancelled_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS ix_orders_user_date ON orders (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_orders_event_confirmed ON orders (event_id) WHERE status = 'CONFIRMED';
-- Idempotencia: una reserva de Redis solo puede convertirse en UNA orden, aunque
-- dos peticiones intenten confirmarla a la vez.
CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_hold ON orders (hold_id) WHERE hold_id IS NOT NULL;

-- ----------------------------------------------------------------------------
-- Auditoría. Solo se escribe desde triggers SECURITY DEFINER: el rol de la
-- aplicación puede LEER pero no insertar, modificar ni borrar entradas.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id         bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at         timestamptz  NOT NULL DEFAULT now(),
    db_user    name         NOT NULL DEFAULT session_user,
    action     varchar(12)  NOT NULL,
    entity     varchar(30)  NOT NULL,
    entity_id  bigint,
    detail     jsonb
);
CREATE INDEX IF NOT EXISTS ix_audit_at ON audit_log (at DESC);

CREATE OR REPLACE FUNCTION fn_audit_orders() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ticketflash, pg_temp AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO audit_log (action, entity, entity_id, detail)
        VALUES ('CREATE', 'order', NEW.id,
                jsonb_build_object('user_id', NEW.user_id, 'event_id', NEW.event_id,
                                   'qty', NEW.qty, 'total', NEW.total));
    ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO audit_log (action, entity, entity_id, detail)
        VALUES ('CANCEL', 'order', NEW.id,
                jsonb_build_object('from', OLD.status, 'to', NEW.status, 'qty', NEW.qty));
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION fn_audit_events() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = ticketflash, pg_temp AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        INSERT INTO audit_log (action, entity, entity_id, detail)
        VALUES ('DELETE', 'event', OLD.id, jsonb_build_object('name', OLD.name));
        RETURN OLD;
    END IF;
    IF NEW.price IS DISTINCT FROM OLD.price THEN
        INSERT INTO audit_log (action, entity, entity_id, detail)
        VALUES ('PRICE', 'event', NEW.id,
                jsonb_build_object('from', OLD.price, 'to', NEW.price));
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS tg_audit_orders ON orders;
CREATE TRIGGER tg_audit_orders AFTER INSERT OR UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION fn_audit_orders();

DROP TRIGGER IF EXISTS tg_audit_events_upd ON events;
CREATE TRIGGER tg_audit_events_upd AFTER UPDATE OF price ON events
    FOR EACH ROW EXECUTE FUNCTION fn_audit_events();

DROP TRIGGER IF EXISTS tg_audit_events_del ON events;
CREATE TRIGGER tg_audit_events_del AFTER DELETE ON events
    FOR EACH ROW EXECUTE FUNCTION fn_audit_events();

-- ----------------------------------------------------------------------------
-- Vistas (consultas reutilizables; el rol de solo lectura trabaja sobre ellas)
-- ----------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_event_catalog AS
SELECT e.id, e.name, v.name AS venue, c.name AS category, e.starts_at,
       e.price, e.capacity, e.sold, e.version
FROM events e
JOIN venues v     ON v.id = e.venue_id
JOIN categories c ON c.id = e.category_id;

CREATE OR REPLACE VIEW v_sales_by_event AS
SELECT e.id AS event_id, e.name,
       COALESCE(SUM(o.qty)   FILTER (WHERE o.status = 'CONFIRMED'), 0) AS tickets_sold,
       COALESCE(SUM(o.total) FILTER (WHERE o.status = 'CONFIRMED'), 0) AS revenue,
       COUNT(o.id)           FILTER (WHERE o.status = 'CONFIRMED')     AS orders_confirmed
FROM events e
LEFT JOIN orders o ON o.event_id = e.id
GROUP BY e.id, e.name;

-- Compara el contador denormalizado events.sold con la suma real de órdenes.
CREATE OR REPLACE VIEW v_sold_consistency AS
SELECT e.id AS event_id, e.name, e.sold,
       COALESCE(SUM(o.qty) FILTER (WHERE o.status = 'CONFIRMED'), 0)::int AS orders_qty,
       e.sold = COALESCE(SUM(o.qty) FILTER (WHERE o.status = 'CONFIRMED'), 0) AS consistent
FROM events e
LEFT JOIN orders o ON o.event_id = e.id
GROUP BY e.id, e.name, e.sold;
