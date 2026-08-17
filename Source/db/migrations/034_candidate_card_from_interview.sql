-- 034_candidate_card_from_interview.sql
-- Собранное на интервью доезжает до менеджера.
--
-- Было так: open_interview заводил профиль черновиком, интервью складывало
-- разобранные поля в interviews.extraction и на этом путь заканчивался.
-- Профиль оставался пустым, а вдобавок невидимым: p_candidates_own отдаёт
-- клинике только status = 'active'. Менеджер видел «Кандидат 3f2a91c4» и пять
-- прочерков, хотя опыт и навыки лежали в базе рядом.
--
-- Здесь три вещи:
--   1. клиника видит карточку того, кто откликнулся ИМЕННО К НЕЙ, независимо
--      от статуса профиля. Не «все активные всем» — только свои отклики;
--   2. extraction переносится в поля профиля отдельной функцией;
--   3. медик может посмотреть свою карточку в боте, и телефон в неё НЕ входит.

-- ── 1. Проверка «этот кандидат откликался к этой клинике» ─────────────────────
-- Через SECURITY DEFINER, а не подзапросом прямо в политике, и это не стиль.
-- Политика p_applications_both_sides сама смотрит в candidate_profiles. Если
-- политика candidate_profiles начнёт смотреть в applications, Postgres упрётся
-- в «infinite recursion detected in policy for relation». Функция принадлежит
-- владельцу, FORCE ROW LEVEL SECURITY нигде не включён, поэтому внутри неё
-- политики не применяются и рекурсии не возникает.
CREATE OR REPLACE FUNCTION product.candidate_applied_to_clinic(
    p_candidate uuid,
    p_clinic    uuid
)
RETURNS boolean
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, product
STABLE
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM product.applications a
        JOIN product.jobs j ON j.id = a.job_id
        WHERE a.candidate_id = p_candidate
          AND j.clinic_id = p_clinic
    );
$$;

ALTER FUNCTION product.candidate_applied_to_clinic(uuid, uuid) OWNER TO ezgumed;
GRANT EXECUTE ON FUNCTION product.candidate_applied_to_clinic(uuid, uuid) TO ishmed_app;

COMMENT ON FUNCTION product.candidate_applied_to_clinic IS
  'Откликался ли кандидат на вакансию этой клиники. Отдельная SECURITY DEFINER '
  'функция, потому что прямой подзапрос в политике candidate_profiles даёт '
  'взаимную рекурсию с политикой applications.';

-- ── 2. Клиника видит карточку своего отклика ──────────────────────────────────
DROP POLICY IF EXISTS p_candidates_own ON product.candidate_profiles;

CREATE POLICY p_candidates_own ON product.candidate_profiles
    USING (
        -- сам владелец профиля
        user_id = product.current_user_id()
        -- активный профиль виден клиникам: это талант-пул без контактов
        OR (status = 'active' AND product.current_clinic_id() IS NOT NULL)
        -- и отдельно: кандидат, который откликнулся именно к этой клинике.
        -- Пока интервью идёт, профиль ещё черновик, но менеджеру он нужен
        -- сразу — иначе в списке откликов пусто.
        OR (
            product.current_clinic_id() IS NOT NULL
            AND status <> 'deleted'
            AND product.candidate_applied_to_clinic(
                    candidate_profiles.id, product.current_clinic_id())
        )
    )
    WITH CHECK (user_id = product.current_user_id());

COMMENT ON POLICY p_candidates_own ON product.candidate_profiles IS
  'Владелец видит свой профиль всегда. Клиника — активные профили и профили '
  'тех, кто откликнулся на её вакансии. Удалённый профиль не виден никому, '
  'кроме владельца: право на забвение сильнее удобства менеджера.';

-- ── 3. Перенос разобранного в профиль ─────────────────────────────────────────
-- Вызывается после закрытия интервью. Идемпотентна: повторный вызов перезапишет
-- теми же значениями.
--
-- Чего здесь намеренно НЕТ:
--   * schedule и specialty. Модель отдаёт график свободным текстом («Сменный
--     график», «Готов работать по субботам»), а profile.schedule и specialty —
--     это коды словарей, по которым будет работать матчинг И4. Записать туда
--     фразу означает сломать фильтры молча. Сырой текст остаётся в extraction;
--   * слова «проверено». Всё, что здесь появляется, — со слов человека.
CREATE OR REPLACE FUNCTION product.apply_interview_extraction(p_interview_id uuid)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE
    v_candidate uuid;
    v_data      jsonb;
    v_months    integer;
    v_salary    numeric(12,2);
    v_skills    text[];
    v_langs     text[];
BEGIN
    SELECT a.candidate_id, i.extraction
      INTO v_candidate, v_data
      FROM product.interviews i
      JOIN product.applications a ON a.id = i.application_id
     WHERE i.id = p_interview_id;

    IF v_candidate IS NULL THEN
        RAISE EXCEPTION 'интервью не найдено' USING ERRCODE = 'no_data_found';
    END IF;
    IF v_data IS NULL OR v_data = '{}'::jsonb THEN
        -- Модель ничего не установила: профиль не портим, статус не двигаем.
        RETURN v_candidate;
    END IF;

    -- jsonb_typeof, а не приведение: если модель вернёт строку вместо числа,
    -- приведение уронит закрытие интервью, а это последнее, чем можно рисковать.
    IF jsonb_typeof(v_data -> 'experience_months') = 'number' THEN
        v_months := (v_data ->> 'experience_months')::integer;
        IF v_months < 0 OR v_months > 720 THEN
            v_months := NULL;  -- ck_candidate_experience
        END IF;
    END IF;

    IF jsonb_typeof(v_data -> 'salary_expectation_uzs') = 'number' THEN
        v_salary := (v_data ->> 'salary_expectation_uzs')::numeric(12,2);
        IF v_salary <= 0 OR v_salary > 999999999 THEN
            v_salary := NULL;
        END IF;
    END IF;

    IF jsonb_typeof(v_data -> 'skills') = 'array' THEN
        SELECT array_agg(btrim(x) ORDER BY ord)
          INTO v_skills
          FROM jsonb_array_elements_text(v_data -> 'skills') WITH ORDINALITY AS s(x, ord)
         WHERE btrim(x) <> '';
    END IF;

    IF jsonb_typeof(v_data -> 'languages') = 'array' THEN
        SELECT array_agg(btrim(x) ORDER BY ord)
          INTO v_langs
          FROM jsonb_array_elements_text(v_data -> 'languages') WITH ORDINALITY AS l(x, ord)
         WHERE btrim(x) <> '';
    END IF;

    UPDATE product.candidate_profiles c
       SET experience_months = COALESCE(v_months, c.experience_months),
           salary_min_uzs    = COALESCE(v_salary, c.salary_min_uzs),
           skills            = COALESCE(v_skills, c.skills),
           languages         = COALESCE(v_langs, c.languages),
           extraction        = v_data,
           source            = 'text',
           -- Черновик становится живым профилем: человек прошёл собеседование,
           -- это и есть подтверждение, что он ищет работу. Удалённый профиль
           -- не оживляем никогда — иначе forget_candidate ничего не значит.
           status            = CASE WHEN c.status = 'draft' THEN 'active' ELSE c.status END
     WHERE c.id = v_candidate;

    RETURN v_candidate;
END $$;

ALTER FUNCTION product.apply_interview_extraction(uuid) OWNER TO ezgumed;
GRANT EXECUTE ON FUNCTION product.apply_interview_extraction(uuid) TO ishmed_app;

COMMENT ON FUNCTION product.apply_interview_extraction IS
  'Переносит interviews.extraction в поля профиля кандидата и переводит '
  'черновик в active. График и специальность не трогает: там коды словарей, '
  'а модель отдаёт свободный текст.';

-- ── 4. Своя карточка для медика ───────────────────────────────────────────────
-- Бот работает без контекста тенанта, поэтому читать профиль напрямую он не
-- может: p_candidates_own опирается на current_user_id(), а его никто не
-- выставлял.
--
-- Телефона здесь НЕТ, и это не упущение. Прикладная роль не должна получать
-- способ прочитать контакт — даже свой, даже через функцию: p_user_id приходит
-- из кода, а код может ошибиться. Отдаём только факт «контакт указан».
DROP FUNCTION IF EXISTS product.my_candidate_card(bigint);

CREATE FUNCTION product.my_candidate_card(p_user_id bigint)
RETURNS TABLE (
    candidate_id      uuid,
    full_name         text,
    profile_status    text,
    role_category     text,
    role_category_ru  text,
    specialty         text,
    experience_months integer,
    skills            text[],
    languages         text[],
    salary_min_uzs    numeric(12,2),
    has_contact       boolean,
    applications_total bigint,
    interviews_done   bigint,
    consent_at        timestamptz
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, product
STABLE
AS $$
    SELECT c.id,
           u.full_name,
           c.status::text,
           c.role_category,
           rc.name_ru,
           c.specialty,
           c.experience_months,
           c.skills,
           c.languages,
           c.salary_min_uzs,
           EXISTS (SELECT 1 FROM product.candidate_contacts cc
                    WHERE cc.candidate_id = c.id),
           (SELECT count(*) FROM product.applications a WHERE a.candidate_id = c.id),
           (SELECT count(*) FROM product.applications a2
              JOIN product.interviews i2 ON i2.application_id = a2.id
             WHERE a2.candidate_id = c.id AND i2.status = 'completed'),
           u.consent_at
      FROM product.candidate_profiles c
      JOIN product.users u ON u.id = c.user_id
      LEFT JOIN product.role_categories rc ON rc.code = c.role_category
     WHERE c.user_id = p_user_id
       AND c.status <> 'deleted';
$$;

ALTER FUNCTION product.my_candidate_card(bigint) OWNER TO ezgumed;
GRANT EXECUTE ON FUNCTION product.my_candidate_card(bigint) TO ishmed_app;

COMMENT ON FUNCTION product.my_candidate_card IS
  'Своя карточка для бота медика. Телефон не отдаётся сознательно: только '
  'has_contact. Прикладная роль не получает пути к contacts ни в каком виде.';
