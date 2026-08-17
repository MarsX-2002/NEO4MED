-- 011_agent_and_users.sql
-- Первая продуктовая миграция IshMed. Схемы raw/core/ai не трогаем.
--
-- Создаёт:
--   * роль приложения ishmed_app — под ней работают бот и веб;
--   * схему agent — там LangGraph сам ведёт свои таблицы чекпоинтов;
--   * схему product и таблицу product.users — минимум, нужный для /start.
-- Остальные продуктовые таблицы придут отдельной миграцией.

-- ── Роль приложения ───────────────────────────────────────────────────────────
-- Отдельная от ezgumed намеренно. ezgumed владеет объектами, а приложение
-- работает урезанными правами: тогда забытый GRANT ломает тест, а не
-- открывает лишние данные в бою. Пароль ставится отдельно скриптом
-- db/set-app-role-password.sh — в миграции секретов не держим.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ishmed_app') THEN
        CREATE ROLE ishmed_app LOGIN;
    END IF;
END $$;

COMMENT ON ROLE ishmed_app IS
  'Прикладная роль бота и веба. Не владеет объектами. Не должна получать права '
  'на product.candidate_contacts: контакты отдаёт только SECURITY DEFINER функция.';

-- ── Схема агента ──────────────────────────────────────────────────────────────
-- Владелец — ishmed_app, потому что таблицы чекпоинтов создаёт сама библиотека
-- при вызове PostgresSaver.setup(). Отдельная схема нужна, чтобы её миграции
-- никогда не пересеклись с нашими db/migrations.
CREATE SCHEMA IF NOT EXISTS agent AUTHORIZATION ishmed_app;
COMMENT ON SCHEMA agent IS
  'Чекпоинты LangGraph. Таблицами управляет библиотека, не наши миграции.';

-- ── Продуктовая схема ─────────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS product AUTHORIZATION ezgumed;
COMMENT ON SCHEMA product IS
  'Транзакционные сущности IshMed: пользователи, клиники, профили, вакансии, '
  'матчи, приглашения. Импортированный каталог живёт отдельно в core.';

-- Роль пользователя в продукте. Клиники сидят в веб-платформе, медики в боте,
-- поэтому у записи из Telegram роль всегда medic — но тип оставляем общим,
-- чтобы веб-пользователи клиник жили в той же таблице identity.
DO $$ BEGIN
    CREATE TYPE product.user_role AS ENUM ('medic', 'clinic_user', 'admin');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE product.locale AS ENUM ('ru', 'uz');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS product.users (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    public_id         uuid NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    role              product.user_role NOT NULL,

    -- Медик приходит из Telegram, сотрудник клиники — по email.
    -- Ровно один из двух идентификаторов обязателен.
    telegram_user_id  bigint UNIQUE,
    email             text UNIQUE,

    locale            product.locale,
    full_name         text,

    -- Согласие на обработку данных. Фиксируем ДО приёма персональных данных,
    -- поэтому это отдельное поле с временем, а не флаг.
    consent_at        timestamptz,
    consent_version   text,

    is_blocked        boolean NOT NULL DEFAULT false,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_users_identity CHECK (
        (telegram_user_id IS NOT NULL) OR (email IS NOT NULL)
    ),
    CONSTRAINT ck_users_medic_from_telegram CHECK (
        role <> 'medic' OR telegram_user_id IS NOT NULL
    ),
    CONSTRAINT ck_users_consent_pair CHECK (
        (consent_at IS NULL) = (consent_version IS NULL)
    )
);

COMMENT ON COLUMN product.users.consent_at IS
  'Время согласия. NULL означает, что персональные данные принимать нельзя.';
COMMENT ON COLUMN product.users.public_id IS
  'UUID для внешних ссылок. Числовой id наружу не отдаём.';

CREATE INDEX IF NOT EXISTS ix_users_role ON product.users (role);

DROP TRIGGER IF EXISTS tr_users_touch ON product.users;
CREATE TRIGGER tr_users_touch BEFORE UPDATE ON product.users
    FOR EACH ROW EXECUTE FUNCTION core.tg_touch_updated_at();

ALTER TABLE product.users OWNER TO ezgumed;

-- ── Права приложения ──────────────────────────────────────────────────────────
GRANT USAGE ON SCHEMA product TO ishmed_app;
GRANT SELECT, INSERT, UPDATE ON product.users TO ishmed_app;

-- Приложению нужен доступ к каталогу вакансий только на чтение.
GRANT USAGE ON SCHEMA core, ai TO ishmed_app;
GRANT SELECT ON ALL TABLES IN SCHEMA core TO ishmed_app;
GRANT SELECT ON ai.vacancy_embeddings, ai.embedding_models TO ishmed_app;
GRANT EXECUTE ON FUNCTION ai.search_vacancies(vector, text, integer, text, numeric, text) TO ishmed_app;
