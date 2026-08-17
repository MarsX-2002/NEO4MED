-- 010_ownership.sql
-- Делаем ezgumed владельцем всех объектов в raw/core/ai.
--
-- Зачем: миграции выполняются локальным суперпользователем (CREATE EXTENSION
-- иначе не пройдёт), и объекты оставались за ним. Из-за этого pg_dump тащил в
-- дамп строки вида «ALTER DEFAULT PRIVILEGES FOR ROLE <локальный_админ>»,
-- а на сервере такой роли нет — восстановление падало.
-- Владелец ezgumed существует на всех машинах, значит дамп становится переносимым.
-- Плюс владельцу не нужны GRANT'ы: права из 009 после этого избыточны.

DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT n.nspname, c.relname, c.relkind
             FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname IN ('raw','core','ai')
               AND c.relkind IN ('r','v','m','S','p')   -- таблицы, вьюхи, матвьюхи, секвенции
               -- Секвенции identity/serial пропускаем: они привязаны к колонке
               -- и владельца меняют только вместе со своей таблицей.
               AND NOT (c.relkind = 'S' AND EXISTS (
                   SELECT 1 FROM pg_depend d
                   WHERE d.objid = c.oid AND d.deptype IN ('a','i')))
             ORDER BY CASE c.relkind WHEN 'r' THEN 0 WHEN 'p' THEN 0 ELSE 1 END
    LOOP
        EXECUTE format('ALTER %s %I.%I OWNER TO ezgumed',
            CASE r.relkind
                WHEN 'S' THEN 'SEQUENCE'
                WHEN 'v' THEN 'VIEW'
                WHEN 'm' THEN 'MATERIALIZED VIEW'
                ELSE 'TABLE'
            END, r.nspname, r.relname);
    END LOOP;

    FOR r IN SELECT n.nspname, p.oid::regprocedure AS sig
             FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
             WHERE n.nspname IN ('raw','core','ai')
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO ezgumed', r.sig);
    END LOOP;

    FOR r IN SELECT n.nspname, t.typname
             FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
             WHERE n.nspname IN ('raw','core','ai') AND t.typtype = 'e'
    LOOP
        EXECUTE format('ALTER TYPE %I.%I OWNER TO ezgumed', r.nspname, r.typname);
    END LOOP;
END $$;

-- Снимаем default privileges, привязанные к роли, которая гоняла миграции.
-- Именно эти строки делали дамп непереносимым.
ALTER DEFAULT PRIVILEGES IN SCHEMA raw, core, ai
    REVOKE ALL ON TABLES FROM ezgumed;
ALTER DEFAULT PRIVILEGES IN SCHEMA raw, core, ai
    REVOKE ALL ON SEQUENCES FROM ezgumed;
ALTER DEFAULT PRIVILEGES IN SCHEMA raw, core, ai
    REVOKE ALL ON FUNCTIONS FROM ezgumed;

-- Дальше объекты создаёт сам ezgumed и остаётся их владельцем,
-- так что новых GRANT'ов не потребуется.
