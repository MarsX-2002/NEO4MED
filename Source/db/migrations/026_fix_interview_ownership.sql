-- 026_fix_interview_ownership.sql
-- Исправление двух огрехов миграции 024.
--
-- 1. Владелец. Миграции идут на сервере под postgres, поэтому созданные в 024
--    таблицы достались postgres, тогда как вся остальная схема product
--    принадлежит ezgumed. Следствие было не только косметическое: функции
--    витрины объявлены SECURITY DEFINER и работают от ezgumed, а прав на
--    чужие таблицы у него не было — get_published_job падал бы на подсчёте
--    вопросов. В проекте владельца назначают явно, как в 018 и 020.
--
-- 2. FORCE ROW LEVEL SECURITY. Больше нигде в схеме он не применяется, и не
--    случайно: политики начинают действовать и на владельца, а под владельцем
--    сеются демо-данные и тестовые фикстуры. Приложение ходит под ishmed_app,
--    которая владельцем не является, поэтому RLS для неё работает и без FORCE.
--    С FORCE тесты падали на подготовке данных, а не на проверках.

ALTER TABLE product.job_questions   OWNER TO ezgumed;
ALTER TABLE product.interviews      OWNER TO ezgumed;
ALTER TABLE product.interview_turns OWNER TO ezgumed;
ALTER TYPE  interview_status        OWNER TO ezgumed;

ALTER TABLE product.job_questions   NO FORCE ROW LEVEL SECURITY;
ALTER TABLE product.interviews      NO FORCE ROW LEVEL SECURITY;
ALTER TABLE product.interview_turns NO FORCE ROW LEVEL SECURITY;

-- Права после смены владельца сбрасываются не всегда предсказуемо — проставим заново.
GRANT SELECT, INSERT, UPDATE, DELETE ON product.job_questions TO ishmed_app;
GRANT SELECT, INSERT, UPDATE ON product.interviews TO ishmed_app;
GRANT SELECT, INSERT, UPDATE ON product.interview_turns TO ishmed_app;
