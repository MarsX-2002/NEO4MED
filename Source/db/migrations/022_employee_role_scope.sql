-- 022_employee_role_scope.sql
-- Сотрудник не должен видеть кабинет клиники.
--
-- Что я сломал в 020. Чтобы сотрудник попал в контекст своей клиники и увидел
-- собственные курсы, accept_employee_invite добавлял его в clinic_members с
-- ролью recruiter. Но RLS различает ТЕНАНТЫ, а не роли: получив clinic_id,
-- сотрудник получил и список коллег, и отзывы пациентов, и вакансии.
-- Проверено на живом аккаунте — /api/employees отдавал ему всё.
--
-- Исправление в два рубежа:
--   1. отдельная роль employee в clinic_members;
--   2. политики RLS на управленческих таблицах исключают эту роль.
-- Проверки в коде добавим тоже, но полагаться на них как на единственный
-- рубеж нельзя: забытый эндпоинт снова откроет данные.

ALTER TYPE product.member_role ADD VALUE IF NOT EXISTS 'employee';

-- Роль текущего запроса. Как и с тенантом: не выставлено — значит прав нет,
-- а не «есть все».
CREATE OR REPLACE FUNCTION product.current_member_role()
RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('ishmed.member_role', true), '')
$$;

COMMENT ON FUNCTION product.current_member_role IS
  'Роль сотрудника в клинике для текущего запроса: owner, recruiter или '
  'employee. Ставится приложением вместе с clinic_id.';

CREATE OR REPLACE FUNCTION product.is_manager()
RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT coalesce(product.current_member_role() IN ('owner', 'recruiter'), false)
$$;

GRANT EXECUTE ON FUNCTION product.current_member_role(), product.is_manager() TO ishmed_app;

-- ══ Управленческие таблицы: только для менеджеров ══════════════════════════════
-- Список выверен вручную. Сюда попало всё, что относится к управлению клиникой
-- и к другим людям. Курсы и назначения намеренно НЕ здесь: сотруднику они нужны.
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'employees', 'employee_invites',
        'reviews', 'review_targets', 'review_attachments',
        'jobs', 'staff_positions',
        'candidate_profiles', 'matches', 'invitations', 'applications',
        'kb_documents', 'kb_chunks',
        'clinic_members', 'consent_events', 'intake_sessions'
    ]
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS p_%1$s_manager ON product.%1$s', t);
    END LOOP;
END $$;

-- Полностью закрытые от сотрудника таблицы: снимаем старые «только по тенанту»
-- политики и ставим те же плюс требование быть менеджером.
DROP POLICY IF EXISTS p_employees_tenant ON product.employees;
CREATE POLICY p_employees_tenant ON product.employees
    USING (clinic_id = product.current_clinic_id() AND product.is_manager())
    WITH CHECK (clinic_id = product.current_clinic_id() AND product.is_manager());

DROP POLICY IF EXISTS p_employee_invites_tenant ON product.employee_invites;
CREATE POLICY p_employee_invites_tenant ON product.employee_invites
    USING (clinic_id = product.current_clinic_id() AND product.is_manager())
    WITH CHECK (clinic_id = product.current_clinic_id() AND product.is_manager());

DROP POLICY IF EXISTS p_reviews_tenant ON product.reviews;
CREATE POLICY p_reviews_tenant ON product.reviews
    USING (clinic_id = product.current_clinic_id() AND product.is_manager())
    WITH CHECK (clinic_id = product.current_clinic_id() AND product.is_manager());

DROP POLICY IF EXISTS p_review_targets_tenant ON product.review_targets;
CREATE POLICY p_review_targets_tenant ON product.review_targets
    USING (clinic_id = product.current_clinic_id() AND product.is_manager())
    WITH CHECK (clinic_id = product.current_clinic_id() AND product.is_manager());

DROP POLICY IF EXISTS p_review_attachments_tenant ON product.review_attachments;
CREATE POLICY p_review_attachments_tenant ON product.review_attachments
    USING (clinic_id = product.current_clinic_id() AND product.is_manager())
    WITH CHECK (clinic_id = product.current_clinic_id() AND product.is_manager());

DROP POLICY IF EXISTS p_jobs_tenant ON product.jobs;
CREATE POLICY p_jobs_tenant ON product.jobs
    USING (clinic_id = product.current_clinic_id() AND product.is_manager())
    WITH CHECK (clinic_id = product.current_clinic_id() AND product.is_manager());

DROP POLICY IF EXISTS p_staff_positions_tenant ON product.staff_positions;
CREATE POLICY p_staff_positions_tenant ON product.staff_positions
    USING (clinic_id = product.current_clinic_id() AND product.is_manager())
    WITH CHECK (clinic_id = product.current_clinic_id() AND product.is_manager());

DROP POLICY IF EXISTS p_kb_documents_tenant ON product.kb_documents;
CREATE POLICY p_kb_documents_tenant ON product.kb_documents
    USING (clinic_id = product.current_clinic_id() AND product.is_manager())
    WITH CHECK (clinic_id = product.current_clinic_id() AND product.is_manager());

DROP POLICY IF EXISTS p_kb_chunks_tenant ON product.kb_chunks;
CREATE POLICY p_kb_chunks_tenant ON product.kb_chunks
    USING (clinic_id = product.current_clinic_id() AND product.is_manager())
    WITH CHECK (clinic_id = product.current_clinic_id() AND product.is_manager());

DROP POLICY IF EXISTS p_clinic_members_tenant ON product.clinic_members;
CREATE POLICY p_clinic_members_tenant ON product.clinic_members
    USING (clinic_id = product.current_clinic_id() AND product.is_manager())
    WITH CHECK (clinic_id = product.current_clinic_id() AND product.is_manager());

-- Подразделения сотруднику нужны: он должен видеть, где работает, и курсы
-- могут ссылаться на подразделение. Оставляем чтение по тенанту, но запись
-- только менеджеру.
DROP POLICY IF EXISTS p_clinic_units_tenant ON product.clinic_units;
CREATE POLICY p_clinic_units_read ON product.clinic_units
    FOR SELECT USING (clinic_id = product.current_clinic_id());
CREATE POLICY p_clinic_units_write ON product.clinic_units
    FOR ALL USING (clinic_id = product.current_clinic_id() AND product.is_manager())
    WITH CHECK (clinic_id = product.current_clinic_id() AND product.is_manager());

-- ══ Обучение: сотрудник видит своё, менеджер — всё по клинике ═════════════════
DROP POLICY IF EXISTS p_courses_tenant ON product.courses;
CREATE POLICY p_courses_read ON product.courses
    FOR SELECT USING (
        clinic_id = product.current_clinic_id()
        AND (product.is_manager() OR status = 'published')
    );
CREATE POLICY p_courses_write ON product.courses
    FOR ALL USING (clinic_id = product.current_clinic_id() AND product.is_manager())
    WITH CHECK (clinic_id = product.current_clinic_id() AND product.is_manager());

DROP POLICY IF EXISTS p_course_lessons_tenant ON product.course_lessons;
CREATE POLICY p_course_lessons_read ON product.course_lessons
    FOR SELECT USING (clinic_id = product.current_clinic_id());
CREATE POLICY p_course_lessons_write ON product.course_lessons
    FOR ALL USING (clinic_id = product.current_clinic_id() AND product.is_manager())
    WITH CHECK (clinic_id = product.current_clinic_id() AND product.is_manager());

DROP POLICY IF EXISTS p_course_questions_tenant ON product.course_questions;
CREATE POLICY p_course_questions_read ON product.course_questions
    FOR SELECT USING (clinic_id = product.current_clinic_id());
CREATE POLICY p_course_questions_write ON product.course_questions
    FOR ALL USING (clinic_id = product.current_clinic_id() AND product.is_manager())
    WITH CHECK (clinic_id = product.current_clinic_id() AND product.is_manager());

DROP POLICY IF EXISTS p_course_options_tenant ON product.course_options;
CREATE POLICY p_course_options_read ON product.course_options
    FOR SELECT USING (clinic_id = product.current_clinic_id());
CREATE POLICY p_course_options_write ON product.course_options
    FOR ALL USING (clinic_id = product.current_clinic_id() AND product.is_manager())
    WITH CHECK (clinic_id = product.current_clinic_id() AND product.is_manager());

-- Назначения и попытки: сотрудник видит только свои. Связь с человеком идёт
-- через employees.user_id, а не через сессию — так подменить чужой employee_id
-- в запросе бессмысленно.
DROP POLICY IF EXISTS p_course_assignments_tenant ON product.course_assignments;
CREATE POLICY p_course_assignments_scope ON product.course_assignments
    USING (
        clinic_id = product.current_clinic_id()
        AND (
            product.is_manager()
            OR employee_id IN (
                SELECT e.id FROM product.employees e
                WHERE e.user_id = product.current_user_id()
            )
        )
    )
    WITH CHECK (clinic_id = product.current_clinic_id() AND product.is_manager());

DROP POLICY IF EXISTS p_course_attempts_tenant ON product.course_attempts;
CREATE POLICY p_course_attempts_scope ON product.course_attempts
    USING (
        clinic_id = product.current_clinic_id()
        AND (
            product.is_manager()
            OR employee_id IN (
                SELECT e.id FROM product.employees e
                WHERE e.user_id = product.current_user_id()
            )
        )
    )
    WITH CHECK (
        clinic_id = product.current_clinic_id()
        AND employee_id IN (
            SELECT e.id FROM product.employees e
            WHERE e.user_id = product.current_user_id()
        )
    );

-- ══ Своё подразделение и своя карточка ════════════════════════════════════════
-- Сотруднику нужны собственные данные, но product.employees для него закрыт.
-- Отдаём ровно себя, и ничего больше.
CREATE OR REPLACE FUNCTION product.my_employee_card()
RETURNS TABLE (
    employee_id uuid,
    full_name   text,
    unit_name   text,
    role_name   text,
    clinic_name text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
    SELECT e.id, e.full_name, u.name, rc.name_ru, c.name
    FROM product.employees e
    JOIN product.clinics c ON c.id = e.clinic_id
    LEFT JOIN product.clinic_units u ON u.id = e.unit_id
    LEFT JOIN product.role_categories rc ON rc.code = e.role_category
    WHERE e.user_id = product.current_user_id()
$$;

GRANT EXECUTE ON FUNCTION product.my_employee_card() TO ishmed_app;

-- ══ Роль в приглашении ════════════════════════════════════════════════════════
-- accept_employee_invite ставил recruiter — это и было причиной. Теперь employee.
CREATE OR REPLACE FUNCTION product.accept_employee_invite(
    p_token_hash    text,
    p_email         text,
    p_password_hash text
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE
    inv     record;
    v_user  bigint;
BEGIN
    SELECT i.*, e.full_name AS emp_name INTO inv
      FROM product.employee_invites i
      JOIN product.employees e ON e.id = i.employee_id
     WHERE i.token_hash = p_token_hash
       AND i.used_at IS NULL
       AND i.expires_at > now()
     FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'приглашение недействительно' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF p_password_hash NOT LIKE '$argon2%' THEN
        RAISE EXCEPTION 'пароль должен быть захэширован argon2' USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO product.users (role, email, locale, full_name, consent_at, consent_version)
    VALUES ('employee', lower(btrim(p_email)), 'ru', inv.emp_name, now(), 'employee-portal')
    RETURNING id INTO v_user;

    INSERT INTO product.user_credentials (user_id, password_hash)
    VALUES (v_user, p_password_hash);

    -- Роль employee: контекст тенанта нужен для курсов, но управленческие
    -- таблицы закрыты политиками именно по этой роли.
    INSERT INTO product.clinic_members (clinic_id, user_id, role)
    VALUES (inv.clinic_id, v_user, 'employee')
    ON CONFLICT DO NOTHING;

    UPDATE product.employees SET user_id = v_user WHERE id = inv.employee_id;
    UPDATE product.employee_invites SET used_at = now() WHERE id = inv.id;

    RETURN v_user;
END $$;

-- Уже созданным сотрудникам роль исправляем: тестовый аккаунт заведён до этой
-- миграции и сейчас имеет лишние права.
UPDATE product.clinic_members cm
   SET role = 'employee'
 WHERE EXISTS (
    SELECT 1 FROM product.users u
     WHERE u.id = cm.user_id AND u.role = 'employee'
 ) AND cm.role <> 'employee';
