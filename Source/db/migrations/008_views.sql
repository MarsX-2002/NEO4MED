-- 008_views.sql
-- Плоское представление и гибридный поиск — то, чем будет пользоваться EA-помощник.

CREATE OR REPLACE VIEW core.v_vacancies AS
SELECT v.external_id,
       v.detail_level,
       v.title_uz,
       p.title_ru        AS position_category,
       o.inn             AS organization_inn,
       o.name            AS organization,
       o.oked,
       d.name            AS department,
       r.name_ru         AS region,
       dis.name_ru       AS district,
       v.address,
       v.salary,
       v.salary_quality,
       v.rate,
       v.education_code,
       el.name_ru        AS education,
       v.experience_years,
       v.languages,
       v.duties,
       v.requirements,
       v.conditions,
       v.benefits,
       v.phones,
       v.email,
       v.date_start,
       v.date_end,
       (v.date_end IS NULL) AS is_open_ended,
       v.is_urgent,
       v.views,
       v.applications,
       v.api_url
FROM core.vacancies v
JOIN core.organizations o          ON o.id = v.organization_id
LEFT JOIN core.positions p        ON p.id = v.position_id
LEFT JOIN core.departments d      ON d.id = v.department_id
LEFT JOIN core.regions r          ON r.id = v.region_id
LEFT JOIN core.districts dis      ON dis.id = v.district_id
LEFT JOIN core.education_levels el ON el.code = v.education_code;

COMMENT ON VIEW core.v_vacancies IS
  'Денормализованная вакансия одной строкой. Помни про detail_level: у basic-записей '
  'region/duties/requirements пусты по природе источника, это не потеря данных.';

-- ── Гибридный поиск: вектор + полнотекст, слияние по RRF ──────────────────────
-- Чистый вектор на таких коротких формулировках («Akusher ginekolog vrach»)
-- регулярно промахивается, чистый FTS не переживает разнописание.
-- Reciprocal Rank Fusion, k=60 — стандартная константа, устойчива без тюнинга.
CREATE OR REPLACE FUNCTION ai.search_vacancies(
    p_query_embedding vector(1536),
    p_query_text      text    DEFAULT NULL,
    p_limit           integer DEFAULT 20,
    p_region          text    DEFAULT NULL,
    p_min_salary      numeric DEFAULT NULL,
    p_model           text    DEFAULT 'text-embedding-3-small'
)
RETURNS TABLE (
    external_id  bigint,
    title_uz     text,
    organization text,
    region       text,
    salary       numeric,
    vec_rank     integer,
    fts_rank     integer,
    rrf_score    double precision
)
LANGUAGE sql STABLE AS $$
WITH filtered AS (
    SELECT v.external_id
    FROM core.vacancies v
    LEFT JOIN core.regions r ON r.id = v.region_id
    WHERE (p_region     IS NULL OR r.name_ru = p_region)
      AND (p_min_salary IS NULL OR v.salary >= p_min_salary)
),
vec AS (
    SELECT e.vacancy_id,
           row_number() OVER (ORDER BY e.embedding <=> p_query_embedding) AS rnk
    FROM ai.vacancy_embeddings e
    JOIN filtered f ON f.external_id = e.vacancy_id
    WHERE e.model = p_model
    ORDER BY e.embedding <=> p_query_embedding
    LIMIT p_limit * 5
),
fts AS (
    SELECT v.external_id AS vacancy_id,
           row_number() OVER (
               ORDER BY ts_rank(v.search_document,
                                websearch_to_tsquery('simple', p_query_text)) DESC
           ) AS rnk
    FROM core.vacancies v
    JOIN filtered f ON f.external_id = v.external_id
    WHERE p_query_text IS NOT NULL
      AND v.search_document @@ websearch_to_tsquery('simple', p_query_text)
    LIMIT p_limit * 5
)
SELECT vv.external_id,
       vv.title_uz,
       vv.organization,
       vv.region,
       vv.salary,
       vec.rnk::integer,
       fts.rnk::integer,
       coalesce(1.0 / (60 + vec.rnk), 0) + coalesce(1.0 / (60 + fts.rnk), 0) AS rrf_score
FROM core.v_vacancies vv
LEFT JOIN vec ON vec.vacancy_id = vv.external_id
LEFT JOIN fts ON fts.vacancy_id = vv.external_id
WHERE vec.vacancy_id IS NOT NULL OR fts.vacancy_id IS NOT NULL
ORDER BY rrf_score DESC
LIMIT p_limit;
$$;

COMMENT ON FUNCTION ai.search_vacancies IS
  'Гибридный поиск вакансий. Вектор считает Azure text-embedding-3-small, слияние RRF k=60.';
