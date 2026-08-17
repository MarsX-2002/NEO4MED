-- 002_schemas.sql
-- Три слоя:
--   raw  — приёмник выгрузок «как есть», всё text, ничего не валидируем
--   core — нормализованная предметная модель, единственный источник правды
--   ai   — векторы и артефакты EA-помощника, отделены от ядра намеренно

CREATE SCHEMA IF NOT EXISTS raw  AUTHORIZATION ezgumed;
CREATE SCHEMA IF NOT EXISTS core AUTHORIZATION ezgumed;
CREATE SCHEMA IF NOT EXISTS ai   AUTHORIZATION ezgumed;

COMMENT ON SCHEMA raw  IS 'Сырые выгрузки источников 1:1, только text. Пересобирается из файлов.';
COMMENT ON SCHEMA core IS 'Нормализованная модель вакансий/организаций. Источник правды.';
COMMENT ON SCHEMA ai   IS 'Эмбеддинги и документы для EA-помощника. Пересчитывается из core.';

GRANT USAGE ON SCHEMA public TO ezgumed;

ALTER DATABASE ezgumed SET search_path = core, public;
