-- 013_product_security.sql
-- Изоляция тенантов через RLS и закрытые контакты через SECURITY DEFINER.
--
-- Смысл: и бот, и веб отдают часть решений LLM-агенту. Проверка «а можно ли
-- показать этот телефон», написанная в питоне, держится на том, что модель
-- не уговорят. Проверка в базе не держится ни на чём — она либо есть, либо нет.
--
-- RLS применяется к ishmed_app, потому что владелец объектов — ezgumed, а
-- владельцы RLS обходят. Поэтому SECURITY DEFINER функции, принадлежащие
-- ezgumed, видят всё и служат единственной легальной дверью.

-- ══ Владелец объектов ═════════════════════════════════════════════════════════
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT c.relname, c.relkind FROM pg_class c
             JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'product' AND c.relkind IN ('r','v')
    LOOP
        EXECUTE format('ALTER %s product.%I OWNER TO ezgumed',
                       CASE r.relkind WHEN 'v' THEN 'VIEW' ELSE 'TABLE' END, r.relname);
    END LOOP;
    FOR r IN SELECT p.oid::regprocedure AS sig FROM pg_proc p
             JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'product'
    LOOP
        EXECUTE format('ALTER FUNCTION %s OWNER TO ezgumed', r.sig);
    END LOOP;
END $$;

-- ══ RLS: таблицы, привязанные к тенанту ═══════════════════════════════════════
ALTER TABLE product.clinics         ENABLE ROW LEVEL SECURITY;
ALTER TABLE product.clinic_members  ENABLE ROW LEVEL SECURITY;
ALTER TABLE product.clinic_units    ENABLE ROW LEVEL SECURITY;
ALTER TABLE product.staff_positions ENABLE ROW LEVEL SECURITY;
ALTER TABLE product.jobs            ENABLE ROW LEVEL SECURITY;
ALTER TABLE product.matches         ENABLE ROW LEVEL SECURITY;
ALTER TABLE product.invitations     ENABLE ROW LEVEL SECURITY;
ALTER TABLE product.consent_events  ENABLE ROW LEVEL SECURITY;
ALTER TABLE product.intake_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE product.candidate_profiles ENABLE ROW LEVEL SECURITY;

-- RLS сознательно НЕ включаем на:
--   product.users, product.sessions, product.user_credentials
-- Их читают ДО того, как контекст известен: по telegram_user_id ищут медика,
-- по хэшу токена — сессию. Политика, зависящая от контекста, сделала бы вход
-- невозможным. Персональных данных сверх identity там нет, а телефон и username
-- живут в закрытой product.candidate_contacts.

CREATE POLICY p_clinics_tenant ON product.clinics
    USING (id = product.current_clinic_id())
    WITH CHECK (id = product.current_clinic_id());

CREATE POLICY p_clinic_members_tenant ON product.clinic_members
    USING (clinic_id = product.current_clinic_id())
    WITH CHECK (clinic_id = product.current_clinic_id());

CREATE POLICY p_clinic_units_tenant ON product.clinic_units
    USING (clinic_id = product.current_clinic_id())
    WITH CHECK (clinic_id = product.current_clinic_id());

CREATE POLICY p_staff_positions_tenant ON product.staff_positions
    USING (clinic_id = product.current_clinic_id())
    WITH CHECK (clinic_id = product.current_clinic_id());

CREATE POLICY p_jobs_tenant ON product.jobs
    USING (clinic_id = product.current_clinic_id())
    WITH CHECK (clinic_id = product.current_clinic_id());

-- Профиль медика: сам владелец видит всегда; клиника видит только активные
-- профили и только будучи в контексте тенанта. Контактов в этой таблице нет,
-- поэтому анонимная карточка безопасна по построению.
CREATE POLICY p_candidates_own ON product.candidate_profiles
    USING (
        user_id = product.current_user_id()
        OR (status = 'active' AND product.current_clinic_id() IS NOT NULL)
    )
    WITH CHECK (user_id = product.current_user_id());

CREATE POLICY p_intake_own ON product.intake_sessions
    USING (user_id = product.current_user_id())
    WITH CHECK (user_id = product.current_user_id());

-- Матчи и приглашения видны с двух сторон: клинике по своей вакансии,
-- медику по своему профилю.
CREATE POLICY p_matches_both_sides ON product.matches
    USING (
        EXISTS (SELECT 1 FROM product.jobs j
                 WHERE j.id = matches.job_id AND j.clinic_id = product.current_clinic_id())
        OR EXISTS (SELECT 1 FROM product.candidate_profiles c
                    WHERE c.id = matches.candidate_id AND c.user_id = product.current_user_id())
    )
    WITH CHECK (
        EXISTS (SELECT 1 FROM product.jobs j
                 WHERE j.id = matches.job_id AND j.clinic_id = product.current_clinic_id())
    );

CREATE POLICY p_invitations_both_sides ON product.invitations
    USING (
        EXISTS (SELECT 1 FROM product.jobs j
                 WHERE j.id = invitations.job_id AND j.clinic_id = product.current_clinic_id())
        OR EXISTS (SELECT 1 FROM product.candidate_profiles c
                    WHERE c.id = invitations.candidate_id AND c.user_id = product.current_user_id())
    )
    WITH CHECK (
        EXISTS (SELECT 1 FROM product.jobs j
                 WHERE j.id = invitations.job_id AND j.clinic_id = product.current_clinic_id())
        OR EXISTS (SELECT 1 FROM product.candidate_profiles c
                    WHERE c.id = invitations.candidate_id AND c.user_id = product.current_user_id())
    );

CREATE POLICY p_consent_events_scope ON product.consent_events
    USING (
        actor_user_id = product.current_user_id()
        OR EXISTS (
            SELECT 1 FROM product.invitations i JOIN product.jobs j ON j.id = i.job_id
             WHERE i.id = consent_events.invitation_id
               AND j.clinic_id = product.current_clinic_id())
    )
    WITH CHECK (true);   -- писать в журнал разрешено всегда, читать — по видимости

-- ══ Единственная дверь к контактам ════════════════════════════════════════════
-- SECURITY DEFINER + фиксированный search_path. Без явного search_path такую
-- функцию можно обмануть, подсунув свою схему раньше product.
CREATE OR REPLACE FUNCTION product.reveal_contact(
    p_invitation_id uuid,
    p_actor_user_id bigint
)
RETURNS TABLE (phone text, telegram_username text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE
    inv        product.invitations;
    is_clinic  boolean;
    is_medic   boolean;
BEGIN
    SELECT * INTO inv FROM product.invitations WHERE id = p_invitation_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'приглашение не найдено' USING ERRCODE = 'no_data_found';
    END IF;

    -- Ключевое правило продукта: invite + accept. Без обоих событий контакта нет.
    IF inv.status <> 'accepted' THEN
        RAISE EXCEPTION 'контакт закрыт: приглашение в статусе %', inv.status
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM product.invitations i
        JOIN product.jobs j            ON j.id = i.job_id
        JOIN product.clinic_members cm ON cm.clinic_id = j.clinic_id
        WHERE i.id = p_invitation_id AND cm.user_id = p_actor_user_id
    ) INTO is_clinic;

    SELECT EXISTS (
        SELECT 1 FROM product.candidate_profiles c
        WHERE c.id = inv.candidate_id AND c.user_id = p_actor_user_id
    ) INTO is_medic;

    IF NOT (is_clinic OR is_medic) THEN
        RAISE EXCEPTION 'запрашивающий не участник этого приглашения'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- Раскрытие — событие, которое обязано остаться в журнале.
    INSERT INTO product.consent_events (invitation_id, actor_user_id, event_type, meta)
    VALUES (p_invitation_id, p_actor_user_id, 'contact_revealed',
            jsonb_build_object('as', CASE WHEN is_clinic THEN 'clinic' ELSE 'medic' END));

    RETURN QUERY
        SELECT cc.phone, cc.telegram_username
        FROM product.candidate_contacts cc
        WHERE cc.candidate_id = inv.candidate_id;
END $$;

COMMENT ON FUNCTION product.reveal_contact IS
  'Единственный способ получить контакт кандидата. Требует status=accepted и '
  'участия запрашивающего. Пишет событие в consent_events.';

-- ══ Запись контакта: тоже только через функцию ════════════════════════════════
-- Прикладная роль не имеет INSERT на таблицу контактов, иначе «нет прав на
-- чтение» обходилось бы перезаписью и чтением возвращаемого значения.
CREATE OR REPLACE FUNCTION product.save_contact(
    p_user_id  bigint,
    p_phone    text,
    p_username text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE cand uuid;
BEGIN
    SELECT id INTO cand FROM product.candidate_profiles WHERE user_id = p_user_id;
    IF cand IS NULL THEN
        RAISE EXCEPTION 'профиль кандидата не найден' USING ERRCODE = 'no_data_found';
    END IF;

    INSERT INTO product.candidate_contacts (candidate_id, phone, telegram_username)
    VALUES (cand, nullif(btrim(p_phone), ''), nullif(btrim(p_username), ''))
    ON CONFLICT (candidate_id) DO UPDATE
        SET phone             = COALESCE(EXCLUDED.phone, product.candidate_contacts.phone),
            telegram_username = COALESCE(EXCLUDED.telegram_username,
                                         product.candidate_contacts.telegram_username),
            updated_at        = now();
END $$;

-- ══ Право на забвение ═════════════════════════════════════════════════════════
-- В тексте согласия обещано, что профиль можно скрыть или удалить в любой момент.
-- Обещание должно быть исполнимым, иначе оно ложное. Контакты лежат в закрытой
-- таблице, поэтому удаление тоже идёт через SECURITY DEFINER.
CREATE OR REPLACE FUNCTION product.forget_candidate(p_user_id bigint)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE cand uuid;
BEGIN
    SELECT id INTO cand FROM product.candidate_profiles WHERE user_id = p_user_id;
    IF cand IS NULL THEN RETURN; END IF;

    DELETE FROM product.candidate_contacts WHERE candidate_id = cand;
    DELETE FROM product.matches            WHERE candidate_id = cand;

    UPDATE product.invitations SET status = 'withdrawn', responded_at = now()
     WHERE candidate_id = cand AND status = 'sent';

    UPDATE product.candidate_profiles
       SET status = 'deleted', transcript = NULL, extraction = NULL,
           skills = '{}', credential_claims = '{}', districts = '{}'
     WHERE id = cand;

    INSERT INTO product.consent_events (actor_user_id, event_type)
    VALUES (p_user_id, 'profile_deleted');
END $$;

COMMENT ON FUNCTION product.forget_candidate IS
  'Удаление профиля по требованию медика: контакты и матчи стираются, профиль '
  'обезличивается, активные приглашения отзываются. Под обещание из текста согласия.';

-- ══ Права прикладной роли ═════════════════════════════════════════════════════
GRANT SELECT ON product.role_categories, product.specialties,
                product.schedule_kinds, product.districts TO ishmed_app;

GRANT SELECT, INSERT, UPDATE ON
      product.clinics, product.clinic_members, product.clinic_units,
      product.staff_positions, product.jobs, product.candidate_profiles,
      product.intake_sessions, product.invitations, product.consent_events
      TO ishmed_app;

-- Сессии и матчи пересоздаются, поэтому им нужен DELETE.
GRANT SELECT, INSERT, UPDATE, DELETE ON product.sessions, product.matches TO ishmed_app;

-- Пароли: читать хэш для проверки входа и обновлять счётчик попыток.
GRANT SELECT, INSERT, UPDATE ON product.user_credentials TO ishmed_app;

-- product.candidate_contacts: НИКАКИХ прав. Это не упущение, а решение.
REVOKE ALL ON product.candidate_contacts FROM ishmed_app;

GRANT EXECUTE ON FUNCTION
      product.reveal_contact(uuid, bigint),
      product.save_contact(bigint, text, text),
      product.forget_candidate(bigint),
      product.current_clinic_id(),
      product.current_user_id()
      TO ishmed_app;

-- Секвенция consent_events нужна для INSERT
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA product TO ishmed_app;
