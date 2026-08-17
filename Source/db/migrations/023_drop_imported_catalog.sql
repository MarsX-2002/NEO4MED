-- 023_drop_imported_catalog.sql
-- Сносим импортированный каталог. Модель продукта изменилась: работаем только
-- с вакансиями, которые публикует клиника, и никого не парсим.
--
-- Уходит: 3595 вакансий и 773 организации с ishapi.mehnat.uz, сырой CSV-слой,
-- их эмбеддинги и гибридный поиск по ним.
--
-- ВНИМАНИЕ, две зависимости, из-за которых нельзя просто DROP SCHEMA CASCADE:
--   1. одиннадцать триггеров updated_at в product вызывают core.tg_touch_updated_at
--      — каскад снёс бы их молча, и updated_at перестал бы обновляться по всей
--      продуктовой схеме;
--   2. product.kb_chunks ссылается на ai.embedding_models.
-- Поэтому сначала переносим общее, потом сносим каталог.
--
-- Данные восстановимы: перед миграцией снят дамп, исходный CSV и скрипт
-- выгрузки с ishapi.mehnat.uz сохранены.

-- ══ 1. Общая функция переезжает в product ═════════════════════════════════════
CREATE OR REPLACE FUNCTION product.tg_touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END $$;

ALTER FUNCTION product.tg_touch_updated_at() OWNER TO ezgumed;

-- Перевешиваем все триггеры product на новую функцию. Перебором, а не
-- перечислением: список таблиц с updated_at будет расти, и забытый триггер
-- обнаружился бы только тем, что поле перестало обновляться.
DO $$
DECLARE r record;
BEGIN
    FOR r IN
        SELECT c.relname AS tbl, t.tgname AS trg
        FROM pg_trigger t
        JOIN pg_class c ON c.oid = t.tgrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_proc p ON p.oid = t.tgfoid
        JOIN pg_namespace pn ON pn.oid = p.pronamespace
        WHERE n.nspname = 'product' AND pn.nspname = 'core' AND NOT t.tgisinternal
    LOOP
        EXECUTE format('DROP TRIGGER %I ON product.%I', r.trg, r.tbl);
        EXECUTE format(
            'CREATE TRIGGER %I BEFORE UPDATE ON product.%I '
            'FOR EACH ROW EXECUTE FUNCTION product.tg_touch_updated_at()',
            r.trg, r.tbl);
        RAISE NOTICE 'триггер %.% переведён на product.tg_touch_updated_at', r.tbl, r.trg;
    END LOOP;
END $$;

-- ══ 2. Каталог и всё, что только для него ═════════════════════════════════════
-- Поиск и эмбеддинги вакансий каталога: без каталога они бессмысленны.
DROP FUNCTION IF EXISTS ai.search_vacancies(vector, text, integer, text, numeric, text);
DROP VIEW IF EXISTS ai.v_embed_queue;
DROP FUNCTION IF EXISTS ai.vacancy_embed_text(bigint);
DROP TABLE IF EXISTS ai.vacancy_embeddings;

-- ai.documents/document_chunks были заготовкой под базу знаний, которую
-- заменили product.kb_documents/kb_chunks. Пустые, дублируют смысл.
DROP TABLE IF EXISTS ai.document_chunks;
DROP TABLE IF EXISTS ai.documents;

-- Схема ai остаётся: embedding_models нужна product.kb_chunks, model_calls —
-- журнал обращений к моделям, он пригодится интервью.
COMMENT ON SCHEMA ai IS
  'Инфраструктура моделей: реестр эмбеддингов и журнал вызовов. Каталог вакансий '
  'и поиск по нему удалены в миграции 023.';

DROP VIEW IF EXISTS core.v_vacancies;
DROP SCHEMA IF EXISTS raw CASCADE;
DROP SCHEMA IF EXISTS core CASCADE;

-- search_path у базы указывал на core: без этого новые сессии будут получать
-- предупреждение о несуществующей схеме.
ALTER DATABASE ezgumed SET search_path = product, public;
