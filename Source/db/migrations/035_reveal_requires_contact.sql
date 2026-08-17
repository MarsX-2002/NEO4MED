-- 035_reveal_requires_contact.sql
-- Раскрытие, которого не было, не должно попадать в журнал согласий.
--
-- Прежняя функция при отсутствии контакта не падала: она писала событие
-- contact_revealed и возвращала пустой набор. Получалось худшее из двух —
-- менеджер не видит телефона и думает, что сломался кабинет, а в журнале
-- согласий у кандидата остаётся запись «ваш контакт открыли». Журнал согласий
-- — юридический документ, и ложная запись в нём хуже отсутствующей.
--
-- Теперь порядок обратный: сначала убеждаемся, что контакт есть, и только
-- потом пишем событие. Отдельный код ошибки, чтобы кабинет отличил «не имеете
-- права» от «кандидат не оставил телефон» и сказал менеджеру правду.

CREATE OR REPLACE FUNCTION product.reveal_application_contact(
    p_application_id uuid,
    p_actor_user_id  bigint
)
RETURNS TABLE (phone text, telegram_username text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE
    app       product.applications;
    is_clinic boolean;
    is_medic  boolean;
    has_row   boolean;
BEGIN
    SELECT * INTO app FROM product.applications WHERE id = p_application_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'отклик не найден' USING ERRCODE = 'no_data_found';
    END IF;

    IF app.status <> 'accepted' THEN
        RAISE EXCEPTION 'контакт закрыт: отклик в статусе %', app.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM product.applications a
        JOIN product.jobs j            ON j.id = a.job_id
        JOIN product.clinic_members cm ON cm.clinic_id = j.clinic_id
        WHERE a.id = p_application_id AND cm.user_id = p_actor_user_id
    ) INTO is_clinic;

    SELECT EXISTS (
        SELECT 1 FROM product.candidate_profiles c
        WHERE c.id = app.candidate_id AND c.user_id = p_actor_user_id
    ) INTO is_medic;

    IF NOT (is_clinic OR is_medic) THEN
        RAISE EXCEPTION 'запрашивающий не участник этого отклика'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM product.candidate_contacts cc
        WHERE cc.candidate_id = app.candidate_id
    ) INTO has_row;

    IF NOT has_row THEN
        -- 'no_data_found' здесь означает ровно одно: кандидат не оставлял
        -- телефон. Права проверены выше и в порядке.
        RAISE EXCEPTION 'кандидат не оставил контакт' USING ERRCODE = 'no_data_found';
    END IF;

    INSERT INTO product.consent_events (application_id, actor_user_id, event_type, meta)
    VALUES (p_application_id, p_actor_user_id, 'contact_revealed',
            jsonb_build_object('as', CASE WHEN is_clinic THEN 'clinic' ELSE 'medic' END,
                               'via', 'application'));

    RETURN QUERY
        SELECT cc.phone, cc.telegram_username
        FROM product.candidate_contacts cc
        WHERE cc.candidate_id = app.candidate_id;
END $$;

COMMENT ON FUNCTION product.reveal_application_contact IS
  'Контакт по отклику. Требует status=accepted, участия запрашивающего и '
  'наличия самого контакта: событие в журнал пишется только когда раскрывать '
  'действительно есть что.';
