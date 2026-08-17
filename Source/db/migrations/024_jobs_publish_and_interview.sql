-- 024_jobs_publish_and_interview.sql
-- Публикация вакансии клиникой, план вопросов с одобрением менеджера
-- и авто-интервью кандидата.
--
-- Смысл публикации — авто-интервью: клиника пишет, кого ищет, получает
-- deep link в бота, кандидат откликается и проходит собеседование.
--
-- Что вынесено в базу, а не оставлено в коде бота:
--   1. кандидат видит только опубликованные вакансии и только те поля,
--      которые ему положено видеть (без source_text, extraction, created_by);
--   2. интервью запускается только по одобренному менеджером плану вопросов;
--   3. одно интервью на кандидата и вакансию, переиграть нельзя;
--   4. основные вопросы выдаются из плана по порядку — модель их не сочиняет;
--   5. предел ходов считает база, а не только граф.

-- ══ 1. Генератор публичных кодов ══════════════════════════════════════════════
-- Тот же алфавит без похожих символов, что у slug для QR: код диктуют голосом
-- и набирают руками, «l» против «1» тут дороже лишнего символа.
CREATE OR REPLACE FUNCTION product.gen_public_code(p_len int DEFAULT 10)
RETURNS text LANGUAGE plpgsql AS $$
DECLARE
    alphabet constant text := '23456789abcdefghjkmnpqrstuvwxyz';
    result text := '';
BEGIN
    FOR _ IN 1..p_len LOOP
        result := result || substr(alphabet, 1 + floor(random() * length(alphabet))::int, 1);
    END LOOP;
    RETURN result;
END $$;

ALTER FUNCTION product.gen_public_code(int) OWNER TO ezgumed;

-- ══ 2. Вакансия: публичный код, публикация, состояние плана ═══════════════════
ALTER TABLE product.jobs
    ADD COLUMN public_code text NOT NULL DEFAULT product.gen_public_code(10),
    ADD COLUMN published_at timestamptz,
    ADD COLUMN closed_at timestamptz,
    ADD COLUMN interview_plan_status text NOT NULL DEFAULT 'none',
    ADD COLUMN interview_intro text,
    ADD COLUMN applications_count integer NOT NULL DEFAULT 0;

ALTER TABLE product.jobs
    ADD CONSTRAINT jobs_interview_plan_status_chk
        CHECK (interview_plan_status IN ('none', 'draft', 'approved')),
    ADD CONSTRAINT jobs_public_code_chk
        CHECK (public_code ~ '^[23456789abcdefghjkmnpqrstuvwxyz]{10}$');

CREATE UNIQUE INDEX jobs_public_code_uq ON product.jobs (public_code);
CREATE INDEX jobs_active_idx ON product.jobs (published_at DESC)
    WHERE status = 'active';

COMMENT ON COLUMN product.jobs.public_code IS
  'Код для deep link t.me/ishmedbot?start=job_<код>. Публичный, но неугадываемый.';
COMMENT ON COLUMN product.jobs.interview_plan_status IS
  'none — вопросов нет, draft — модель предложила, approved — менеджер одобрил. '
  'Публикация возможна только при approved.';

-- ══ 3. План вопросов ══════════════════════════════════════════════════════════
-- Вопросы фиксируются до публикации. Если модель будет придумывать их на ходу,
-- двух кандидатов нельзя сравнить между собой — они отвечали на разное.
CREATE TABLE product.job_questions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id      uuid NOT NULL REFERENCES product.jobs (id) ON DELETE CASCADE,
    ord         smallint NOT NULL CHECK (ord BETWEEN 1 AND 12),
    question    text NOT NULL CHECK (length(btrim(question)) BETWEEN 5 AND 400),
    intent      text,
    origin      text NOT NULL DEFAULT 'ai' CHECK (origin IN ('ai', 'manual')),
    edited      boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (job_id, ord)
);

COMMENT ON TABLE product.job_questions IS
  'Одобренный менеджером план интервью. Порядок фиксирован, чтобы кандидатов '
  'можно было сравнивать.';
COMMENT ON COLUMN product.job_questions.intent IS
  'Что проверяем: опыт, график, оплата, навык, документы. Для группировки в отчёте.';
COMMENT ON COLUMN product.job_questions.edited IS
  'Менеджер правил формулировку модели. Полезно знать, насколько модели доверяют.';

CREATE TRIGGER tr_job_questions_touch BEFORE UPDATE ON product.job_questions
    FOR EACH ROW EXECUTE FUNCTION product.tg_touch_updated_at();

-- ══ 4. Интервью ═══════════════════════════════════════════════════════════════
CREATE TYPE interview_status AS ENUM ('in_progress', 'completed', 'abandoned');

CREATE TABLE product.interviews (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- одно интервью на отклик, а отклик уникален по (job_id, candidate_id):
    -- отсюда «одно интервью на вакансию для кандидата» без отдельной проверки
    application_id  uuid NOT NULL UNIQUE
                    REFERENCES product.applications (id) ON DELETE CASCADE,
    status          interview_status NOT NULL DEFAULT 'in_progress',
    asked_count     smallint NOT NULL DEFAULT 0,
    turn_budget     smallint NOT NULL DEFAULT 16 CHECK (turn_budget BETWEEN 4 AND 40),
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    last_activity_at timestamptz NOT NULL DEFAULT now(),
    -- итог для клиники
    summary         text,
    extraction      jsonb,
    gaps            text[] NOT NULL DEFAULT '{}',
    follow_ups      text[] NOT NULL DEFAULT '{}',
    voice_answers   smallint NOT NULL DEFAULT 0,
    CONSTRAINT interviews_finished_chk
        CHECK ((status = 'in_progress') = (finished_at IS NULL))
);

COMMENT ON TABLE product.interviews IS
  'Авто-интервью по одобренному плану вакансии. Не выносит решения о найме: '
  'спрашивает, расшифровывает и структурирует, решает человек.';
COMMENT ON COLUMN product.interviews.turn_budget IS
  'Жёсткий предел ходов. Диалог обязан заканчиваться, даже если кандидат '
  'отвечает не по делу.';
COMMENT ON COLUMN product.interviews.gaps IS
  'О чём кандидат не сказал. Не минус кандидату, а подсказка клинике, что спросить.';
COMMENT ON COLUMN product.interviews.follow_ups IS
  'Что стоит уточнить лично. Интервью не решает, оно готовит человека к разговору.';

CREATE INDEX interviews_application_idx ON product.interviews (application_id);
CREATE INDEX interviews_status_idx ON product.interviews (status, last_activity_at);

-- ══ 5. Ходы разговора ═════════════════════════════════════════════════════════
-- Строками, а не одним jsonb: клиника читает ход разговора, и его удобнее
-- отдавать постранично и связывать ответ с вопросом плана.
CREATE TABLE product.interview_turns (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    interview_id  uuid NOT NULL REFERENCES product.interviews (id) ON DELETE CASCADE,
    ord           smallint NOT NULL CHECK (ord BETWEEN 1 AND 40),
    kind          text NOT NULL DEFAULT 'question'
                  CHECK (kind IN ('question', 'follow_up')),
    question_id   uuid REFERENCES product.job_questions (id) ON DELETE SET NULL,
    question_text text NOT NULL,
    asked_at      timestamptz NOT NULL DEFAULT now(),
    answer_kind   text CHECK (answer_kind IN ('text', 'voice', 'button', 'skipped')),
    answer_text   text,
    voice_file_id text,
    voice_seconds smallint,
    answered_at   timestamptz,
    UNIQUE (interview_id, ord)
);

COMMENT ON TABLE product.interview_turns IS
  'Полный транскрипт: вопрос, ответ, ссылка на голосовое и его расшифровка. '
  'Клиника видит и саммари, и весь разговор.';
COMMENT ON COLUMN product.interview_turns.answer_text IS
  'Для голосового ответа здесь лежит расшифровка. Оригинал остаётся в Telegram '
  'по voice_file_id — мы не храним аудио у себя.';
COMMENT ON COLUMN product.interview_turns.kind IS
  'question — вопрос из одобренного плана, follow_up — одно уточнение к нему.';

CREATE INDEX interview_turns_interview_idx
    ON product.interview_turns (interview_id, ord);

-- ══ 6. RLS ════════════════════════════════════════════════════════════════════
ALTER TABLE product.job_questions   ENABLE ROW LEVEL SECURITY;
ALTER TABLE product.interviews      ENABLE ROW LEVEL SECURITY;
ALTER TABLE product.interview_turns ENABLE ROW LEVEL SECURITY;
ALTER TABLE product.job_questions   FORCE ROW LEVEL SECURITY;
ALTER TABLE product.interviews      FORCE ROW LEVEL SECURITY;
ALTER TABLE product.interview_turns FORCE ROW LEVEL SECURITY;

-- План вопросов правит только менеджер своей клиники.
CREATE POLICY p_job_questions_manager ON product.job_questions
    USING (EXISTS (SELECT 1 FROM product.jobs j
                   WHERE j.id = job_id
                     AND j.clinic_id = product.current_clinic_id()
                     AND product.is_manager()))
    WITH CHECK (EXISTS (SELECT 1 FROM product.jobs j
                        WHERE j.id = job_id
                          AND j.clinic_id = product.current_clinic_id()
                          AND product.is_manager()));

-- Интервью видит клиника-владелец вакансии и сам кандидат.
CREATE POLICY p_interviews_both_sides ON product.interviews
    USING (EXISTS (
        SELECT 1 FROM product.applications a
        JOIN product.jobs j ON j.id = a.job_id
        WHERE a.id = application_id
          AND j.clinic_id = product.current_clinic_id()
          AND product.is_manager())
      OR EXISTS (
        SELECT 1 FROM product.applications a
        JOIN product.candidate_profiles c ON c.id = a.candidate_id
        WHERE a.id = application_id
          AND c.user_id = product.current_user_id()));

CREATE POLICY p_interview_turns_both_sides ON product.interview_turns
    USING (EXISTS (
        SELECT 1 FROM product.interviews i
        JOIN product.applications a ON a.id = i.application_id
        JOIN product.jobs j ON j.id = a.job_id
        WHERE i.id = interview_id
          AND j.clinic_id = product.current_clinic_id()
          AND product.is_manager())
      OR EXISTS (
        SELECT 1 FROM product.interviews i
        JOIN product.applications a ON a.id = i.application_id
        JOIN product.candidate_profiles c ON c.id = a.candidate_id
        WHERE i.id = interview_id
          AND c.user_id = product.current_user_id()));

-- ══ 7. Публикация вакансии ════════════════════════════════════════════════════
-- Публикация без одобренного плана запрещена: иначе кандидат откликнется,
-- начнёт интервью, а спрашивать будет нечего.
CREATE OR REPLACE FUNCTION product.publish_job(p_job_id uuid)
RETURNS TABLE (job_id uuid, public_code text, questions_count integer)
LANGUAGE plpgsql SECURITY INVOKER SET search_path = product, public AS $$
DECLARE
    v_plan text;
    v_count integer;
    v_status job_status;
BEGIN
    SELECT interview_plan_status, status INTO v_plan, v_status
    FROM jobs WHERE id = p_job_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'вакансия не найдена или недоступна' USING ERRCODE = 'no_data_found';
    END IF;

    SELECT count(*) INTO v_count FROM job_questions WHERE job_questions.job_id = p_job_id;

    IF v_plan <> 'approved' THEN
        RAISE EXCEPTION 'план интервью не одобрен менеджером'
            USING ERRCODE = 'check_violation';
    END IF;

    IF v_count < 3 THEN
        RAISE EXCEPTION 'в плане интервью меньше трёх вопросов'
            USING ERRCODE = 'check_violation';
    END IF;

    UPDATE jobs SET status = 'active',
                    published_at = coalesce(published_at, now()),
                    closed_at = NULL
    WHERE id = p_job_id;

    RETURN QUERY SELECT p_job_id, j.public_code, v_count FROM jobs j WHERE j.id = p_job_id;
END $$;

ALTER FUNCTION product.publish_job(uuid) OWNER TO ezgumed;

CREATE OR REPLACE FUNCTION product.close_job(p_job_id uuid)
RETURNS void LANGUAGE sql SECURITY INVOKER SET search_path = product, public AS $$
    UPDATE jobs SET status = 'closed', closed_at = now() WHERE id = p_job_id;
$$;

ALTER FUNCTION product.close_job(uuid) OWNER TO ezgumed;

-- ══ 8. Витрина для кандидата ══════════════════════════════════════════════════
-- SECURITY DEFINER: бот работает без контекста клиники, а показывать кандидату
-- нужно вакансии всех клиник. Набор колонок закрытый — source_text, extraction
-- и created_by не выходят наружу даже если кто-то забудет их отфильтровать в коде.
CREATE OR REPLACE FUNCTION product.list_published_jobs(
    p_role_category text DEFAULT NULL,
    p_city          text DEFAULT NULL,
    p_limit         int  DEFAULT 20,
    p_offset        int  DEFAULT 0)
RETURNS TABLE (
    job_id        uuid,
    public_code   text,
    title         text,
    clinic_name   text,
    role_category text,
    specialty     text,
    city          text,
    districts     text[],
    schedule      text[],
    salary_min_uzs numeric,
    salary_max_uzs numeric,
    experience_min_months integer,
    required_skills text[],
    published_at  timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path = product, public STABLE AS $$
    SELECT j.id, j.public_code, j.title, c.name,
           j.role_category, j.specialty, j.city, j.districts, j.schedule,
           j.salary_min_uzs, j.salary_max_uzs, j.experience_min_months,
           j.required_skills, j.published_at
    FROM jobs j
    JOIN clinics c ON c.id = j.clinic_id
    WHERE j.status = 'active'
      AND j.interview_plan_status = 'approved'
      AND (p_role_category IS NULL OR j.role_category = p_role_category)
      AND (p_city IS NULL OR j.city = p_city)
    ORDER BY j.published_at DESC NULLS LAST
    LIMIT least(greatest(p_limit, 1), 50) OFFSET greatest(p_offset, 0);
$$;

ALTER FUNCTION product.list_published_jobs(text, text, int, int) OWNER TO ezgumed;

CREATE OR REPLACE FUNCTION product.get_published_job(p_code text)
RETURNS TABLE (
    job_id        uuid,
    public_code   text,
    title         text,
    clinic_name   text,
    role_category text,
    specialty     text,
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
           j.role_category, j.specialty, j.city, j.districts, j.schedule,
           j.salary_min_uzs, j.salary_max_uzs, j.experience_min_months,
           j.required_skills, j.required_languages, j.credential_requirements,
           j.interview_intro,
           (SELECT count(*)::int FROM job_questions q WHERE q.job_id = j.id),
           j.published_at
    FROM jobs j
    JOIN clinics c ON c.id = j.clinic_id
    WHERE j.public_code = lower(btrim(p_code))
      AND j.status = 'active'
      AND j.interview_plan_status = 'approved';
$$;

ALTER FUNCTION product.get_published_job(text) OWNER TO ezgumed;

-- ══ 9. Отклик и запуск интервью ═══════════════════════════════════════════════
-- Здесь живёт правило «переиграть нельзя»: повторный вызов возвращает
-- существующее интервью с его состоянием, а не начинает новое.
CREATE OR REPLACE FUNCTION product.open_interview(
    p_user_id bigint,
    p_job_id  uuid,
    p_message text DEFAULT NULL)
RETURNS TABLE (
    interview_id   uuid,
    application_id uuid,
    status         interview_status,
    asked_count    smallint,
    is_new         boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = product, public AS $$
DECLARE
    v_candidate uuid;
    v_app       uuid;
    v_interview uuid;
    v_status    interview_status;
    v_asked     smallint;
    v_new       boolean := false;
    v_plan      text;
    v_jstatus   job_status;
BEGIN
    SELECT interview_plan_status, status INTO v_plan, v_jstatus FROM jobs WHERE id = p_job_id;
    IF NOT FOUND OR v_jstatus <> 'active' OR v_plan <> 'approved' THEN
        RAISE EXCEPTION 'вакансия не опубликована или у неё нет одобренного плана интервью'
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT id INTO v_candidate FROM candidate_profiles WHERE user_id = p_user_id;
    IF v_candidate IS NULL THEN
        INSERT INTO candidate_profiles (user_id, role_category, source, status)
        SELECT p_user_id, j.role_category, 'text', 'draft' FROM jobs j WHERE j.id = p_job_id
        RETURNING id INTO v_candidate;
    END IF;

    SELECT id INTO v_app FROM applications
    WHERE job_id = p_job_id AND candidate_id = v_candidate;

    IF v_app IS NULL THEN
        INSERT INTO applications (job_id, candidate_id, message)
        VALUES (p_job_id, v_candidate, p_message)
        RETURNING id INTO v_app;
        UPDATE jobs SET applications_count = applications_count + 1 WHERE id = p_job_id;
    END IF;

    SELECT i.id, i.status, i.asked_count INTO v_interview, v_status, v_asked
    FROM interviews i WHERE i.application_id = v_app;

    IF v_interview IS NULL THEN
        INSERT INTO interviews (application_id) VALUES (v_app)
        RETURNING id, interviews.status, interviews.asked_count
        INTO v_interview, v_status, v_asked;
        v_new := true;
    END IF;

    RETURN QUERY SELECT v_interview, v_app, v_status, v_asked, v_new;
END $$;

ALTER FUNCTION product.open_interview(bigint, uuid, text) OWNER TO ezgumed;

-- Следующий вопрос берётся из одобренного плана по порядку. Модель не решает,
-- что спросить дальше, и не может пропустить или переставить вопросы.
CREATE OR REPLACE FUNCTION product.next_interview_question(p_interview_id uuid)
RETURNS TABLE (
    question_id   uuid,
    ord           smallint,
    question      text,
    intent        text,
    total         integer,
    turns_left    integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = product, public STABLE AS $$
DECLARE
    v_job uuid;
    v_budget smallint;
    v_used smallint;
BEGIN
    SELECT a.job_id, i.turn_budget, i.asked_count
    INTO v_job, v_budget, v_used
    FROM interviews i JOIN applications a ON a.id = i.application_id
    WHERE i.id = p_interview_id AND i.status = 'in_progress';

    IF NOT FOUND OR v_used >= v_budget THEN
        RETURN;
    END IF;

    RETURN QUERY
    SELECT q.id, q.ord, q.question, q.intent,
           (SELECT count(*)::int FROM job_questions x WHERE x.job_id = v_job),
           (v_budget - v_used)::int
    FROM job_questions q
    WHERE q.job_id = v_job
      AND NOT EXISTS (
          SELECT 1 FROM interview_turns t
          WHERE t.interview_id = p_interview_id
            AND t.question_id = q.id
            AND t.kind = 'question')
    ORDER BY q.ord
    LIMIT 1;
END $$;

ALTER FUNCTION product.next_interview_question(uuid) OWNER TO ezgumed;

CREATE OR REPLACE FUNCTION product.ask_interview_turn(
    p_interview_id uuid,
    p_question_id  uuid,
    p_question_text text,
    p_kind         text DEFAULT 'question')
RETURNS TABLE (turn_id uuid, ord smallint) LANGUAGE plpgsql
SECURITY DEFINER SET search_path = product, public AS $$
DECLARE
    v_ord smallint;
    v_turn uuid;
    v_budget smallint;
    v_used smallint;
BEGIN
    SELECT turn_budget, asked_count INTO v_budget, v_used
    FROM interviews WHERE id = p_interview_id AND status = 'in_progress'
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'интервью не найдено или уже закончено'
            USING ERRCODE = 'check_violation';
    END IF;

    IF v_used >= v_budget THEN
        RAISE EXCEPTION 'исчерпан предел ходов интервью'
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT coalesce(max(t.ord), 0)::smallint + 1 INTO v_ord
    FROM interview_turns t WHERE t.interview_id = p_interview_id;

    INSERT INTO interview_turns (interview_id, ord, kind, question_id, question_text)
    VALUES (p_interview_id, v_ord, p_kind, p_question_id, p_question_text)
    RETURNING id INTO v_turn;

    UPDATE interviews SET asked_count = asked_count + 1, last_activity_at = now()
    WHERE id = p_interview_id;

    RETURN QUERY SELECT v_turn, v_ord;
END $$;

ALTER FUNCTION product.ask_interview_turn(uuid, uuid, text, text) OWNER TO ezgumed;

CREATE OR REPLACE FUNCTION product.record_interview_answer(
    p_interview_id  uuid,
    p_answer_kind   text,
    p_answer_text   text,
    p_voice_file_id text DEFAULT NULL,
    p_voice_seconds smallint DEFAULT NULL)
RETURNS TABLE (turn_id uuid, ord smallint) LANGUAGE plpgsql
SECURITY DEFINER SET search_path = product, public AS $$
DECLARE
    v_turn uuid;
    v_ord smallint;
BEGIN
    -- отвечаем всегда на последний заданный и ещё не отвеченный вопрос
    SELECT t.id, t.ord INTO v_turn, v_ord
    FROM interview_turns t
    WHERE t.interview_id = p_interview_id AND t.answered_at IS NULL
    ORDER BY t.ord DESC LIMIT 1;

    IF v_turn IS NULL THEN
        RAISE EXCEPTION 'нет заданного вопроса, на который можно ответить'
            USING ERRCODE = 'check_violation';
    END IF;

    UPDATE interview_turns SET answer_kind = p_answer_kind,
                               answer_text = p_answer_text,
                               voice_file_id = p_voice_file_id,
                               voice_seconds = p_voice_seconds,
                               answered_at = now()
    WHERE id = v_turn;

    UPDATE interviews SET last_activity_at = now(),
                          voice_answers = voice_answers
                              + CASE WHEN p_answer_kind = 'voice' THEN 1 ELSE 0 END
    WHERE id = p_interview_id;

    RETURN QUERY SELECT v_turn, v_ord;
END $$;

ALTER FUNCTION product.record_interview_answer(uuid, text, text, text, smallint)
    OWNER TO ezgumed;

CREATE OR REPLACE FUNCTION product.complete_interview(
    p_interview_id uuid,
    p_summary      text,
    p_extraction   jsonb DEFAULT NULL,
    p_gaps         text[] DEFAULT '{}',
    p_follow_ups   text[] DEFAULT '{}')
RETURNS void LANGUAGE sql SECURITY DEFINER
SET search_path = product, public AS $$
    UPDATE interviews
    SET status = 'completed', finished_at = now(), last_activity_at = now(),
        summary = p_summary, extraction = p_extraction,
        gaps = coalesce(p_gaps, '{}'), follow_ups = coalesce(p_follow_ups, '{}')
    WHERE id = p_interview_id AND status = 'in_progress';
$$;

ALTER FUNCTION product.complete_interview(uuid, text, jsonb, text[], text[])
    OWNER TO ezgumed;

CREATE OR REPLACE FUNCTION product.abandon_interview(p_interview_id uuid)
RETURNS void LANGUAGE sql SECURITY DEFINER
SET search_path = product, public AS $$
    UPDATE interviews SET status = 'abandoned', finished_at = now()
    WHERE id = p_interview_id AND status = 'in_progress';
$$;

ALTER FUNCTION product.abandon_interview(uuid) OWNER TO ezgumed;

-- Состояние интервью для бота: сколько отвечено, что дальше.
CREATE OR REPLACE FUNCTION product.interview_state(p_interview_id uuid)
RETURNS TABLE (
    status        interview_status,
    asked_count   smallint,
    answered_count integer,
    total_questions integer,
    turn_budget   smallint,
    job_title     text,
    clinic_name   text,
    pending_question text)
LANGUAGE sql SECURITY DEFINER SET search_path = product, public STABLE AS $$
    SELECT i.status, i.asked_count,
           (SELECT count(*)::int FROM interview_turns t
            WHERE t.interview_id = i.id AND t.answered_at IS NOT NULL),
           (SELECT count(*)::int FROM job_questions q WHERE q.job_id = a.job_id),
           i.turn_budget, j.title, c.name,
           (SELECT t.question_text FROM interview_turns t
            WHERE t.interview_id = i.id AND t.answered_at IS NULL
            ORDER BY t.ord DESC LIMIT 1)
    FROM interviews i
    JOIN applications a ON a.id = i.application_id
    JOIN jobs j ON j.id = a.job_id
    JOIN clinics c ON c.id = j.clinic_id
    WHERE i.id = p_interview_id;
$$;

ALTER FUNCTION product.interview_state(uuid) OWNER TO ezgumed;

-- ══ 10. Права ═════════════════════════════════════════════════════════════════
GRANT SELECT, INSERT, UPDATE, DELETE ON product.job_questions TO ishmed_app;
GRANT SELECT, INSERT, UPDATE ON product.interviews TO ishmed_app;
GRANT SELECT, INSERT, UPDATE ON product.interview_turns TO ishmed_app;

GRANT EXECUTE ON FUNCTION product.gen_public_code(int) TO ishmed_app;
GRANT EXECUTE ON FUNCTION product.publish_job(uuid) TO ishmed_app;
GRANT EXECUTE ON FUNCTION product.close_job(uuid) TO ishmed_app;
GRANT EXECUTE ON FUNCTION product.list_published_jobs(text, text, int, int) TO ishmed_app;
GRANT EXECUTE ON FUNCTION product.get_published_job(text) TO ishmed_app;
GRANT EXECUTE ON FUNCTION product.open_interview(bigint, uuid, text) TO ishmed_app;
GRANT EXECUTE ON FUNCTION product.next_interview_question(uuid) TO ishmed_app;
GRANT EXECUTE ON FUNCTION product.ask_interview_turn(uuid, uuid, text, text) TO ishmed_app;
GRANT EXECUTE ON FUNCTION product.record_interview_answer(uuid, text, text, text, smallint) TO ishmed_app;
GRANT EXECUTE ON FUNCTION product.complete_interview(uuid, text, jsonb, text[], text[]) TO ishmed_app;
GRANT EXECUTE ON FUNCTION product.abandon_interview(uuid) TO ishmed_app;
GRANT EXECUTE ON FUNCTION product.interview_state(uuid) TO ishmed_app;
