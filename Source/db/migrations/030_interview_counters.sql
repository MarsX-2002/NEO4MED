-- 030_interview_counters.sql
-- Разделяем в счётчиках вопросы плана и уточнения.
--
-- Было: answered_count считал все отвеченные ходы, включая уточнения, а
-- total_questions — только вопросы плана. В кабинете это выглядело как
-- «ответов 9 из 6», а в боте «Вопрос 8 из 6». Уточнение — не восьмой вопрос
-- плана, это добор к третьему.
--
-- Уточнения не убираем из виду, а показываем отдельным числом: клинике полезно
-- знать, что кандидата пришлось переспрашивать.

-- CREATE OR REPLACE не меняет список выходных колонок: он часть подписи
-- функции. Поэтому обе функции сначала удаляем.
DROP FUNCTION IF EXISTS product.interview_state(uuid);
DROP FUNCTION IF EXISTS product.my_active_interview(bigint);

CREATE FUNCTION product.interview_state(p_interview_id uuid)
RETURNS TABLE (
    status        interview_status,
    asked_count   smallint,
    answered_count integer,
    total_questions integer,
    follow_ups_count integer,
    turn_budget   smallint,
    job_title     text,
    clinic_name   text,
    pending_question text)
LANGUAGE sql SECURITY DEFINER SET search_path = product, public STABLE AS $$
    SELECT i.status, i.asked_count,
           (SELECT count(*)::int FROM interview_turns t
             WHERE t.interview_id = i.id
               AND t.kind = 'question' AND t.answered_at IS NOT NULL),
           (SELECT count(*)::int FROM job_questions q WHERE q.job_id = a.job_id),
           (SELECT count(*)::int FROM interview_turns t
             WHERE t.interview_id = i.id AND t.kind = 'follow_up'),
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
GRANT EXECUTE ON FUNCTION product.interview_state(uuid) TO ishmed_app;

-- Бот показывает «Вопрос N из M» по этому счётчику, поэтому здесь та же правка.
CREATE FUNCTION product.my_active_interview(p_user_id bigint)
RETURNS TABLE (
    interview_id    uuid,
    job_id          uuid,
    job_title       text,
    clinic_name     text,
    asked_count     smallint,
    answered_count  integer,
    total_questions integer,
    follow_ups_count integer,
    pending_turn_id uuid,
    pending_question text,
    pending_is_follow_up boolean,
    started_at      timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path = product, public STABLE AS $$
    SELECT i.id, j.id, j.title, c.name,
           i.asked_count,
           (SELECT count(*)::int FROM interview_turns t
             WHERE t.interview_id = i.id
               AND t.kind = 'question' AND t.answered_at IS NOT NULL),
           (SELECT count(*)::int FROM job_questions q WHERE q.job_id = j.id),
           (SELECT count(*)::int FROM interview_turns t
             WHERE t.interview_id = i.id AND t.kind = 'follow_up'),
           (SELECT t.id FROM interview_turns t
             WHERE t.interview_id = i.id AND t.answered_at IS NULL
             ORDER BY t.ord DESC LIMIT 1),
           (SELECT t.question_text FROM interview_turns t
             WHERE t.interview_id = i.id AND t.answered_at IS NULL
             ORDER BY t.ord DESC LIMIT 1),
           (SELECT t.kind = 'follow_up' FROM interview_turns t
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
