-- 029_interview_candidate_api.sql
-- Функции для стороны кандидата: бот работает без контекста клиники.
--
-- Почему SECURITY DEFINER, а не RLS-запросы: кандидату нужно пройти по цепочке
-- interviews -> applications -> candidate_profiles -> jobs -> clinics, и каждое
-- звено под своей политикой. Собирать такой запрос в боте значит держать
-- правило видимости в коде. Здесь оно в одном месте: владение проверяется по
-- p_user_id, и обойти проверку из приложения нельзя.
--
-- Состояние интервью НЕ дублируется в памяти бота. Единственный источник
-- правды — база: бот можно перезапустить посреди разговора, и человек
-- продолжит с того же вопроса.

-- ══ Активное интервью кандидата ═══════════════════════════════════════════════
CREATE OR REPLACE FUNCTION product.my_active_interview(p_user_id bigint)
RETURNS TABLE (
    interview_id    uuid,
    job_id          uuid,
    job_title       text,
    clinic_name     text,
    asked_count     smallint,
    answered_count  integer,
    total_questions integer,
    pending_turn_id uuid,
    pending_question text,
    started_at      timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path = product, public STABLE AS $$
    SELECT i.id, j.id, j.title, c.name,
           i.asked_count,
           (SELECT count(*)::int FROM interview_turns t
             WHERE t.interview_id = i.id AND t.answered_at IS NOT NULL),
           (SELECT count(*)::int FROM job_questions q WHERE q.job_id = j.id),
           (SELECT t.id FROM interview_turns t
             WHERE t.interview_id = i.id AND t.answered_at IS NULL
             ORDER BY t.ord DESC LIMIT 1),
           (SELECT t.question_text FROM interview_turns t
             WHERE t.interview_id = i.id AND t.answered_at IS NULL
             ORDER BY t.ord DESC LIMIT 1),
           i.started_at
    FROM interviews i
    JOIN applications a ON a.id = i.application_id
    JOIN candidate_profiles cp ON cp.id = a.candidate_id
    JOIN jobs j ON j.id = a.job_id
    JOIN clinics c ON c.id = j.clinic_id
    WHERE cp.user_id = p_user_id
      AND i.status = 'in_progress'
    ORDER BY i.last_activity_at DESC
    LIMIT 1;
$$;

ALTER FUNCTION product.my_active_interview(bigint) OWNER TO ezgumed;
GRANT EXECUTE ON FUNCTION product.my_active_interview(bigint) TO ishmed_app;

-- ══ Транскрипт своего интервью ════════════════════════════════════════════════
-- Владение проверяется здесь же: идентификатор интервью — uuid, но опираться
-- на его неугадываемость как на защиту нельзя.
CREATE OR REPLACE FUNCTION product.candidate_transcript(
    p_interview_id uuid, p_user_id bigint)
RETURNS TABLE (
    ord           smallint,
    kind          text,
    question_text text,
    answer_kind   text,
    answer_text   text)
LANGUAGE sql SECURITY DEFINER SET search_path = product, public STABLE AS $$
    SELECT t.ord, t.kind, t.question_text, t.answer_kind, t.answer_text
    FROM interview_turns t
    JOIN interviews i ON i.id = t.interview_id
    JOIN applications a ON a.id = i.application_id
    JOIN candidate_profiles cp ON cp.id = a.candidate_id
    WHERE t.interview_id = p_interview_id
      AND cp.user_id = p_user_id
    ORDER BY t.ord;
$$;

ALTER FUNCTION product.candidate_transcript(uuid, bigint) OWNER TO ezgumed;
GRANT EXECUTE ON FUNCTION product.candidate_transcript(uuid, bigint) TO ishmed_app;

-- ══ Вакансия интервью: нужна для промпта саммари ══════════════════════════════
CREATE OR REPLACE FUNCTION product.job_of_interview(p_interview_id uuid)
RETURNS TABLE (
    job_id        uuid,
    title         text,
    specialty     text,
    role_category text,
    experience_min_months integer,
    required_skills text[],
    source_text   text)
LANGUAGE sql SECURITY DEFINER SET search_path = product, public STABLE AS $$
    SELECT j.id, j.title, j.specialty, j.role_category,
           j.experience_min_months, j.required_skills, j.source_text
    FROM interviews i
    JOIN applications a ON a.id = i.application_id
    JOIN jobs j ON j.id = a.job_id
    WHERE i.id = p_interview_id;
$$;

ALTER FUNCTION product.job_of_interview(uuid) OWNER TO ezgumed;
GRANT EXECUTE ON FUNCTION product.job_of_interview(uuid) TO ishmed_app;

-- ══ Сколько уточнений уже задано к текущему вопросу ═══════════════════════════
-- Уточнение допускается одно на вопрос плана. Считает база, потому что это
-- предохранитель от зацикливания модели, а не деталь интерфейса.
CREATE OR REPLACE FUNCTION product.follow_ups_after_last_question(p_interview_id uuid)
RETURNS integer LANGUAGE sql SECURITY DEFINER
SET search_path = product, public STABLE AS $$
    SELECT count(*)::int
    FROM interview_turns t
    WHERE t.interview_id = p_interview_id
      AND t.kind = 'follow_up'
      AND t.ord > coalesce((
          SELECT max(q.ord) FROM interview_turns q
          WHERE q.interview_id = p_interview_id AND q.kind = 'question'), 0);
$$;

ALTER FUNCTION product.follow_ups_after_last_question(uuid) OWNER TO ezgumed;
GRANT EXECUTE ON FUNCTION product.follow_ups_after_last_question(uuid) TO ishmed_app;

-- ══ Отклики кандидата: список для меню бота ═══════════════════════════════════
CREATE OR REPLACE FUNCTION product.my_applications(p_user_id bigint)
RETURNS TABLE (
    application_id uuid,
    job_id         uuid,
    public_code    text,
    job_title      text,
    clinic_name    text,
    app_status     application_status,
    applied_at     timestamptz,
    interview_status interview_status,
    answered_count integer,
    total_questions integer)
LANGUAGE sql SECURITY DEFINER SET search_path = product, public STABLE AS $$
    SELECT a.id, j.id, j.public_code, j.title, c.name,
           a.status, a.applied_at, i.status,
           (SELECT count(*)::int FROM interview_turns t
             WHERE t.interview_id = i.id AND t.answered_at IS NOT NULL),
           (SELECT count(*)::int FROM job_questions q WHERE q.job_id = j.id)
    FROM applications a
    JOIN candidate_profiles cp ON cp.id = a.candidate_id
    JOIN jobs j ON j.id = a.job_id
    JOIN clinics c ON c.id = j.clinic_id
    LEFT JOIN interviews i ON i.application_id = a.id
    WHERE cp.user_id = p_user_id
    ORDER BY a.applied_at DESC
    LIMIT 30;
$$;

ALTER FUNCTION product.my_applications(bigint) OWNER TO ezgumed;
GRANT EXECUTE ON FUNCTION product.my_applications(bigint) TO ishmed_app;

-- ══ Вакансия по идентификатору для витрины ════════════════════════════════════
-- Дополнение к get_published_job(code): в списке у нас uuid, а не код.
CREATE OR REPLACE FUNCTION product.get_published_job_by_id(p_job_id uuid)
RETURNS TABLE (
    job_id        uuid,
    public_code   text,
    title         text,
    clinic_name   text,
    role_category text,
    specialty     text,
    specialty_name text,
    city          text,
    districts     text[],
    schedule      text[],
    salary_min_uzs numeric,
    salary_max_uzs numeric,
    experience_min_months integer,
    required_skills text[],
    required_languages text[],
    credential_requirements text[],
    interview_intro text,
    questions_count integer,
    published_at  timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path = product, public STABLE AS $$
    SELECT j.id, j.public_code, j.title, c.name,
           j.role_category, j.specialty, s.name_ru, j.city, j.districts, j.schedule,
           j.salary_min_uzs, j.salary_max_uzs, j.experience_min_months,
           j.required_skills, j.required_languages, j.credential_requirements,
           j.interview_intro,
           (SELECT count(*)::int FROM job_questions q WHERE q.job_id = j.id),
           j.published_at
    FROM jobs j
    JOIN clinics c ON c.id = j.clinic_id
    LEFT JOIN specialties s ON s.code = j.specialty
    WHERE j.id = p_job_id
      AND j.status = 'active'
      AND j.interview_plan_status = 'approved';
$$;

ALTER FUNCTION product.get_published_job_by_id(uuid) OWNER TO ezgumed;
GRANT EXECUTE ON FUNCTION product.get_published_job_by_id(uuid) TO ishmed_app;
