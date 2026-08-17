-- 004_core_dictionaries.sql
-- Справочники. Всё, что в выгрузке повторяется десятки раз и по чему
-- будут фильтровать и группировать, вынесено в отдельные таблицы.

-- ── Организации ───────────────────────────────────────────────────────────────
-- В выгрузке 773 ИНН на 777 написаний названия: один и тот же ИНН приходит
-- то с кавычками и «DAVLAT MUASSASASI», то без. ИНН — ключ, название канонизируем
-- (берём самое длинное написание), остальные варианты складываем в aliases.
CREATE TABLE IF NOT EXISTS core.organizations (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    inn         text NOT NULL UNIQUE,
    name        text NOT NULL,
    oked        text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_organizations_inn CHECK (inn ~ '^[0-9]{9}$')
);
COMMENT ON TABLE core.organizations IS 'Медучреждения-работодатели, ключ — ИНН.';
COMMENT ON COLUMN core.organizations.oked IS 'ОКЭД вида деятельности; заполнен не у всех.';

CREATE TABLE IF NOT EXISTS core.organization_aliases (
    organization_id bigint NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
    name            text   NOT NULL,
    PRIMARY KEY (organization_id, name)
);
COMMENT ON TABLE core.organization_aliases IS
  'Все встреченные написания названия организации. Нужны для матчинга при следующих выгрузках.';

-- ── Подразделения ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS core.departments (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    organization_id bigint NOT NULL REFERENCES core.organizations(id) ON DELETE CASCADE,
    name            text   NOT NULL,
    UNIQUE (organization_id, name)
);
COMMENT ON TABLE core.departments IS
  'Подразделение внутри организации (поликлиника, отделение). ~1140 значений.';

-- ── География ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS core.regions (
    id      integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name_ru text NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS core.districts (
    id        integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    region_id integer NOT NULL REFERENCES core.regions(id) ON DELETE CASCADE,
    name_ru   text    NOT NULL,
    UNIQUE (region_id, name_ru)
);
COMMENT ON TABLE core.districts IS 'Район/город внутри области. Одноимённые районы в разных областях различаются region_id.';

-- ── Должности ─────────────────────────────────────────────────────────────────
-- position_ru — это классификатор источника (642 значения, крупными группами
-- вроде «Врач-специалист (все специальности, кроме хирурга)»).
-- Настоящая специализация живёт в position_uz на уровне вакансии.
CREATE TABLE IF NOT EXISTS core.positions (
    id       integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title_ru text NOT NULL UNIQUE
);
COMMENT ON TABLE core.positions IS
  'Классификатор должностей источника. Грубый: реальная специализация в vacancies.title_uz.';

-- ── Уровень образования ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS core.education_levels (
    code      text PRIMARY KEY,
    name_ru   text NOT NULL,
    rank      smallint NOT NULL   -- для сравнений «не ниже чем»
);
INSERT INTO core.education_levels (code, name_ru, rank) VALUES
    ('Н/Т',    'Не требуется',                    0),
    ('ССПО',   'Среднее специальное/проф. образование', 1),
    ('В/О',    'Высшее образование',              2),
    ('В/О-NE', 'Высшее (неоконченное/иное)',      2)
ON CONFLICT (code) DO NOTHING;

-- ── Качество зарплаты ─────────────────────────────────────────────────────────
-- Метка из предыдущего этапа обработки: 14 записей помечены как подозрительные
-- (минимум по выгрузке 0.25 сум — очевидно, единицы измерения перепутаны).
CREATE TABLE IF NOT EXISTS core.salary_quality_levels (
    code    text PRIMARY KEY,
    name_ru text NOT NULL
);
INSERT INTO core.salary_quality_levels (code, name_ru) VALUES
    ('ok',        'как указано в источнике'),
    ('suspicious','подозрительно низкая — проверить единицы')
ON CONFLICT (code) DO NOTHING;
