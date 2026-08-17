-- 007_indexes.sql
-- Индексы отдельной миграцией: при перезаливке ETL их удобно дропать и строить заново.

-- ── Ядро: типовые фильтры EA-помощника ───────────────────────────────────────
CREATE INDEX IF NOT EXISTS ix_vacancies_region      ON core.vacancies (region_id);
CREATE INDEX IF NOT EXISTS ix_vacancies_district    ON core.vacancies (district_id);
CREATE INDEX IF NOT EXISTS ix_vacancies_org         ON core.vacancies (organization_id);
CREATE INDEX IF NOT EXISTS ix_vacancies_department  ON core.vacancies (department_id);
CREATE INDEX IF NOT EXISTS ix_vacancies_position    ON core.vacancies (position_id);
CREATE INDEX IF NOT EXISTS ix_vacancies_detail      ON core.vacancies (detail_level);
CREATE INDEX IF NOT EXISTS ix_vacancies_salary      ON core.vacancies (salary DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS ix_vacancies_date_start  ON core.vacancies (date_start DESC);
CREATE INDEX IF NOT EXISTS ix_vacancies_experience  ON core.vacancies (experience_years);
CREATE INDEX IF NOT EXISTS ix_vacancies_languages   ON core.vacancies USING gin (languages);

-- Полнотекст
CREATE INDEX IF NOT EXISTS ix_vacancies_fts
    ON core.vacancies USING gin (search_document);

-- Триграммы: названия должностей приходят в трёх системах письма
-- (латиница uz, кириллица uz, русский) — опечатки и разнописания норма.
CREATE INDEX IF NOT EXISTS ix_vacancies_title_trgm
    ON core.vacancies USING gin (title_uz gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_organizations_name_trgm
    ON core.organizations USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_departments_name_trgm
    ON core.departments USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS ix_positions_title_trgm
    ON core.positions USING gin (title_ru gin_trgm_ops);

-- ── Векторные индексы ─────────────────────────────────────────────────────────
-- HNSW, косинус. Для 3.6k строк можно и без индекса, но масштаб источника —
-- вся база mehnat.uz, поэтому строим сразу правильно.
-- m=16, ef_construction=64 — дефолты pgvector, на таком объёме их менять незачем.
CREATE INDEX IF NOT EXISTS ix_vacancy_embeddings_hnsw
    ON ai.vacancy_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS ix_document_chunks_hnsw
    ON ai.document_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS ix_vacancy_embeddings_model ON ai.vacancy_embeddings (model);
