-- 005_core_vacancies.sql
-- Ядро модели. Одна строка = одна вакансия источника (external_id уникален).

-- Уровень детализации записи. В выгрузке два класса строк, и это не шум:
--   full  — есть регион, район, обязанности, требования, условия, ОКЭД, дата окончания
--   basic — этих полей нет вообще, только организация, должность, зарплата, адрес
-- Помощнику это знать обязательно: иначе он будет «додумывать» отсутствующее.
DO $$ BEGIN
    CREATE TYPE core.detail_level AS ENUM ('full', 'basic');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS core.vacancies (
    external_id       bigint PRIMARY KEY,          -- id вакансии в mehnat.uz
    detail_level      core.detail_level NOT NULL,

    -- что за работа
    position_id       integer REFERENCES core.positions(id),
    title_uz          text NOT NULL,               -- фактическое название, самое информативное поле
    duties            text,
    requirements      text,
    conditions        text,
    benefits          text,

    -- кто наниматель
    organization_id   bigint  NOT NULL REFERENCES core.organizations(id),
    department_id     bigint  REFERENCES core.departments(id),

    -- где
    region_id         integer REFERENCES core.regions(id),
    district_id       integer REFERENCES core.districts(id),
    address           text,                        -- фактический_адрес

    -- деньги и объём
    salary            numeric(14,2),               -- сум/мес как в API
    salary_quality    text NOT NULL DEFAULT 'ok'
                        REFERENCES core.salary_quality_levels(code),
    rate              numeric(4,2),                -- 1.00 / 1.50 / 2.00 ставки

    -- требования к кандидату
    education_code    text REFERENCES core.education_levels(code),
    experience_years  smallint,
    languages         text[],                      -- ['uz','ru'] из '; '-списка

    -- контакты
    phones            text[],
    email             text,

    -- сроки и активность
    date_start        date,
    date_end          date,                        -- NULL = бессрочно ('9999-01-01' в источнике)
    is_urgent         boolean,                     -- см. коммент: коррелирует с detail_level
    views             integer,
    applications      integer,

    -- происхождение
    api_url           text,
    source_batch_id   uuid REFERENCES raw.load_batches(batch_id),
    imported_at       timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_vacancies_experience CHECK (experience_years IS NULL OR experience_years BETWEEN 0 AND 60),
    CONSTRAINT ck_vacancies_rate       CHECK (rate IS NULL OR rate > 0),
    CONSTRAINT ck_vacancies_salary     CHECK (salary IS NULL OR salary >= 0),
    CONSTRAINT ck_vacancies_dates      CHECK (date_end IS NULL OR date_start IS NULL OR date_end >= date_start),
    -- инвариант выгрузки: у basic-записей детальные поля обязаны быть пустыми
    CONSTRAINT ck_vacancies_basic_shape CHECK (
        detail_level <> 'basic'
        OR (region_id IS NULL AND duties IS NULL AND requirements IS NULL)
    )
);

COMMENT ON TABLE core.vacancies IS 'Вакансии медучреждений Узбекистана. Одна строка = один external_id источника.';
COMMENT ON COLUMN core.vacancies.detail_level IS
  'full — карточка с регионом/обязанностями/требованиями; basic — только базовые поля. '
  'Фильтруй по этому полю, прежде чем строить статистику по регионам.';
COMMENT ON COLUMN core.vacancies.is_urgent IS
  'Флаг «срочно» из источника. В текущей выгрузке совпадает с detail_level=full '
  '(2334 да / 1261 нет), то есть как признак срочности недостоверен.';
COMMENT ON COLUMN core.vacancies.salary_quality IS
  'suspicious — зарплата явно в неверных единицах (встречаются значения < 1 сум).';
COMMENT ON COLUMN core.vacancies.title_uz IS
  'Название должности как её написал работодатель. Основной источник специализации.';

-- Полнотекст: 'simple' конфигурация, потому что тексты смешанные —
-- узбекская латиница, узбекская кириллица и русский в одном поле.
-- Стеммеры для узбекского в PG нет, 'simple' + unaccent + триграммы дают
-- предсказуемый результат без ложной уверенности.
ALTER TABLE core.vacancies
    ADD COLUMN IF NOT EXISTS search_document tsvector
    GENERATED ALWAYS AS (
        to_tsvector('simple',
            coalesce(title_uz, '')      || ' ' ||
            coalesce(duties, '')        || ' ' ||
            coalesce(requirements, '')  || ' ' ||
            coalesce(conditions, '')    || ' ' ||
            coalesce(benefits, '')
        )
    ) STORED;

-- Триггер актуализации updated_at
CREATE OR REPLACE FUNCTION core.tg_touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS tr_vacancies_touch ON core.vacancies;
CREATE TRIGGER tr_vacancies_touch BEFORE UPDATE ON core.vacancies
    FOR EACH ROW EXECUTE FUNCTION core.tg_touch_updated_at();

DROP TRIGGER IF EXISTS tr_organizations_touch ON core.organizations;
CREATE TRIGGER tr_organizations_touch BEFORE UPDATE ON core.organizations
    FOR EACH ROW EXECUTE FUNCTION core.tg_touch_updated_at();
