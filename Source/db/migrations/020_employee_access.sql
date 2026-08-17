-- 020_employee_access.sql
-- Вход сотрудника в портал для обучения.
--
-- Сотрудник не регистрируется сам: менеджер принимает человека в штат и выдаёт
-- одноразовую ссылку, по которой тот задаёт пароль. Так у нас нет открытой
-- регистрации, которую пришлось бы защищать от посторонних, и нет паролей,
-- придуманных менеджером за сотрудника.
--
-- Роль employee уже есть в product.user_role (добавлена в 016).

CREATE TABLE IF NOT EXISTS product.employee_invites (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id   uuid NOT NULL REFERENCES product.clinics(id) ON DELETE CASCADE,
    employee_id uuid NOT NULL REFERENCES product.employees(id) ON DELETE CASCADE,

    -- В ссылку уходит случайный токен, здесь лежит только его sha256.
    -- Утечка дампа не даёт войти чужим приглашением — ровно как с сессиями.
    token_hash  text NOT NULL UNIQUE,

    expires_at  timestamptz NOT NULL,
    used_at     timestamptz,
    created_by  bigint REFERENCES product.users(id),
    created_at  timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE product.employee_invites IS
  'Одноразовые приглашения сотрудников в портал. Токен только хэшем.';

CREATE INDEX IF NOT EXISTS ix_employee_invites_employee
    ON product.employee_invites (employee_id, created_at DESC);

ALTER TABLE product.employee_invites OWNER TO ezgumed;
ALTER TABLE product.employee_invites ENABLE ROW LEVEL SECURITY;

CREATE POLICY p_employee_invites_tenant ON product.employee_invites
    USING (clinic_id = product.current_clinic_id())
    WITH CHECK (clinic_id = product.current_clinic_id());

GRANT SELECT, INSERT, UPDATE ON product.employee_invites TO ishmed_app;

-- ══ Приём приглашения ═════════════════════════════════════════════════════════
-- Сотрудник открывает ссылку, будучи никем: контекста тенанта нет, сессии нет.
-- Значит и здесь единственная дверь — SECURITY DEFINER функция. Она же создаёт
-- учётную запись, чтобы приложение не могло завести пользователя в обход
-- приглашения.

CREATE OR REPLACE FUNCTION product.peek_employee_invite(p_token_hash text)
RETURNS TABLE (
    employee_id uuid,
    full_name   text,
    clinic_name text,
    unit_name   text,
    is_valid    boolean,
    reason      text
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE inv record;
BEGIN
    SELECT i.*, e.full_name AS emp_name, c.name AS cl_name, u.name AS un_name
      INTO inv
      FROM product.employee_invites i
      JOIN product.employees e ON e.id = i.employee_id
      JOIN product.clinics  c  ON c.id = i.clinic_id
      LEFT JOIN product.clinic_units u ON u.id = e.unit_id
     WHERE i.token_hash = p_token_hash;

    IF NOT FOUND THEN
        RETURN QUERY SELECT NULL::uuid, NULL::text, NULL::text, NULL::text,
                            false, 'Приглашение не найдено';
        RETURN;
    END IF;
    IF inv.used_at IS NOT NULL THEN
        RETURN QUERY SELECT inv.employee_id, inv.emp_name, inv.cl_name, inv.un_name,
                            false, 'Приглашением уже воспользовались';
        RETURN;
    END IF;
    IF inv.expires_at < now() THEN
        RETURN QUERY SELECT inv.employee_id, inv.emp_name, inv.cl_name, inv.un_name,
                            false, 'Срок приглашения истёк';
        RETURN;
    END IF;

    RETURN QUERY SELECT inv.employee_id, inv.emp_name, inv.cl_name, inv.un_name,
                        true, NULL::text;
END $$;

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

    -- Сотрудник должен попадать в контекст своей клиники, иначе RLS не покажет
    -- ему даже собственные курсы. Роль в клинике при этом не owner и не
    -- recruiter — обучающийся не управляет ничем.
    INSERT INTO product.clinic_members (clinic_id, user_id, role)
    VALUES (inv.clinic_id, v_user, 'recruiter')
    ON CONFLICT DO NOTHING;

    UPDATE product.employees SET user_id = v_user WHERE id = inv.employee_id;
    UPDATE product.employee_invites SET used_at = now() WHERE id = inv.id;

    RETURN v_user;
END $$;

COMMENT ON FUNCTION product.accept_employee_invite IS
  'Создаёт учётную запись сотрудника по одноразовому приглашению. Пароль '
  'приходит уже захэшированным: открытый пароль в базу не попадает даже '
  'транзитом через аргумент функции.';

GRANT EXECUTE ON FUNCTION
      product.peek_employee_invite(text),
      product.accept_employee_invite(text, text, text)
      TO ishmed_app;
