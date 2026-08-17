-- 039_talent_pool_and_invitations.sql
-- Подбор: человек сам заводит карточку, клиника ищет по всем и приглашает.
--
-- Раздел «Подбор» упирался не в фильтры, а в отсутствие тех, кого искать.
-- Профиль кандидата до сих пор рождался единственным путём — из отклика
-- (`open_interview` вставляет строку со `status='draft'`), и по политике
-- `p_candidates_own` такой профиль видит только та клиника, к которой человек
-- пришёл. Общий пул (`status='active'`) не наполнялся ничем: 036 сознательно
-- убрала перевод в `active` после интервью, потому что человек на «покажите
-- меня всем клиникам» не соглашался.
--
-- Здесь появляется второй вход в тот же профиль: человек заходит в бота и
-- заполняет карточку сам. Профиль по-прежнему ОДИН на человека
-- (candidate_profiles.user_id UNIQUE), меняется только то, кто его заполнил и
-- насколько широко он виден:
--
--   заполнил сам      -> status='active'  -> виден всем клиникам, анонимно
--   создан откликом   -> status='draft'   -> виден только той клинике
--
-- Три вещи, которые нельзя оставить в коде приложения:
--
--   1. Ручной ввод сильнее машинного. `apply_interview_extraction` писала в
--      профиль через COALESCE(новое, старое), то есть модель всегда побеждала.
--      Пока профили рождались только из отклика, это было безобидно. Теперь
--      человек напишет «5 лет», промямлит на собеседовании про три — и его
--      карточка в общем поиске молча изменится. Разворачиваем приоритет.
--   2. Коды словарей в массивах. `districts[]` и `schedule[]` не имеют ни FK,
--      ни CHECK: целостность держалась на внимательности вызывающего. Через
--      форму туда попадает пользовательский ввод, поэтому проверка переезжает
--      в функцию записи. Записанная мимо словаря фраза сломала бы фильтры
--      молча — худший способ узнать об ошибке.
--   3. Приглашение только на опубликованную вакансию с одобренным планом.
--      Приглашение ведёт в собеседование; без плана человек примет приглашение
--      и придёт в пустоту.
--
-- Плюс починка: `reveal_contact` (путь приглашений) осталась в виде до 035 —
-- при отсутствии контакта она пишет в журнал «контакт открыт» и возвращает
-- пустоту. 035 исправила только `reveal_application_contact`. Теперь обе двери
-- ведут себя одинаково.

-- ══ 1. Кто заполнил профиль ═══════════════════════════════════════════════════
ALTER TABLE product.candidate_profiles
    ADD COLUMN IF NOT EXISTS self_filled_at timestamptz;

COMMENT ON COLUMN product.candidate_profiles.self_filled_at IS
  'Человек заполнял карточку сам через форму в боте. Не NULL означает, что '
  'извлечение из интервью может только дополнять пустые поля, но не '
  'перезаписывать сказанное человеком напрямую.';

-- Новый тип события журнала: попадание в общий поиск — отдельное решение
-- человека, и оно обязано быть записано.
ALTER TABLE product.consent_events DROP CONSTRAINT IF EXISTS ck_consent_event_type;
ALTER TABLE product.consent_events ADD CONSTRAINT ck_consent_event_type CHECK (
    event_type IN (
        'invite_sent','invite_accepted','invite_declined','invite_withdrawn',
        'application_sent','application_accepted','application_declined',
        'contact_revealed','profile_hidden','profile_deleted','consent_given',
        'profile_published'
    )
);

-- ══ 2. Ручной ввод сильнее машинного ══════════════════════════════════════════
-- Единственное изменение против 036 — направление COALESCE при self_filled_at.
-- Массивы сравниваем через nullif(..., '{}'): пустой массив здесь означает
-- «человек ничего не указал», а не «указал пустоту».
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
        RETURN v_candidate;
    END IF;

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

    -- Статус не трогаем: попадание в общий каталог — отдельное решение
    -- человека (см. 036), а не следствие собеседования.
    UPDATE product.candidate_profiles c
       SET experience_months = CASE WHEN c.self_filled_at IS NOT NULL
                                    THEN COALESCE(c.experience_months, v_months)
                                    ELSE COALESCE(v_months, c.experience_months) END,
           salary_min_uzs    = CASE WHEN c.self_filled_at IS NOT NULL
                                    THEN COALESCE(c.salary_min_uzs, v_salary)
                                    ELSE COALESCE(v_salary, c.salary_min_uzs) END,
           skills            = CASE WHEN c.self_filled_at IS NOT NULL
                                    THEN COALESCE(nullif(c.skills, '{}'), v_skills, c.skills)
                                    ELSE COALESCE(v_skills, c.skills) END,
           languages         = CASE WHEN c.self_filled_at IS NOT NULL
                                    THEN COALESCE(nullif(c.languages, '{}'), v_langs, c.languages)
                                    ELSE COALESCE(v_langs, c.languages) END,
           extraction        = v_data,
           source            = CASE WHEN c.self_filled_at IS NOT NULL THEN c.source
                                    ELSE 'text'::product.data_source END
     WHERE c.id = v_candidate;

    RETURN v_candidate;
END $$;

COMMENT ON FUNCTION product.apply_interview_extraction IS
  'Переносит interviews.extraction в поля профиля. Если человек заполнял '
  'карточку сам (self_filled_at), извлечение только дополняет пустые поля: '
  'сказанное человеком напрямую сильнее разобранного моделью. Статус профиля '
  'не меняет. График и специальность не трогает — там коды словарей.';

-- ══ 3. Запись своей карточки ══════════════════════════════════════════════════
-- SECURITY DEFINER по той же причине, что и my_candidate_card: бот работает без
-- контекста тенанта, а WITH CHECK политики p_candidates_own требует
-- current_user_id(), которого никто не выставлял.
--
-- Частичное обновление: форма идёт шагами, и каждый шаг пишется сразу. Никакого
-- состояния в памяти бота — состояние это сама строка. Перезапуск бота посреди
-- анкеты не теряет ответы, ровно как в интервью.
CREATE OR REPLACE FUNCTION product.save_my_profile(
    p_user_id           bigint,
    p_role_category     text    DEFAULT NULL,
    p_specialty         text    DEFAULT NULL,
    p_experience_months integer DEFAULT NULL,
    p_skills            text[]  DEFAULT NULL,
    p_languages         text[]  DEFAULT NULL,
    p_districts         text[]  DEFAULT NULL,
    p_schedule          text[]  DEFAULT NULL,
    p_salary_min_uzs    numeric DEFAULT NULL,
    p_credential_claims text[]  DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE
    v_candidate uuid;
    v_role      text;
    v_bad       text[];
BEGIN
    IF NOT EXISTS (SELECT 1 FROM product.users u WHERE u.id = p_user_id) THEN
        RAISE EXCEPTION 'пользователь не найден' USING ERRCODE = 'no_data_found';
    END IF;

    IF p_role_category IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM product.role_categories rc
                        WHERE rc.code = p_role_category) THEN
        RAISE EXCEPTION 'неизвестная категория роли: %', p_role_category
            USING ERRCODE = 'foreign_key_violation';
    END IF;

    -- Специальность обязана принадлежать своей категории. FK проверяет только
    -- существование кода, а «медсестра со специальностью стоматолог-хирург»
    -- прошла бы и сломала первый же hard-фильтр подбора.
    IF p_specialty IS NOT NULL THEN
        SELECT s.role_category INTO v_role
          FROM product.specialties s WHERE s.code = p_specialty;
        IF v_role IS NULL THEN
            RAISE EXCEPTION 'неизвестная специальность: %', p_specialty
                USING ERRCODE = 'foreign_key_violation';
        END IF;
        IF p_role_category IS NOT NULL AND v_role <> p_role_category THEN
            RAISE EXCEPTION 'специальность % не относится к категории %',
                p_specialty, p_role_category USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF p_districts IS NOT NULL THEN
        SELECT array_agg(x) INTO v_bad FROM unnest(p_districts) AS x
         WHERE NOT EXISTS (SELECT 1 FROM product.districts d WHERE d.code = x);
        IF v_bad IS NOT NULL THEN
            RAISE EXCEPTION 'неизвестный район: %', array_to_string(v_bad, ', ')
                USING ERRCODE = 'foreign_key_violation';
        END IF;
    END IF;

    IF p_schedule IS NOT NULL THEN
        SELECT array_agg(x) INTO v_bad FROM unnest(p_schedule) AS x
         WHERE NOT EXISTS (SELECT 1 FROM product.schedule_kinds sk WHERE sk.code = x);
        IF v_bad IS NOT NULL THEN
            RAISE EXCEPTION 'неизвестный график: %', array_to_string(v_bad, ', ')
                USING ERRCODE = 'foreign_key_violation';
        END IF;
    END IF;

    IF p_experience_months IS NOT NULL
       AND (p_experience_months < 0 OR p_experience_months > 720) THEN
        RAISE EXCEPTION 'опыт вне допустимого диапазона: %', p_experience_months
            USING ERRCODE = 'check_violation';
    END IF;

    IF p_salary_min_uzs IS NOT NULL
       AND (p_salary_min_uzs <= 0 OR p_salary_min_uzs > 999999999) THEN
        RAISE EXCEPTION 'ожидание по зарплате вне допустимого диапазона'
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT c.id INTO v_candidate
      FROM product.candidate_profiles c WHERE c.user_id = p_user_id;

    IF v_candidate IS NULL THEN
        INSERT INTO product.candidate_profiles
            (user_id, role_category, specialty, experience_months,
             skills, languages, districts, schedule, salary_min_uzs,
             credential_claims, status, source, self_filled_at)
        VALUES (p_user_id, p_role_category, p_specialty, p_experience_months,
                COALESCE(p_skills, '{}'), COALESCE(p_languages, '{}'),
                COALESCE(p_districts, '{}'), COALESCE(p_schedule, '{}'),
                p_salary_min_uzs, COALESCE(p_credential_claims, '{}'),
                'draft', 'manual', now())
        RETURNING id INTO v_candidate;
        RETURN v_candidate;
    END IF;

    -- Смена категории роли обнуляет специальность: иначе останется
    -- специальность от прошлой категории, и проверка выше её уже не поймает.
    UPDATE product.candidate_profiles c
       SET role_category     = COALESCE(p_role_category, c.role_category),
           specialty         = CASE
               WHEN p_specialty IS NOT NULL THEN p_specialty
               WHEN p_role_category IS NOT NULL
                    AND p_role_category IS DISTINCT FROM c.role_category THEN NULL
               ELSE c.specialty END,
           experience_months = COALESCE(p_experience_months, c.experience_months),
           skills            = COALESCE(p_skills, c.skills),
           languages         = COALESCE(p_languages, c.languages),
           districts         = COALESCE(p_districts, c.districts),
           schedule          = COALESCE(p_schedule, c.schedule),
           salary_min_uzs    = COALESCE(p_salary_min_uzs, c.salary_min_uzs),
           credential_claims = COALESCE(p_credential_claims, c.credential_claims),
           self_filled_at    = COALESCE(c.self_filled_at, now()),
           -- Человек вернулся после удаления и снова заполняет карточку. Это
           -- его осознанное действие, поэтому надгробие снимаем — в отличие от
           -- apply_interview_extraction, которая удалённый профиль не оживляет
           -- никогда: там решение принимала бы машина.
           status            = CASE WHEN c.status = 'deleted' THEN 'draft'::product.profile_status
                                    ELSE c.status END
     WHERE c.id = v_candidate;

    RETURN v_candidate;
END $$;

ALTER FUNCTION product.save_my_profile(bigint, text, text, integer, text[], text[],
                                       text[], text[], numeric, text[]) OWNER TO ezgumed;

COMMENT ON FUNCTION product.save_my_profile IS
  'Частичная запись своей карточки медиком из бота. Проверяет коды словарей, '
  'включая принадлежность специальности своей категории роли: массивы '
  'districts/schedule не имеют FK, и мимо словаря записанная фраза сломала бы '
  'фильтры подбора молча. В общий поиск не выводит — это publish_my_profile.';

-- ══ 4. Своя карточка для формы ════════════════════════════════════════════════
-- my_candidate_card отдаёт то, что нужно показать человеку, и не отдаёт
-- districts/schedule/credential_claims — форме они нужны, чтобы понять, какой
-- шаг ещё не пройден. Телефона здесь тоже нет: только факт наличия.
DROP FUNCTION IF EXISTS product.my_profile_form(bigint);

CREATE FUNCTION product.my_profile_form(p_user_id bigint)
RETURNS TABLE (
    candidate_id      uuid,
    profile_status    text,
    self_filled       boolean,
    role_category     text,
    specialty         text,
    experience_months integer,
    skills            text[],
    languages         text[],
    districts         text[],
    schedule          text[],
    salary_min_uzs    numeric(12,2),
    credential_claims text[],
    has_contact       boolean,
    in_pool           boolean
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, product
STABLE
AS $$
    SELECT c.id,
           c.status::text,
           c.self_filled_at IS NOT NULL,
           c.role_category,
           c.specialty,
           c.experience_months,
           c.skills,
           c.languages,
           c.districts,
           c.schedule,
           c.salary_min_uzs,
           c.credential_claims,
           EXISTS (SELECT 1 FROM product.candidate_contacts cc
                    WHERE cc.candidate_id = c.id),
           c.status = 'active'
      FROM product.candidate_profiles c
     WHERE c.user_id = p_user_id
       AND c.status <> 'deleted';
$$;

ALTER FUNCTION product.my_profile_form(bigint) OWNER TO ezgumed;

COMMENT ON FUNCTION product.my_profile_form IS
  'Состояние своей карточки для формы в боте. Следующий шаг формы выводится из '
  'того, какие поля здесь пусты — состояние анкеты это сама строка, а не FSM '
  'в памяти процесса.';

-- ══ 5. Вход в общий поиск и выход из него ═════════════════════════════════════
-- Полноту проверяет БАЗА, а не бот: неполная карточка в поиске бесполезна
-- клинике и обидна человеку («меня никто не находит»). Возвращаем перечень
-- недостающего, чтобы бот сказал, чего не хватает, вместо общего отказа.
CREATE OR REPLACE FUNCTION product.publish_my_profile(p_user_id bigint)
RETURNS TABLE (candidate_id uuid, published boolean, missing text[], has_contact boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE
    c       product.candidate_profiles;
    v_miss  text[] := '{}';
    v_phone boolean;
BEGIN
    SELECT * INTO c FROM product.candidate_profiles cp WHERE cp.user_id = p_user_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'профиль кандидата не найден' USING ERRCODE = 'no_data_found';
    END IF;
    IF c.status = 'deleted' THEN
        RAISE EXCEPTION 'профиль удалён' USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF c.role_category IS NULL     THEN v_miss := v_miss || 'role_category'; END IF;
    IF c.specialty IS NULL         THEN v_miss := v_miss || 'specialty'; END IF;
    IF c.experience_months IS NULL THEN v_miss := v_miss || 'experience_months'; END IF;
    IF coalesce(array_length(c.districts, 1), 0) = 0 THEN v_miss := v_miss || 'districts'; END IF;
    IF coalesce(array_length(c.schedule, 1), 0)  = 0 THEN v_miss := v_miss || 'schedule'; END IF;

    v_phone := EXISTS (SELECT 1 FROM product.candidate_contacts cc
                        WHERE cc.candidate_id = c.id);

    IF coalesce(array_length(v_miss, 1), 0) > 0 THEN
        RETURN QUERY SELECT c.id, false, v_miss, v_phone;
        RETURN;
    END IF;

    -- Телефон для публикации не обязателен. Он нужен позже — контакт
    -- открывается только после accept, и до тех пор его отсутствие никому не
    -- мешает. Отдаём флаг, чтобы бот попросил номер здесь же, а не запрещал
    -- человеку показаться клиникам из-за не нажатой кнопки.
    UPDATE product.candidate_profiles cp
       SET status = 'active',
           self_filled_at = COALESCE(cp.self_filled_at, now())
     WHERE cp.id = c.id;

    INSERT INTO product.consent_events (actor_user_id, event_type, meta)
    VALUES (p_user_id, 'profile_published',
            jsonb_build_object('role', c.role_category, 'specialty', c.specialty));

    RETURN QUERY SELECT c.id, true, '{}'::text[], v_phone;
END $$;

ALTER FUNCTION product.publish_my_profile(bigint) OWNER TO ezgumed;

COMMENT ON FUNCTION product.publish_my_profile IS
  'Выводит карточку в общий поиск клиник. Неполную не выводит и возвращает '
  'список недостающих полей. Пишет profile_published в журнал согласий: '
  'видимость всем клиникам — обещание, которое обязано быть зафиксировано.';

CREATE OR REPLACE FUNCTION product.hide_my_profile(p_user_id bigint)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE v_candidate uuid;
BEGIN
    SELECT c.id INTO v_candidate
      FROM product.candidate_profiles c
     WHERE c.user_id = p_user_id AND c.status = 'active';
    IF v_candidate IS NULL THEN RETURN; END IF;

    UPDATE product.candidate_profiles c SET status = 'hidden' WHERE c.id = v_candidate;

    -- Данные не стираем: это не forget_candidate, а «пока не ищу». Человек
    -- вернётся и включится обратно одной кнопкой, не заполняя анкету заново.
    INSERT INTO product.consent_events (actor_user_id, event_type)
    VALUES (p_user_id, 'profile_hidden');
END $$;

ALTER FUNCTION product.hide_my_profile(bigint) OWNER TO ezgumed;

COMMENT ON FUNCTION product.hide_my_profile IS
  'Убирает карточку из общего поиска, ничего не стирая. Для удаления есть '
  'forget_candidate — это разные обещания, и путать их нельзя.';

-- ══ 6. Витрина пула для клиники ═══════════════════════════════════════════════
-- SECURITY DEFINER с закрытым списком колонок, как list_published_jobs. Смысл
-- тот же: имя, телефон, транскрипт и extraction не выйдут наружу, даже если в
-- коде кабинета забудут их отфильтровать.
--
-- Имени здесь НЕТ, и это отличие от списка откликов. В откликах имя показано,
-- потому что человек пришёл к этой клинике сам. В пуле он никого не выбирал:
-- до приглашения и accept карточка анонимна.
--
-- Контекст проверяем в WHERE, а не через RAISE: не выставленный контекст должен
-- означать «ничего не видно», как и везде. Функция SECURITY DEFINER, поэтому
-- политики внутри неё не работают, и без этой проверки пул читался бы из бота.
DROP FUNCTION IF EXISTS product.pool_candidates(text, text, text, text, integer, numeric, int, int);

CREATE FUNCTION product.pool_candidates(
    p_role_category  text    DEFAULT NULL,
    p_specialty      text    DEFAULT NULL,
    p_district       text    DEFAULT NULL,
    p_schedule       text    DEFAULT NULL,
    p_experience_min integer DEFAULT NULL,
    p_salary_max     numeric DEFAULT NULL,
    p_limit          int     DEFAULT 50,
    p_offset         int     DEFAULT 0
)
RETURNS TABLE (
    candidate_id      uuid,
    role_category     text,
    role_name_ru      text,
    role_name_uz      text,
    specialty         text,
    specialty_name_ru text,
    specialty_name_uz text,
    experience_months integer,
    skills            text[],
    languages         text[],
    city              text,
    districts         text[],
    schedule          text[],
    salary_min_uzs    numeric(12,2),
    credential_claims text[],
    has_contact       boolean,
    updated_at        timestamptz,
    total_count       bigint
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, product
STABLE
AS $$
    SELECT c.id,
           c.role_category, rc.name_ru, rc.name_uz,
           c.specialty, s.name_ru, s.name_uz,
           c.experience_months,
           c.skills, c.languages, c.city, c.districts, c.schedule,
           c.salary_min_uzs, c.credential_claims,
           EXISTS (SELECT 1 FROM product.candidate_contacts cc
                    WHERE cc.candidate_id = c.id),
           c.updated_at,
           count(*) OVER ()
      FROM product.candidate_profiles c
      LEFT JOIN product.role_categories rc ON rc.code = c.role_category
      LEFT JOIN product.specialties s      ON s.code  = c.specialty
     WHERE product.current_clinic_id() IS NOT NULL
       AND product.is_manager()
       AND c.status = 'active'
       AND (p_role_category  IS NULL OR c.role_category = p_role_category)
       AND (p_specialty      IS NULL OR c.specialty = p_specialty)
       AND (p_district       IS NULL OR c.districts @> ARRAY[p_district])
       AND (p_schedule       IS NULL OR c.schedule  @> ARRAY[p_schedule])
       AND (p_experience_min IS NULL OR coalesce(c.experience_months, 0) >= p_experience_min)
       -- «Кто согласен на эти деньги»: ожидание кандидата не выше названного.
       -- Не указавших ожидание не отсеиваем — молчание это не отказ.
       AND (p_salary_max     IS NULL OR c.salary_min_uzs IS NULL
                                     OR c.salary_min_uzs <= p_salary_max)
     ORDER BY c.updated_at DESC
     LIMIT least(greatest(p_limit, 1), 200) OFFSET greatest(p_offset, 0);
$$;

ALTER FUNCTION product.pool_candidates(text, text, text, text, integer, numeric, int, int)
    OWNER TO ezgumed;

COMMENT ON FUNCTION product.pool_candidates IS
  'Общий поиск кандидатов для клиники: только status=active, то есть только '
  'те, кто сам вывел карточку в поиск. Список колонок закрытый и анонимный — '
  'ни имени, ни телефона, ни транскрипта. Доступна только менеджеру в '
  'контексте тенанта: без контекста возвращает пусто.';

-- ══ 7. Приглашения ════════════════════════════════════════════════════════════
-- Гарантия, которую нельзя оставить в API: приглашать можно только на
-- опубликованную вакансию с одобренным планом. Приглашение ведёт человека в
-- собеседование, и без плана он придёт в пустоту.
--
-- И вторая: приглашать можно только того, кто сам вышел в поиск. Кандидат из
-- отклика (status='draft') виден клинике по другой причине — он к ней пришёл, и
-- разговор с ним идёт через отклик, а не через приглашение.
CREATE OR REPLACE FUNCTION product.send_invitation(
    p_job_id        uuid,
    p_candidate_id  uuid,
    p_actor_user_id bigint,
    p_message       text DEFAULT NULL
)
RETURNS TABLE (invitation_id uuid, is_new boolean, invitation_status text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE
    v_clinic  uuid;
    v_plan    text;
    v_jstatus product.job_status;
    v_cstatus product.profile_status;
    v_inv     product.invitations;
BEGIN
    SELECT j.clinic_id, j.status, j.interview_plan_status
      INTO v_clinic, v_jstatus, v_plan
      FROM product.jobs j WHERE j.id = p_job_id;
    IF v_clinic IS NULL THEN
        RAISE EXCEPTION 'вакансия не найдена' USING ERRCODE = 'no_data_found';
    END IF;

    -- Членство и роль проверяем здесь: функция SECURITY DEFINER, политики
    -- внутри неё не действуют, и без этой проверки пригласить можно было бы от
    -- имени чужой клиники, зная только два uuid.
    IF NOT EXISTS (
        SELECT 1 FROM product.clinic_members cm
         WHERE cm.clinic_id = v_clinic
           AND cm.user_id = p_actor_user_id
           AND cm.role IN ('owner', 'recruiter')
    ) THEN
        RAISE EXCEPTION 'нет права приглашать по этой вакансии'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF v_jstatus <> 'active' OR v_plan <> 'approved' THEN
        RAISE EXCEPTION 'вакансия не опубликована или план интервью не одобрен'
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT c.status INTO v_cstatus
      FROM product.candidate_profiles c WHERE c.id = p_candidate_id;
    IF v_cstatus IS NULL THEN
        RAISE EXCEPTION 'кандидат не найден' USING ERRCODE = 'no_data_found';
    END IF;
    IF v_cstatus <> 'active' THEN
        RAISE EXCEPTION 'кандидат не выводил карточку в общий поиск'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT * INTO v_inv FROM product.invitations i
     WHERE i.job_id = p_job_id AND i.candidate_id = p_candidate_id;
    IF FOUND THEN
        -- Повторное приглашение не создаём и статус не сбрасываем: отказ
        -- означает отказ, и второй раз спрашивать то же самое нельзя.
        RETURN QUERY SELECT v_inv.id, false, v_inv.status::text;
        RETURN;
    END IF;

    INSERT INTO product.invitations (job_id, candidate_id, message, status)
    VALUES (p_job_id, p_candidate_id, nullif(btrim(p_message), ''), 'sent')
    RETURNING * INTO v_inv;

    INSERT INTO product.consent_events (invitation_id, actor_user_id, event_type, meta)
    VALUES (v_inv.id, p_actor_user_id, 'invite_sent',
            jsonb_build_object('job_id', p_job_id));

    RETURN QUERY SELECT v_inv.id, true, v_inv.status::text;
END $$;

ALTER FUNCTION product.send_invitation(uuid, uuid, bigint, text) OWNER TO ezgumed;

COMMENT ON FUNCTION product.send_invitation IS
  'Приглашение кандидата из общего поиска на свою вакансию. Требует: '
  'менеджерского членства в клинике вакансии, опубликованной вакансии с '
  'одобренным планом, и кандидата, который сам вышел в поиск. Идемпотентна по '
  '(job_id, candidate_id) — повторный вызов возвращает существующее.';

CREATE OR REPLACE FUNCTION product.respond_invitation(
    p_invitation_id uuid,
    p_user_id       bigint,
    p_accept        boolean
)
RETURNS TABLE (invitation_id uuid, job_id uuid, invitation_status text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE
    v_inv    product.invitations;
    v_status product.invitation_status;
BEGIN
    SELECT i.* INTO v_inv
      FROM product.invitations i
      JOIN product.candidate_profiles c ON c.id = i.candidate_id
     WHERE i.id = p_invitation_id AND c.user_id = p_user_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'приглашение не найдено' USING ERRCODE = 'no_data_found';
    END IF;

    -- Отвечать можно один раз. Ответ — это событие в журнале согласий, и
    -- переигрывать его нельзя: клиника уже могла увидеть контакт.
    IF v_inv.status <> 'sent' THEN
        RAISE EXCEPTION 'на приглашение уже отвечено: %', v_inv.status
            USING ERRCODE = 'check_violation';
    END IF;

    v_status := CASE WHEN p_accept THEN 'accepted' ELSE 'declined' END;

    UPDATE product.invitations i
       SET status = v_status, responded_at = now()
     WHERE i.id = p_invitation_id;

    INSERT INTO product.consent_events (invitation_id, actor_user_id, event_type)
    VALUES (p_invitation_id, p_user_id,
            CASE WHEN p_accept THEN 'invite_accepted' ELSE 'invite_declined' END);

    RETURN QUERY SELECT v_inv.id, v_inv.job_id, v_status::text;
END $$;

ALTER FUNCTION product.respond_invitation(uuid, bigint, boolean) OWNER TO ezgumed;

COMMENT ON FUNCTION product.respond_invitation IS
  'Ответ медика на приглашение. Только из статуса sent и только по своему '
  'профилю. accepted открывает клинике контакт через reveal_contact — поэтому '
  'переотвечать нельзя.';

-- Список приглашений для бота: тенанта у него нет, политика invitations
-- опирается на current_user_id(), который бот не выставляет.
DROP FUNCTION IF EXISTS product.my_invitations(bigint);

CREATE FUNCTION product.my_invitations(p_user_id bigint)
RETURNS TABLE (
    invitation_id     uuid,
    job_id            uuid,
    public_code       text,
    job_title         text,
    clinic_name       text,
    invitation_status text,
    message           text,
    sent_at           timestamptz,
    specialty_name    text,
    experience_min_months integer,
    salary_min_uzs    numeric,
    salary_max_uzs    numeric,
    schedule          text[],
    questions_count   integer,
    job_open          boolean,
    has_application   boolean
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, product
STABLE
AS $$
    SELECT i.id, j.id, j.public_code, j.title, cl.name,
           i.status::text, i.message, i.sent_at,
           s.name_ru, j.experience_min_months,
           j.salary_min_uzs, j.salary_max_uzs, j.schedule,
           (SELECT count(*)::int FROM product.job_questions q WHERE q.job_id = j.id),
           (j.status = 'active' AND j.interview_plan_status = 'approved'),
           EXISTS (SELECT 1 FROM product.applications a
                    WHERE a.job_id = j.id AND a.candidate_id = i.candidate_id)
      FROM product.invitations i
      JOIN product.candidate_profiles c ON c.id = i.candidate_id
      JOIN product.jobs j    ON j.id = i.job_id
      JOIN product.clinics cl ON cl.id = j.clinic_id
      LEFT JOIN product.specialties s ON s.code = j.specialty
     WHERE c.user_id = p_user_id
       AND i.status IN ('sent', 'accepted')
     ORDER BY i.sent_at DESC
     LIMIT 30;
$$;

ALTER FUNCTION product.my_invitations(bigint) OWNER TO ezgumed;

COMMENT ON FUNCTION product.my_invitations IS
  'Приглашения медика для бота. Отклонённые не показываем: человек уже сказал '
  'нет, и напоминать ему об этом незачем. job_open нужен, чтобы не вести в '
  'собеседование по закрытой вакансии.';

-- ══ 8. Владелец функций-дверей к контактам ════════════════════════════════════
-- Найдено при подготовке этой миграции: `reveal_contact`, `save_contact`,
-- `forget_candidate` (013) и `reveal_application_contact` (014) принадлежат
-- РОЛИ postgres, то есть суперпользователю. Они `SECURITY DEFINER`, значит всё
-- это время исполнялись с правами суперпользователя, хотя им нужны права
-- ezgumed и ничего больше: все 36 таблиц схемы product принадлежат ezgumed.
--
-- Прямо сейчас это не эксплуатируется: у всех четырёх задан фиксированный
-- `search_path`, а он закрывает классический способ обмана — подсунуть свою
-- схему раньше product. Но радиус поражения любой будущей ошибки внутри них
-- сейчас равен «весь кластер», а должен равняться «таблицы product». Смысл
-- закрытой таблицы контактов в том, что дверей ровно три; дверь, открывающаяся
-- правами суперпользователя, — не та дверь, которую мы описывали.
--
-- Меняем владельца у этих четырёх: они образуют одну группу, и именно их
-- гарантию расширяет эта миграция. Остальные 18 функций postgres не трогаю
-- вслепую — они в долгах отдельной строкой.
ALTER FUNCTION product.save_contact(bigint, text, text)              OWNER TO ezgumed;
ALTER FUNCTION product.forget_candidate(bigint)                      OWNER TO ezgumed;
ALTER FUNCTION product.reveal_contact(uuid, bigint)                  OWNER TO ezgumed;
ALTER FUNCTION product.reveal_application_contact(uuid, bigint)      OWNER TO ezgumed;

-- ══ 9. Починка reveal_contact ═════════════════════════════════════════════════
-- 035 закрыла эту дыру только для пути откликов. Здесь она жила до сих пор:
-- при отсутствии контакта функция писала в журнал согласий «контакт открыт» и
-- возвращала пустоту. Запись в журнале обязана означать, что контакт
-- действительно был показан, иначе журнал согласий врёт — а он единственное
-- доказательство того, что мы выполняли обещание.
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
    SELECT * INTO inv FROM product.invitations i WHERE i.id = p_invitation_id;
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

    -- Раскрывать нечего — значит и события нет. Проверка ДО вставки: иначе в
    -- журнале остаётся «контакт открыт» при пустом ответе (было до 038).
    IF NOT EXISTS (SELECT 1 FROM product.candidate_contacts cc
                    WHERE cc.candidate_id = inv.candidate_id) THEN
        RAISE EXCEPTION 'кандидат не оставил контакт' USING ERRCODE = 'no_data_found';
    END IF;

    INSERT INTO product.consent_events (invitation_id, actor_user_id, event_type, meta)
    VALUES (p_invitation_id, p_actor_user_id, 'contact_revealed',
            jsonb_build_object('as', CASE WHEN is_clinic THEN 'clinic' ELSE 'medic' END,
                               'via', 'invitation'));

    RETURN QUERY
        SELECT cc.phone, cc.telegram_username
        FROM product.candidate_contacts cc
        WHERE cc.candidate_id = inv.candidate_id;
END $$;

COMMENT ON FUNCTION product.reveal_contact IS
  'Контакт кандидата по приглашению. Требует status=accepted, участия '
  'запрашивающего и существующего контакта. Пишет событие в consent_events '
  'только когда раскрывать есть что.';

-- ══ 10. Политики: подбор — управленческий раздел ══════════════════════════════
-- В 022 менеджерская проверка появилась у вакансий, сотрудников и отзывов, но
-- кандидатов, матчей и приглашений не коснулась: там дропались политики
-- p_*_manager, которых никогда не существовало. В итоге роль employee видела
-- весь пул кандидатов. Закрываем.
--
-- Сторона медика проверку роли НЕ получает: у бота нет ни клиники, ни роли, и
-- добавить туда is_manager() означало бы отрезать человека от своего профиля.
DROP POLICY IF EXISTS p_candidates_own ON product.candidate_profiles;

CREATE POLICY p_candidates_own ON product.candidate_profiles
    USING (
        user_id = product.current_user_id()
        OR (
            product.current_clinic_id() IS NOT NULL
            AND product.is_manager()
            AND (
                -- общий пул: человек сам вывел карточку в поиск
                status = 'active'
                -- или он откликнулся именно к этой клинике
                OR (status <> 'deleted'
                    AND product.candidate_applied_to_clinic(
                            candidate_profiles.id, product.current_clinic_id()))
            )
        )
    )
    WITH CHECK (user_id = product.current_user_id());

COMMENT ON POLICY p_candidates_own ON product.candidate_profiles IS
  'Владелец видит свой профиль всегда. Менеджер клиники — карточки из общего '
  'поиска и карточки тех, кто откликнулся на его вакансии. Сотрудник (employee) '
  'не видит кандидатов вообще: подбор — управленческий раздел.';

DROP POLICY IF EXISTS p_matches_both_sides ON product.matches;

CREATE POLICY p_matches_both_sides ON product.matches
    USING (
        EXISTS (SELECT 1 FROM product.jobs j
                 WHERE j.id = matches.job_id
                   AND j.clinic_id = product.current_clinic_id()
                   AND product.is_manager())
        OR EXISTS (SELECT 1 FROM product.candidate_profiles c
                    WHERE c.id = matches.candidate_id
                      AND c.user_id = product.current_user_id())
    )
    WITH CHECK (
        EXISTS (SELECT 1 FROM product.jobs j
                 WHERE j.id = matches.job_id
                   AND j.clinic_id = product.current_clinic_id()
                   AND product.is_manager())
    );

DROP POLICY IF EXISTS p_invitations_both_sides ON product.invitations;

CREATE POLICY p_invitations_both_sides ON product.invitations
    USING (
        EXISTS (SELECT 1 FROM product.jobs j
                 WHERE j.id = invitations.job_id
                   AND j.clinic_id = product.current_clinic_id()
                   AND product.is_manager())
        OR EXISTS (SELECT 1 FROM product.candidate_profiles c
                    WHERE c.id = invitations.candidate_id
                      AND c.user_id = product.current_user_id())
    )
    WITH CHECK (
        EXISTS (SELECT 1 FROM product.jobs j
                 WHERE j.id = invitations.job_id
                   AND j.clinic_id = product.current_clinic_id()
                   AND product.is_manager())
        OR EXISTS (SELECT 1 FROM product.candidate_profiles c
                    WHERE c.id = invitations.candidate_id
                      AND c.user_id = product.current_user_id())
    );

-- ══ 11. Индексы ═══════════════════════════════════════════════════════════════
-- ix_matches_job покрывает выдачу по вакансии. Обратный запрос — «мои матчи» со
-- стороны медика и удаление при forget_candidate — шёл seq scan.
CREATE INDEX IF NOT EXISTS ix_matches_candidate ON product.matches (candidate_id);

-- Приглашения по вакансии: кабинет спрашивает их вместе с матчами, а индекс был
-- только по (candidate_id, status).
CREATE INDEX IF NOT EXISTS ix_invitations_job ON product.invitations (job_id, status);

-- Пул фильтруется по специальности и графику, а частичный индекс 012 покрывал
-- только (role_category, specialty). GIN по schedule нужен для @>.
CREATE INDEX IF NOT EXISTS ix_candidates_schedule ON product.candidate_profiles
    USING gin (schedule);

-- ══ 12. Права ═════════════════════════════════════════════════════════════════
-- Таблицы прав не требуют: candidate_profiles, matches, invitations уже
-- открыты прикладной роли в 013. Новые двери — только функции.
GRANT EXECUTE ON FUNCTION
      product.save_my_profile(bigint, text, text, integer, text[], text[],
                              text[], text[], numeric, text[]),
      product.my_profile_form(bigint),
      product.publish_my_profile(bigint),
      product.hide_my_profile(bigint),
      product.pool_candidates(text, text, text, text, integer, numeric, int, int),
      product.send_invitation(uuid, uuid, bigint, text),
      product.respond_invitation(uuid, bigint, boolean),
      product.my_invitations(bigint)
      TO ishmed_app;
