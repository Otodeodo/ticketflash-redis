-- =============================================================================
-- TicketFlash · Consultas representativas y su plan de ejecución
-- 03_queries_eficientes.sql  -  Uso:  psql "$DATABASE_URL" -f sql/03_queries_eficientes.sql
-- Cada consulta se apoya en un índice definido en 01_schema.sql.
-- (Con tablas muy pequeñas PostgreSQL puede preferir Seq Scan; para ver el índice:
--  SET enable_seqscan = off;  o cargar más datos con scripts/seed.py --bulk 20000)
-- =============================================================================
SET search_path = ticketflash;

-- Q1. Catálogo filtrado por categoría ordenado por fecha  -> ix_events_category_date
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM v_event_catalog WHERE category = 'musica' ORDER BY starts_at;

-- Q2. Historial de un usuario, lo más reciente primero  -> ix_orders_user_date
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, event_id, qty, total, status, created_at
FROM orders WHERE user_id = 1 ORDER BY created_at DESC LIMIT 20;

-- Q3. Ventas e ingresos por evento (agregación con FILTER, un solo recorrido)
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM v_sales_by_event ORDER BY revenue DESC LIMIT 5;

-- Q4. Compra atómica sin sobreventa: UPDATE condicional (bloquea solo UNA fila).
--     Devuelve 0 filas si no quedan entradas; la comprobación y el descuento son
--     una sola operación, por lo que no existe ventana entre "leer" y "escribir".
--     (EXPLAIN sin ANALYZE para no modificar datos.)
EXPLAIN
UPDATE events SET sold = sold + 2, version = version + 1
WHERE id = 1 AND sold + 2 <= capacity RETURNING price;

-- Q5. Auditoría reciente  -> ix_audit_at
EXPLAIN (ANALYZE)
SELECT at, db_user, action, entity, entity_id, detail
FROM audit_log ORDER BY at DESC LIMIT 20;

-- Q6. Verificación de consistencia del contador denormalizado (debe devolver 0 filas)
SELECT * FROM v_sold_consistency WHERE NOT consistent;
