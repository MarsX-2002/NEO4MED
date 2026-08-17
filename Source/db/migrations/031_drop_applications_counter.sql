-- 031_drop_applications_counter.sql
-- Убираем денормализованный счётчик откликов и чиним старые вакансии.
--
-- 1. jobs.applications_count наращивался в open_interview, но при удалении
--    отклика не уменьшался. После нескольких прогонов интервью кабинет
--    показывал 26 откликов при двух существующих. Держать счётчик в согласии
--    пришлось бы триггерами на вставку и удаление, и это был бы третий способ
--    узнать одно и то же число.
--    Откликов на вакансию — десятки, не миллионы: считаем запросом. Расходиться
--    с действительностью тогда нечему.
--
-- 2. Демо-вакансия «Процедурная медсестра» была опубликована до появления
--    правила «нет одобренного плана — нет публикации». Кандидатам она не
--    показывается (витрина фильтрует по approved), но в кабинете выглядела
--    опубликованной без вопросов. Возвращаем в черновики: пусть состояние
--    совпадает с тем, что происходит на самом деле.

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

ALTER TABLE product.jobs DROP COLUMN applications_count;

-- Вакансии, опубликованные до правила об одобренном плане, возвращаем
-- в черновики: сейчас по ним нельзя пройти интервью, а значит публикация
-- ничего не даёт.
UPDATE product.jobs
   SET status = 'draft', published_at = NULL
 WHERE status = 'active' AND interview_plan_status <> 'approved';
