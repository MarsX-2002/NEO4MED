-- 027_open_interview_ambiguity.sql
-- Починка open_interview: «column reference "status" is ambiguous».
--
-- Имена колонок из RETURNS TABLE становятся переменными plpgsql и перекрывают
-- одноимённые колонки таблиц в теле функции. У нас на выходе есть status, и
-- строка «SELECT interview_plan_status, status FROM jobs» становится
-- двусмысленной: это либо jobs.status, либо выходная переменная.
--
-- Ровно на это мы уже наступали с колонкой position в RETURNS TABLE дерева
-- подразделений. Лечится не переименованием, а полной квалификацией всех
-- ссылок на колонки псевдонимом таблицы — тогда порядок разрешения имён
-- перестаёт зависеть от того, как названы выходные колонки.

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
    v_role      text;
BEGIN
    SELECT j.interview_plan_status, j.status, j.role_category
      INTO v_plan, v_jstatus, v_role
    FROM jobs j
    WHERE j.id = p_job_id;

    IF NOT FOUND OR v_jstatus <> 'active' OR v_plan <> 'approved' THEN
        RAISE EXCEPTION 'вакансия не опубликована или у неё нет одобренного плана интервью'
            USING ERRCODE = 'check_violation';
    END IF;

    -- Профиль кандидата может ещё не существовать: он заводится первым
    -- откликом, а заполняется потом из ответов интервью.
    SELECT c.id INTO v_candidate
    FROM candidate_profiles c
    WHERE c.user_id = p_user_id;

    IF v_candidate IS NULL THEN
        INSERT INTO candidate_profiles (user_id, role_category, source, status)
        VALUES (p_user_id, v_role, 'text', 'draft')
        RETURNING candidate_profiles.id INTO v_candidate;
    END IF;

    SELECT a.id INTO v_app
    FROM applications a
    WHERE a.job_id = p_job_id AND a.candidate_id = v_candidate;

    IF v_app IS NULL THEN
        INSERT INTO applications (job_id, candidate_id, message)
        VALUES (p_job_id, v_candidate, p_message)
        RETURNING applications.id INTO v_app;

        UPDATE jobs j SET applications_count = j.applications_count + 1
        WHERE j.id = p_job_id;
    END IF;

    SELECT i.id, i.status, i.asked_count
      INTO v_interview, v_status, v_asked
    FROM interviews i
    WHERE i.application_id = v_app;

    IF v_interview IS NULL THEN
        INSERT INTO interviews (application_id)
        VALUES (v_app)
        RETURNING interviews.id, interviews.status, interviews.asked_count
        INTO v_interview, v_status, v_asked;
        v_new := true;
    END IF;

    RETURN QUERY SELECT v_interview, v_app, v_status, v_asked, v_new;
END $$;

ALTER FUNCTION product.open_interview(bigint, uuid, text) OWNER TO ezgumed;
GRANT EXECUTE ON FUNCTION product.open_interview(bigint, uuid, text) TO ishmed_app;

-- Та же профилактика для остальных функций 024: квалифицируем ссылки, где
-- имя выходной колонки совпадает с именем колонки таблицы.
CREATE OR REPLACE FUNCTION product.ask_interview_turn(
    p_interview_id uuid,
    p_question_id  uuid,
    p_question_text text,
    p_kind         text DEFAULT 'question')
RETURNS TABLE (turn_id uuid, ord smallint) LANGUAGE plpgsql
SECURITY DEFINER SET search_path = product, public AS $$
DECLARE
    v_ord    smallint;
    v_turn   uuid;
    v_budget smallint;
    v_used   smallint;
BEGIN
    SELECT i.turn_budget, i.asked_count INTO v_budget, v_used
    FROM interviews i
    WHERE i.id = p_interview_id AND i.status = 'in_progress'
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
    FROM interview_turns t
    WHERE t.interview_id = p_interview_id;

    INSERT INTO interview_turns (interview_id, ord, kind, question_id, question_text)
    VALUES (p_interview_id, v_ord, p_kind, p_question_id, p_question_text)
    RETURNING interview_turns.id INTO v_turn;

    UPDATE interviews i SET asked_count = i.asked_count + 1, last_activity_at = now()
    WHERE i.id = p_interview_id;

    RETURN QUERY SELECT v_turn, v_ord;
END $$;

ALTER FUNCTION product.ask_interview_turn(uuid, uuid, text, text) OWNER TO ezgumed;
GRANT EXECUTE ON FUNCTION product.ask_interview_turn(uuid, uuid, text, text) TO ishmed_app;

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
    v_ord  smallint;
BEGIN
    SELECT t.id, t.ord INTO v_turn, v_ord
    FROM interview_turns t
    WHERE t.interview_id = p_interview_id AND t.answered_at IS NULL
    ORDER BY t.ord DESC
    LIMIT 1;

    IF v_turn IS NULL THEN
        RAISE EXCEPTION 'нет заданного вопроса, на который можно ответить'
            USING ERRCODE = 'check_violation';
    END IF;

    UPDATE interview_turns t
    SET answer_kind = p_answer_kind,
        answer_text = p_answer_text,
        voice_file_id = p_voice_file_id,
        voice_seconds = p_voice_seconds,
        answered_at = now()
    WHERE t.id = v_turn;

    UPDATE interviews i
    SET last_activity_at = now(),
        voice_answers = i.voice_answers
            + CASE WHEN p_answer_kind = 'voice' THEN 1 ELSE 0 END
    WHERE i.id = p_interview_id;

    RETURN QUERY SELECT v_turn, v_ord;
END $$;

ALTER FUNCTION product.record_interview_answer(uuid, text, text, text, smallint)
    OWNER TO ezgumed;
GRANT EXECUTE ON FUNCTION product.record_interview_answer(uuid, text, text, text, smallint)
    TO ishmed_app;

CREATE OR REPLACE FUNCTION product.next_interview_question(p_interview_id uuid)
RETURNS TABLE (
    question_id uuid,
    ord         smallint,
    question    text,
    intent      text,
    total       integer,
    turns_left  integer)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = product, public STABLE AS $$
DECLARE
    v_job    uuid;
    v_budget smallint;
    v_used   smallint;
BEGIN
    SELECT a.job_id, i.turn_budget, i.asked_count
      INTO v_job, v_budget, v_used
    FROM interviews i
    JOIN applications a ON a.id = i.application_id
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
GRANT EXECUTE ON FUNCTION product.next_interview_question(uuid) TO ishmed_app;
