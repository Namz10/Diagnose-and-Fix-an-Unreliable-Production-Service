-- Bootstrap schema + seed data for the widget service.
-- The app also does this idempotently on startup (see app/db.py), so this file
-- is belt-and-suspenders for the Postgres container's first boot.
CREATE TABLE IF NOT EXISTS widgets (
    id    SERIAL PRIMARY KEY,
    name  TEXT NOT NULL,
    price NUMERIC(10, 2) NOT NULL
);

INSERT INTO widgets (name, price)
SELECT 'widget-' || g, (g * 1.5)::numeric
FROM generate_series(1, 100) AS g
WHERE NOT EXISTS (SELECT 1 FROM widgets);
