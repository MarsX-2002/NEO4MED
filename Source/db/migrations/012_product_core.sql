-- 012_product_core.sql
-- Продуктовая модель IshMed: клиники как тенанты, структура штатного расписания,
-- вакансии, профили медиков, матчи, приглашения, согласие и контакты.
--
-- Две гарантии переложены на саму БД, а не на дисциплину в коде:
--   1. RLS по clinic_id — забытый WHERE не отдаёт данные чужой клиники;
--   2. контакты кандидата недоступны прикладной роли; их отдаёт только
--      SECURITY DEFINER функция и только после accept.
-- Обе нужны именно потому, что вызовы выбирает LLM-агент: уговорить модель
-- словами можно, базу нельзя.
--
-- Схемы raw/core/ai не затрагиваются.

-- ══ Словари ═══════════════════════════════════════════════════════════════════
-- Без контролируемых списков матчинг не построить, а вывод LLM будет несопоставим
-- сам с собой: «медсестра», «hamshira» и «мед. сестра» должны быть одним кодом.

CREATE TABLE IF NOT EXISTS product.role_categories (
    code     text PRIMARY KEY,
    name_ru  text NOT NULL,
    name_uz  text NOT NULL,
    sort     smallint NOT NULL DEFAULT 100
);

INSERT INTO product.role_categories (code, name_ru, name_uz, sort) VALUES
    ('nurse',       'Медсестра / медбрат',   'Hamshira',                  10),
    ('lab',         'Лаборант',              'Laborant',                  20),
    ('diagnostics', 'Диагностика',           'Diagnostika',               30),
    ('doctor',      'Врач',                  'Shifokor',                  40),
    ('midwife',     'Акушер(ка)',            'Doya',                      50),
    ('paramedic',   'Фельдшер',              'Feldsher',                  60),
    ('junior',      'Младший медперсонал',   'Kichik tibbiyot xodimi',    70)
ON CONFLICT (code) DO NOTHING;

COMMENT ON TABLE product.role_categories IS
  'Категория роли — первый hard constraint. Врач и медсестра несовместимы, '
  'и именно на этом падал семантический поиск: в каталоге 1305 врачебных '
  'названий против 54 медсестринских.';

CREATE TABLE IF NOT EXISTS product.specialties (
    code           text PRIMARY KEY,
    role_category  text NOT NULL REFERENCES product.role_categories(code),
    name_ru        text NOT NULL,
    name_uz        text NOT NULL
);

INSERT INTO product.specialties (code, role_category, name_ru, name_uz) VALUES
    ('procedural_nurse',  'nurse',       'Процедурная медсестра',   'Protsedura hamshirasi'),
    ('ward_nurse',        'nurse',       'Палатная медсестра',      'Palata hamshirasi'),
    ('operating_nurse',   'nurse',       'Операционная медсестра',  'Operatsiya hamshirasi'),
    ('anesthesia_nurse',  'nurse',       'Медсестра-анестезист',    'Anesteziolog hamshirasi'),
    ('reception_nurse',   'nurse',       'Медсестра приёмного',     'Qabul bo‘limi hamshirasi'),
    ('general_nurse',     'nurse',       'Медсестра общего профиля','Umumiy hamshira'),
    ('lab_technician',    'lab',         'Лаборант',                'Laborant'),
    ('lab_assistant',     'lab',         'Помощник лаборанта',      'Laborant yordamchisi'),
    ('ultrasound',        'diagnostics', 'УЗИ',                     'UTT (UZI)'),
    ('xray',              'diagnostics', 'Рентген',                 'Rentgen'),
    ('functional_diag',   'diagnostics', 'Функциональная диагностика','Funksional diagnostika'),
    ('midwife',           'midwife',     'Акушерка',                'Doya'),
    ('paramedic',         'paramedic',   'Фельдшер',                'Feldsher'),
    ('orderly',           'junior',      'Санитар(ка)',             'Sanitar'),
    ('doctor_any',        'doctor',      'Врач (любая специальность)','Shifokor')
ON CONFLICT (code) DO NOTHING;

CREATE TABLE IF NOT EXISTS product.schedule_kinds (
    code     text PRIMARY KEY,
    name_ru  text NOT NULL,
    name_uz  text NOT NULL
);

INSERT INTO product.schedule_kinds (code, name_ru, name_uz) VALUES
    ('day',       'Дневной',        'Kunduzgi'),
    ('night',     'Ночной',         'Tungi'),
    ('shift',     'Сменный',        'Smenali'),
    ('full_time', 'Полный день',    'To‘liq kun'),
    ('part_time', 'Неполный день',  'Yarim kun'),
    ('rotational','Вахтовый',       'Vaxta')
ON CONFLICT (code) DO NOTHING;

-- География P0 — Ташкент. Названия берём из уже загруженного каталога,
-- чтобы районы у клиник и в импортированных вакансиях совпадали буквально.
CREATE TABLE IF NOT EXISTS product.districts (
    code     text PRIMARY KEY,
    city     text NOT NULL DEFAULT 'tashkent',
    name_ru  text NOT NULL,
    name_uz  text NOT NULL
);

INSERT INTO product.districts (code, name_ru, name_uz) VALUES
    ('almazar',      'Алмазарский район',       'Olmazor tumani'),
    ('bektemir',     'Бектемирский район',      'Bektemir tumani'),
    ('mirabad',      'Мирабадский район',       'Mirobod tumani'),
    ('mirzo_ulugbek','Мирзо-Улугбекский район', 'Mirzo Ulug‘bek tumani'),
    ('sergeli',      'Сергелийский район',      'Sergeli tumani'),
    ('uchtepa',      'Учтепинский район',       'Uchtepa tumani'),
    ('chilanzar',    'Чиланзарский район',      'Chilonzor tumani'),
    ('shaykhantahur','Шайхантахурский район',   'Shayxontohur tumani'),
    ('yunusabad',    'Юнусабадский район',      'Yunusobod tumani'),
    ('yakkasaray',   'Яккасарайский район',     'Yakkasaroy tumani'),
    ('yangihayot',   'Янгихаётский район',      'Yangihayot tumani'),
    ('yashnabad',    'Яшнободский район',       'Yashnobod tumani')
ON CONFLICT (code) DO NOTHING;

-- ══ Типы ══════════════════════════════════════════════════════════════════════
DO $$ BEGIN CREATE TYPE product.clinic_access AS ENUM ('active','suspended');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN CREATE TYPE product.member_role AS ENUM ('owner','recruiter');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN CREATE TYPE product.profile_status AS ENUM ('draft','active','hidden','deleted');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN CREATE TYPE product.job_status AS ENUM ('draft','active','paused','closed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN CREATE TYPE product.match_level AS ENUM ('strong','possible');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN CREATE TYPE product.invitation_status AS ENUM ('sent','accepted','declined','expired','withdrawn');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN CREATE TYPE product.intake_kind AS ENUM ('candidate_voice','candidate_text','candidate_resume','job_text','job_voice');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN CREATE TYPE product.intake_state AS ENUM ('received','transcribed','extracted','confirmed','failed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN CREATE TYPE product.data_source AS ENUM ('voice','text','resume','manual','fixture');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ══ Контекст запроса ══════════════════════════════════════════════════════════
-- RLS опирается на переменные сессии, которые приложение выставляет на каждый
-- запрос. Обёртки нужны, чтобы политики читались, а отсутствие контекста давало
-- NULL (то есть «ничего не видно»), а не ошибку.
CREATE OR REPLACE FUNCTION product.current_clinic_id() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('ishmed.clinic_id', true), '')::uuid
$$;

CREATE OR REPLACE FUNCTION product.current_user_id() RETURNS bigint
LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('ishmed.user_id', true), '')::bigint
$$;

COMMENT ON FUNCTION product.current_clinic_id IS
  'Тенант текущего запроса из ishmed.clinic_id. Не выставлен — значит клиентских '
  'данных клиник не видно вообще.';

-- ══ Клиники ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS product.clinics (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name          text NOT NULL,
    inn           text,
    city          text NOT NULL DEFAULT 'tashkent',
    address       text,
    access_status product.clinic_access NOT NULL DEFAULT 'active',
    is_demo       boolean NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS product.clinic_members (
    clinic_id  uuid NOT NULL REFERENCES product.clinics(id) ON DELETE CASCADE,
    user_id    bigint NOT NULL REFERENCES product.users(id) ON DELETE CASCADE,
    role       product.member_role NOT NULL DEFAULT 'owner',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (clinic_id, user_id)
);
COMMENT ON TABLE product.clinic_members IS
  'Сотрудники клиники. На демо один владелец, но схема сразу допускает несколько: '
  'добавлять роли потом дороже, чем заложить сейчас.';

-- Пароль отдельной таблицей, а не колонкой в users: хэш не должен случайно
-- попасть в общий SELECT по пользователю или в сериализатор.
CREATE TABLE IF NOT EXISTS product.user_credentials (
    user_id         bigint PRIMARY KEY REFERENCES product.users(id) ON DELETE CASCADE,
    password_hash   text NOT NULL,
    password_set_at timestamptz NOT NULL DEFAULT now(),
    failed_attempts smallint NOT NULL DEFAULT 0,
    locked_until    timestamptz,
    CONSTRAINT ck_credentials_hash_is_argon2 CHECK (password_hash LIKE '$argon2%')
);
COMMENT ON CONSTRAINT ck_credentials_hash_is_argon2 ON product.user_credentials IS
  'Страховка от случайной записи пароля в открытом виде или слабым алгоритмом.';

-- Сессии веб-кабинета. В cookie уходит случайный токен, в базе лежит его хэш:
-- утечка дампа не даёт войти под чужой сессией.
CREATE TABLE IF NOT EXISTS product.sessions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash  text NOT NULL UNIQUE,
    user_id     bigint NOT NULL REFERENCES product.users(id) ON DELETE CASCADE,
    clinic_id   uuid REFERENCES product.clinics(id) ON DELETE CASCADE,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    revoked_at  timestamptz,
    ip          inet,
    user_agent  text
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON product.sessions (user_id);
CREATE INDEX IF NOT EXISTS ix_sessions_expires ON product.sessions (expires_at);

-- ══ Структура компании ════════════════════════════════════════════════════════
-- Клиника мыслит штатным расписанием, а не «объявлениями», поэтому вакансия
-- привязана к штатной единице, а не живёт сама по себе.
CREATE TABLE IF NOT EXISTS product.clinic_units (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id  uuid NOT NULL REFERENCES product.clinics(id) ON DELETE CASCADE,
    parent_id  uuid REFERENCES product.clinic_units(id) ON DELETE CASCADE,
    name       text NOT NULL,
    district   text REFERENCES product.districts(code),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (clinic_id, parent_id, name)
);
COMMENT ON TABLE product.clinic_units IS
  'Подразделения: филиал, поликлиника, отделение. Достраиваются по мере того, '
  'как клиника добавляет вакансии, либо заводятся вручную.';

CREATE TABLE IF NOT EXISTS product.staff_positions (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id      uuid NOT NULL REFERENCES product.clinics(id) ON DELETE CASCADE,
    unit_id        uuid REFERENCES product.clinic_units(id) ON DELETE SET NULL,
    title          text NOT NULL,
    role_category  text NOT NULL REFERENCES product.role_categories(code),
    specialty      text REFERENCES product.specialties(code),
    seats          smallint NOT NULL DEFAULT 1,
    seats_filled   smallint NOT NULL DEFAULT 0,
    -- Это и есть кнопки «вакантно / занято»: открытых ставок больше нуля.
    seats_open     smallint GENERATED ALWAYS AS (seats - seats_filled) STORED,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_staff_seats CHECK (seats > 0 AND seats_filled >= 0 AND seats_filled <= seats)
);
CREATE INDEX IF NOT EXISTS ix_staff_positions_clinic ON product.staff_positions (clinic_id);
CREATE INDEX IF NOT EXISTS ix_staff_positions_open ON product.staff_positions (clinic_id, seats_open)
    WHERE seats_open > 0;

-- ══ Вакансии ══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS product.jobs (
    id                    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id             uuid NOT NULL REFERENCES product.clinics(id) ON DELETE CASCADE,
    staff_position_id     uuid REFERENCES product.staff_positions(id) ON DELETE SET NULL,
    title                 text NOT NULL,
    role_category         text NOT NULL REFERENCES product.role_categories(code),
    specialty             text REFERENCES product.specialties(code),
    experience_min_months integer,
    required_skills       text[] NOT NULL DEFAULT '{}',
    required_languages    text[] NOT NULL DEFAULT '{}',
    city                  text NOT NULL DEFAULT 'tashkent',
    districts             text[] NOT NULL DEFAULT '{}',
    schedule              text[] NOT NULL DEFAULT '{}',
    salary_min_uzs        numeric(12,2),
    salary_max_uzs        numeric(12,2),
    credential_requirements text[] NOT NULL DEFAULT '{}',
    status                product.job_status NOT NULL DEFAULT 'draft',
    -- Исходный текст сохраняем всегда: клиника должна видеть, из чего
    -- получились поля, а мы — разбирать промахи извлечения.
    source_text           text,
    source                product.data_source NOT NULL DEFAULT 'text',
    extraction            jsonb,
    created_by            bigint REFERENCES product.users(id),
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_jobs_salary_range CHECK (
        salary_max_uzs IS NULL OR salary_min_uzs IS NULL OR salary_max_uzs >= salary_min_uzs
    ),
    CONSTRAINT ck_jobs_experience CHECK (
        experience_min_months IS NULL OR experience_min_months BETWEEN 0 AND 720
    )
);
CREATE INDEX IF NOT EXISTS ix_jobs_clinic ON product.jobs (clinic_id, status);
CREATE INDEX IF NOT EXISTS ix_jobs_role ON product.jobs (role_category, specialty) WHERE status = 'active';

-- ══ Профили медиков ═══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS product.candidate_profiles (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           bigint NOT NULL UNIQUE REFERENCES product.users(id) ON DELETE CASCADE,
    role_category     text REFERENCES product.role_categories(code),
    specialty         text REFERENCES product.specialties(code),
    experience_months integer,
    skills            text[] NOT NULL DEFAULT '{}',
    languages         text[] NOT NULL DEFAULT '{}',
    city              text NOT NULL DEFAULT 'tashkent',
    districts         text[] NOT NULL DEFAULT '{}',
    schedule          text[] NOT NULL DEFAULT '{}',
    salary_min_uzs    numeric(12,2),
    -- Все квалификации на P0 — со слов человека. Слово «проверено» в продукте
    -- не появляется, пока проверки нет: поле называется claims осознанно.
    credential_claims text[] NOT NULL DEFAULT '{}',
    status            product.profile_status NOT NULL DEFAULT 'draft',
    source            product.data_source NOT NULL DEFAULT 'voice',
    transcript        text,
    extraction        jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_candidate_experience CHECK (
        experience_months IS NULL OR experience_months BETWEEN 0 AND 720
    )
);
CREATE INDEX IF NOT EXISTS ix_candidates_role ON product.candidate_profiles (role_category, specialty)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS ix_candidates_districts ON product.candidate_profiles USING gin (districts);

-- ══ Контакты: закрытая таблица ════════════════════════════════════════════════
-- Прикладная роль НЕ получает на неё никаких прав. Единственный путь к телефону —
-- функция product.reveal_contact, и только после accept.
CREATE TABLE IF NOT EXISTS product.candidate_contacts (
    candidate_id       uuid PRIMARY KEY REFERENCES product.candidate_profiles(id) ON DELETE CASCADE,
    phone              text,
    telegram_username  text,
    shared_at          timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_contacts_something CHECK (phone IS NOT NULL OR telegram_username IS NOT NULL)
);
COMMENT ON TABLE product.candidate_contacts IS
  'ЗАКРЫТАЯ таблица. Прав у ishmed_app нет и быть не должно. '
  'Чтение только через product.reveal_contact (SECURITY DEFINER).';

-- ══ Приём данных ══════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS product.intake_sessions (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        bigint NOT NULL REFERENCES product.users(id) ON DELETE CASCADE,
    kind           product.intake_kind NOT NULL,
    state          product.intake_state NOT NULL DEFAULT 'received',
    audio_file_id  text,          -- file_id Telegram или имя файла из браузера
    audio_seconds  integer,
    transcript     text,
    extraction     jsonb,
    used_fallback  boolean NOT NULL DEFAULT false,
    error          text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);
COMMENT ON COLUMN product.intake_sessions.used_fallback IS
  'true — сработал fixture fallback DEMO_MODE. Нужен, чтобы в отчётах не выдавать '
  'заготовку за живой AI.';
CREATE INDEX IF NOT EXISTS ix_intake_user ON product.intake_sessions (user_id, created_at DESC);

-- ══ Матчи ═════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS product.matches (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id                 uuid NOT NULL REFERENCES product.jobs(id) ON DELETE CASCADE,
    candidate_id           uuid NOT NULL REFERENCES product.candidate_profiles(id) ON DELETE CASCADE,
    level                  product.match_level NOT NULL,
    score_internal         smallint NOT NULL,
    hard_constraints_passed boolean NOT NULL,
    -- Каждый показанный матч обязан иметь минимум две конкретные причины:
    -- ограничение проверяет это на уровне БД, а не на доверии к коду.
    reasons                text[] NOT NULL,
    gaps                   text[] NOT NULL DEFAULT '{}',
    algorithm_version      text NOT NULL,
    created_at             timestamptz NOT NULL DEFAULT now(),
    UNIQUE (job_id, candidate_id, algorithm_version),
    CONSTRAINT ck_matches_score CHECK (score_internal BETWEEN 0 AND 100),
    CONSTRAINT ck_matches_reasons CHECK (
        NOT hard_constraints_passed OR array_length(reasons, 1) >= 2
    )
);
COMMENT ON CONSTRAINT ck_matches_reasons ON product.matches IS
  'Прошедший фильтры матч без двух причин показывать нельзя (критерий A7).';
CREATE INDEX IF NOT EXISTS ix_matches_job ON product.matches (job_id, score_internal DESC);

-- ══ Приглашения и согласие ════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS product.invitations (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id       uuid NOT NULL REFERENCES product.jobs(id) ON DELETE CASCADE,
    candidate_id uuid NOT NULL REFERENCES product.candidate_profiles(id) ON DELETE CASCADE,
    status       product.invitation_status NOT NULL DEFAULT 'sent',
    message      text,
    sent_at      timestamptz NOT NULL DEFAULT now(),
    responded_at timestamptz,
    expires_at   timestamptz,
    UNIQUE (job_id, candidate_id),
    CONSTRAINT ck_invitations_response_time CHECK (
        (status IN ('sent','expired')) = (responded_at IS NULL)
    )
);
COMMENT ON CONSTRAINT ck_invitations_response_time ON product.invitations IS
  'Ответ и время ответа не расходятся: accepted/declined обязаны иметь responded_at.';
CREATE INDEX IF NOT EXISTS ix_invitations_candidate ON product.invitations (candidate_id, status);

CREATE TABLE IF NOT EXISTS product.consent_events (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invitation_id uuid REFERENCES product.invitations(id) ON DELETE CASCADE,
    actor_user_id bigint REFERENCES product.users(id) ON DELETE SET NULL,
    event_type    text NOT NULL,
    meta          jsonb NOT NULL DEFAULT '{}',
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_consent_event_type CHECK (event_type IN (
        'invite_sent','invite_accepted','invite_declined','invite_withdrawn',
        'contact_revealed','profile_hidden','profile_deleted','consent_given'
    ))
);
COMMENT ON TABLE product.consent_events IS
  'Журнал согласия: кто и когда согласился, кому открыли контакт. Аудит того, '
  'что мы обещали пользователю в тексте согласия.';
CREATE INDEX IF NOT EXISTS ix_consent_events_invitation ON product.consent_events (invitation_id);

-- ══ Триггеры updated_at ═══════════════════════════════════════════════════════
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['clinics','staff_positions','jobs','candidate_profiles',
                             'candidate_contacts','intake_sessions']
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS tr_%1$s_touch ON product.%1$s', t);
        EXECUTE format(
            'CREATE TRIGGER tr_%1$s_touch BEFORE UPDATE ON product.%1$s '
            'FOR EACH ROW EXECUTE FUNCTION core.tg_touch_updated_at()', t);
    END LOOP;
END $$;
