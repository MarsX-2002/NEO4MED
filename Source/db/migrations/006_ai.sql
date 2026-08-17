-- 006_ai.sql
-- Векторный слой для EA-помощника.
-- Провайдер: Azure OpenAI (аккаунт ezgumed-ai-swc, swedencentral).
-- Размерность 1536 подтверждена живым вызовом text-embedding-3-small.

-- ── Реестр моделей ────────────────────────────────────────────────────────────
-- Модель фиксируем в данных, а не в коде: когда сменим на 3-large (3072),
-- старые векторы останутся валидными и сравнимыми между собой.
CREATE TABLE IF NOT EXISTS ai.embedding_models (
    model       text PRIMARY KEY,   -- логическое имя = имя деплоймента в Azure
    provider    text NOT NULL,
    dim         integer NOT NULL,
    deployment  text NOT NULL,
    is_default  boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_embedding_models_default
    ON ai.embedding_models ((true)) WHERE is_default;   -- дефолт может быть только один

INSERT INTO ai.embedding_models (model, provider, dim, deployment, is_default) VALUES
    ('text-embedding-3-small', 'azure-openai', 1536, 'text-embedding-3-small', true)
ON CONFLICT (model) DO NOTHING;

-- ── Эмбеддинги вакансий ───────────────────────────────────────────────────────
-- Отдельная таблица, а не колонка в core.vacancies. Причины:
--   1. переэмбеддинг не блокирует и не раздувает ядро
--   2. две модели могут жить рядом во время миграции
--   3. content_hash позволяет пересчитывать только реально изменившиеся строки
CREATE TABLE IF NOT EXISTS ai.vacancy_embeddings (
    vacancy_id   bigint NOT NULL REFERENCES core.vacancies(external_id) ON DELETE CASCADE,
    model        text   NOT NULL REFERENCES ai.embedding_models(model),
    content_hash text   NOT NULL,          -- md5 текста, из которого построен вектор
    embedding    vector(1536) NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (vacancy_id, model)
);
COMMENT ON COLUMN ai.vacancy_embeddings.content_hash IS
  'md5 от ai.vacancy_embed_text(). Не совпал с текущим — вектор устарел, нужен пересчёт.';

-- Текст, который уходит в модель. Держим в функции, чтобы хэш и сам вектор
-- всегда строились из одного и того же определения.
CREATE OR REPLACE FUNCTION ai.vacancy_embed_text(p_vacancy_id bigint)
RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT concat_ws(E'\n',
        'Должность: '     || coalesce(v.title_uz, ''),
        'Категория: '     || p.title_ru,
        'Организация: '   || o.name,
        'Подразделение: ' || d.name,
        'Регион: '        || concat_ws(', ', r.name_ru, dis.name_ru),
        'Зарплата: '      || to_char(v.salary, 'FM999999999') || ' сум',
        'Образование: '   || el.name_ru,
        'Опыт, лет: '     || v.experience_years,
        'Языки: '         || array_to_string(v.languages, ', '),
        'Обязанности: '   || v.duties,
        'Требования: '    || v.requirements,
        'Условия: '       || v.conditions,
        'Льготы: '        || v.benefits
    )
    FROM core.vacancies v
    JOIN core.organizations o        ON o.id = v.organization_id
    LEFT JOIN core.positions p       ON p.id = v.position_id
    LEFT JOIN core.departments d     ON d.id = v.department_id
    LEFT JOIN core.regions r         ON r.id = v.region_id
    LEFT JOIN core.districts dis     ON dis.id = v.district_id
    LEFT JOIN core.education_levels el ON el.code = v.education_code
    WHERE v.external_id = p_vacancy_id;
$$;
COMMENT ON FUNCTION ai.vacancy_embed_text IS
  'Канонический текст вакансии для эмбеддинга. concat_ws выбрасывает NULL-строки, '
  'поэтому basic-записи не получают пустых «Обязанности:» заголовков.';

-- Очередь на (пере)эмбеддинг: нет вектора или хэш разошёлся
CREATE OR REPLACE VIEW ai.v_embed_queue AS
SELECT v.external_id            AS vacancy_id,
       m.model,
       ai.vacancy_embed_text(v.external_id) AS content,
       md5(ai.vacancy_embed_text(v.external_id)) AS content_hash
FROM core.vacancies v
CROSS JOIN ai.embedding_models m
LEFT JOIN ai.vacancy_embeddings e
       ON e.vacancy_id = v.external_id AND e.model = m.model
WHERE m.is_default
  AND (e.vacancy_id IS NULL
       OR e.content_hash <> md5(ai.vacancy_embed_text(v.external_id)));

-- ── Документы EA-помощника ────────────────────────────────────────────────────
-- Всё, что не вакансия: research-файлы, регламенты, переписка.
CREATE TABLE IF NOT EXISTS ai.documents (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind        text NOT NULL,               -- 'research' | 'policy' | 'note' | ...
    source_ref  text NOT NULL,               -- путь к файлу или URL
    title       text,
    content_sha text NOT NULL,               -- дедуп по содержимому файла
    meta        jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_ref, content_sha)
);

CREATE TABLE IF NOT EXISTS ai.document_chunks (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id  bigint NOT NULL REFERENCES ai.documents(id) ON DELETE CASCADE,
    chunk_no     integer NOT NULL,
    content      text NOT NULL,
    tokens       integer,
    model        text NOT NULL REFERENCES ai.embedding_models(model),
    embedding    vector(1536) NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_no, model)
);

-- ── Журнал обращений к моделям ────────────────────────────────────────────────
-- Деньги считаются в панели Azure, но локальный журнал нужен, чтобы понимать
-- ЧТО именно их сожгло: какой запрос, какой деплоймент, сколько токенов.
CREATE TABLE IF NOT EXISTS ai.model_calls (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deployment        text NOT NULL,
    purpose           text NOT NULL,          -- 'embed_vacancies' | 'ea_chat' | ...
    prompt_tokens     integer,
    completion_tokens integer,
    latency_ms        integer,
    http_status       smallint,
    error             text,
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_model_calls_created ON ai.model_calls (created_at DESC);
