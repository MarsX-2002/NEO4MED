-- 032_cache_question_voice.sql
-- Кэш озвученных вопросов.
--
-- Причина конкретная: у деплоймента tts квота подписки — 3 единицы, то есть
-- 3 запроса в минуту, и поднять выше нельзя (OpenAI.Standard.tts: 3 из 3).
-- Если озвучивать вопрос каждому кандидату заново, шесть нажатий «послушать»
-- растянутся на две минуты ожидания, а два кандидата одновременно упрутся
-- в лимит.
--
-- Но озвучивать заново и не нужно: вопросы плана фиксированы и одобрены,
-- значит запись для них одна и та же для всех. Telegram после первой отправки
-- голосового возвращает file_id, по которому то же аудио отправляется повторно
-- без загрузки. Итого один вызов tts на вопрос за всю жизнь вакансии вместо
-- одного на каждого кандидата.
--
-- Уточнения не кэшируем: они сочиняются под конкретный ответ и второй раз не
-- повторятся. Их немного, в лимит не упрёмся.

ALTER TABLE product.job_questions
    ADD COLUMN voice_file_id text,
    ADD COLUMN voice_made_at timestamptz;

COMMENT ON COLUMN product.job_questions.voice_file_id IS
  'file_id голосового в Telegram. Аудио у себя не храним: Telegram отдаёт его '
  'по этому идентификатору сколько угодно раз.';

-- Правка формулировки обязана сбросить кэш, иначе бот озвучит старый вопрос.
CREATE OR REPLACE FUNCTION product.tg_job_question_voice_reset() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.question IS DISTINCT FROM OLD.question THEN
        NEW.voice_file_id := NULL;
        NEW.voice_made_at := NULL;
    END IF;
    RETURN NEW;
END $$;

ALTER FUNCTION product.tg_job_question_voice_reset() OWNER TO ezgumed;

CREATE TRIGGER tr_job_questions_voice_reset
    BEFORE UPDATE ON product.job_questions
    FOR EACH ROW EXECUTE FUNCTION product.tg_job_question_voice_reset();

-- ══ Бот читает и пишет кэш без контекста клиники ══════════════════════════════
-- job_questions закрыта политикой «только менеджер своей клиники», а бот
-- работает от лица кандидата. Поэтому доступ через SECURITY DEFINER, и только
-- к тому вопросу, который сейчас задан этому интервью.

DROP FUNCTION IF EXISTS product.my_active_interview(bigint);

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
    pending_question_id uuid,
    pending_voice_file_id text,
    started_at      timestamptz)
LANGUAGE sql SECURITY DEFINER SET search_path = product, public STABLE AS $$
    WITH pending AS (
        SELECT t.id, t.interview_id, t.question_text, t.kind, t.question_id
        FROM interview_turns t
        JOIN interviews i2 ON i2.id = t.interview_id
        JOIN applications a2 ON a2.id = i2.application_id
        JOIN candidate_profiles c2 ON c2.id = a2.candidate_id
        WHERE c2.user_id = p_user_id
          AND i2.status = 'in_progress'
          AND t.answered_at IS NULL
        ORDER BY t.ord DESC
        LIMIT 1
    )
    SELECT i.id, j.id, j.title, c.name,
           i.asked_count,
           (SELECT count(*)::int FROM interview_turns t
             WHERE t.interview_id = i.id
               AND t.kind = 'question' AND t.answered_at IS NOT NULL),
           (SELECT count(*)::int FROM job_questions q WHERE q.job_id = j.id),
           (SELECT count(*)::int FROM interview_turns t
             WHERE t.interview_id = i.id AND t.kind = 'follow_up'),
           p.id, p.question_text, p.kind = 'follow_up', p.question_id,
           (SELECT q.voice_file_id FROM job_questions q WHERE q.id = p.question_id),
           i.started_at
    FROM interviews i
    JOIN applications a ON a.id = i.application_id
    JOIN candidate_profiles cp ON cp.id = a.candidate_id
    JOIN jobs j ON j.id = a.job_id
    JOIN clinics c ON c.id = j.clinic_id
    LEFT JOIN pending p ON p.interview_id = i.id
    WHERE cp.user_id = p_user_id
      AND i.status = 'in_progress'
    ORDER BY i.last_activity_at DESC
    LIMIT 1;
$$;

ALTER FUNCTION product.my_active_interview(bigint) OWNER TO ezgumed;
GRANT EXECUTE ON FUNCTION product.my_active_interview(bigint) TO ishmed_app;

-- Записать file_id может только тот, кому этот вопрос сейчас задан: иначе
-- функция стала бы способом подменить озвучку чужой вакансии.
CREATE OR REPLACE FUNCTION product.save_question_voice(
    p_question_id uuid, p_user_id bigint, p_file_id text)
RETURNS void LANGUAGE plpgsql SECURITY DEFINER
SET search_path = product, public AS $$
BEGIN
    IF p_question_id IS NULL OR coalesce(btrim(p_file_id), '') = '' THEN
        RETURN;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM interview_turns t
        JOIN interviews i ON i.id = t.interview_id
        JOIN applications a ON a.id = i.application_id
        JOIN candidate_profiles c ON c.id = a.candidate_id
        WHERE t.question_id = p_question_id
          AND c.user_id = p_user_id
          AND i.status = 'in_progress'
    ) THEN
        RAISE EXCEPTION 'этот вопрос вам не задавали'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    UPDATE job_questions
       SET voice_file_id = p_file_id, voice_made_at = now()
     WHERE id = p_question_id
       AND voice_file_id IS NULL;
END $$;

ALTER FUNCTION product.save_question_voice(uuid, bigint, text) OWNER TO ezgumed;
GRANT EXECUTE ON FUNCTION product.save_question_voice(uuid, bigint, text) TO ishmed_app;
