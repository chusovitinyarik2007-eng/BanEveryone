-- ВНИМАНИЕ: запускать только после резервного копирования SQLite
-- и остановки сервисов, которые используют базу.
-- Сбрасываются ТОЛЬКО связи профилей с инбаундами и назначения пулов.
-- Сохраняются: smart_balancers (правила, priority, fallback, strategy),
-- smart_options (RU->DIRECT, имена, частота), все прочие таблицы и клиенты.
BEGIN IMMEDIATE TRANSACTION;
DELETE FROM smart_endpoint_map;
DELETE FROM smart_aliases;
UPDATE smart_nodes SET pools = '[]';
COMMIT;
