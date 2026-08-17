-- 003_raw.sql
-- Приёмник CSV выгрузки mehnat.uz (medical_vacancies_uzbekistan.csv).
-- Порядок колонок ЗЕРКАЛИТ порядок в файле — это позволяет грузить
-- обычным \copy без указания списка колонок и без предобработки.
-- Все типы text: задача слоя — принять файл, а не спорить с ним.

CREATE TABLE IF NOT EXISTS raw.vacancies_csv (
    external_id        text,  -- id
    position_ru        text,  -- должность_ru        (справочный классификатор)
    position_uz        text,  -- должность_uz        (живой текст вакансии)
    organization       text,  -- организация
    inn                text,  -- инн
    oked               text,  -- oked
    department         text,  -- подразделение
    salary_raw         text,  -- зарплата_как_в_api
    salary_quality     text,  -- качество_зарплаты
    rate               text,  -- ставка
    duties             text,  -- обязанности
    requirements       text,  -- требования
    conditions         text,  -- условия
    education          text,  -- образование
    experience_years   text,  -- опыт_лет
    foreign_languages  text,  -- иностранные_языки  (разделитель '; ')
    benefits           text,  -- льготы             (разделитель '\n')
    region_ru          text,  -- регион_ru
    district_ru        text,  -- район_ru
    vacancy_address    text,  -- адрес_вакансии     (в текущей выгрузке пуст во всех строках)
    actual_address     text,  -- фактический_адрес
    phones             text,  -- телефоны
    email              text,  -- email
    date_start         text,  -- дата_начала
    date_end           text,  -- дата_окончания     ('9999-01-01' = бессрочно)
    is_urgent          text,  -- срочно             (да/нет, см. коммент ниже)
    views              text,  -- просмотры
    applications       text,  -- отклики
    api_url            text,  -- ссылка_api
    completeness       text,  -- полнота_данных     (константа во всей выгрузке)
    -- служебные поля партии загрузки
    batch_id           uuid        NOT NULL DEFAULT gen_random_uuid(),
    source_file        text        NOT NULL DEFAULT 'medical_vacancies_uzbekistan.csv',
    loaded_at          timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE raw.vacancies_csv IS
  'Сырой CSV с ishapi.mehnat.uz. Порядок колонок совпадает с файлом (30 полей).';
COMMENT ON COLUMN raw.vacancies_csv.is_urgent IS
  'ВНИМАНИЕ: значение да/нет в выгрузке совпадает 1:1 с полнотой записи '
  '(да -> есть регион/обязанности/oked, нет -> эти поля пусты). Это признак '
  'источника/эндпоинта, а не бизнес-срочность. В core вынесено в detail_level.';

CREATE INDEX IF NOT EXISTS ix_raw_vacancies_csv_batch
    ON raw.vacancies_csv (batch_id);
CREATE INDEX IF NOT EXISTS ix_raw_vacancies_csv_external_id
    ON raw.vacancies_csv (external_id);

-- Журнал загрузок: чтобы знать, какая партия что принесла
CREATE TABLE IF NOT EXISTS raw.load_batches (
    batch_id     uuid PRIMARY KEY,
    source_file  text        NOT NULL,
    rows_loaded  integer     NOT NULL,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    note         text
);
