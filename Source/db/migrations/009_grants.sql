-- 009_grants.sql
-- Миграции выполняются суперпользователем (расширения того требуют),
-- поэтому права на созданные объекты выдаём прикладной роли явно.

GRANT USAGE ON SCHEMA raw, core, ai TO ezgumed;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA raw, core, ai TO ezgumed;
GRANT USAGE, SELECT                 ON ALL SEQUENCES  IN SCHEMA raw, core, ai TO ezgumed;
GRANT EXECUTE                       ON ALL FUNCTIONS  IN SCHEMA raw, core, ai TO ezgumed;

-- Чтобы будущие объекты не требовали повторного GRANT
ALTER DEFAULT PRIVILEGES IN SCHEMA raw, core, ai
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ezgumed;
ALTER DEFAULT PRIVILEGES IN SCHEMA raw, core, ai
    GRANT USAGE, SELECT ON SEQUENCES TO ezgumed;
ALTER DEFAULT PRIVILEGES IN SCHEMA raw, core, ai
    GRANT EXECUTE ON FUNCTIONS TO ezgumed;
