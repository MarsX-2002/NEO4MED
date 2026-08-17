-- 016_employees_and_reviews.sql
-- Модуль «Штат и отзывы»: реальные сотрудники клиники и оценки пациентов по QR.
--
-- Три решения, заложенные в схему, а не в код:
--
-- 1. Сотрудник — отдельная сущность. До сих пор в системе были кандидат
--    (ищет работу) и пользователь кабинета (менеджер). Работающего человека
--    не было, поэтому seats_filled двигали вручную. Теперь занятость ставки —
--    следствие факта, а не переключатель.
--
-- 2. Отзывы никуда не публикуются. Их видит только клиника. Как только рейтинг
--    виден снаружи, начинается модерация, клевета и конкурент, заливающий
--    единицами. Клиника при этом получает главное: качество сервиса по
--    конкретным кабинетам и людям.
--
-- 3. Форма отзыва не спрашивает о здоровье. Только сервис. Мы сознательно не
--    собираем медицинские данные пациентов: это и юридически чище, и полезнее
--    менеджеру, который очередью и вежливостью управлять может, а диагнозом нет.

-- Роль сотрудника: понадобится в модуле обучения, где он входит в портал
-- и видит только свои назначения. Добавляем сейчас, чтобы не менять enum
-- посреди следующей миграции.
ALTER TYPE product.user_role ADD VALUE IF NOT EXISTS 'employee';

-- ══ Сотрудники ════════════════════════════════════════════════════════════════
DO $$ BEGIN
    CREATE TYPE product.employment_status AS ENUM
        ('onboarding','active','suspended','dismissed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS product.employees (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id         uuid NOT NULL REFERENCES product.clinics(id) ON DELETE CASCADE,
    unit_id           uuid REFERENCES product.clinic_units(id) ON DELETE SET NULL,
    staff_position_id uuid REFERENCES product.staff_positions(id) ON DELETE SET NULL,

    -- Вход в портал выдаётся не всем и не сразу: сначала человек в штате,
    -- потом при необходимости учётная запись для обучения.
    user_id           bigint UNIQUE REFERENCES product.users(id) ON DELETE SET NULL,

    -- Если сотрудник пришёл через наш найм — сохраняем связь. Она нужна, чтобы
    -- считать, сколько наймов реально закрылось, а не сколько контактов открыли.
    candidate_id      uuid REFERENCES product.candidate_profiles(id) ON DELETE SET NULL,

    full_name         text NOT NULL,
    role_category     text REFERENCES product.role_categories(code),
    specialty         text REFERENCES product.specialties(code),

    -- Рабочие контакты сотрудника. Это HR-данные клиники, которые у неё и так
    -- есть, и они не имеют отношения к защищённым контактам кандидатов:
    -- те закрыты, потому что кандидат ещё не согласился их раскрыть.
    work_phone        text,
    work_email        text,

    hired_at          date,
    dismissed_at      date,
    status            product.employment_status NOT NULL DEFAULT 'active',
    note              text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_employees_dismissal CHECK (
        (status = 'dismissed') = (dismissed_at IS NOT NULL)
    ),
    CONSTRAINT ck_employees_dates CHECK (
        dismissed_at IS NULL OR hired_at IS NULL OR dismissed_at >= hired_at
    )
);

COMMENT ON TABLE product.employees IS
  'Работающий человек. Отличается и от candidate_profiles (ищет работу), '
  'и от clinic_members (доступ в кабинет).';

CREATE INDEX IF NOT EXISTS ix_employees_clinic ON product.employees (clinic_id, status);
CREATE INDEX IF NOT EXISTS ix_employees_unit ON product.employees (unit_id)
    WHERE status <> 'dismissed';
CREATE INDEX IF NOT EXISTS ix_employees_position ON product.employees (staff_position_id)
    WHERE status <> 'dismissed';

-- ── Занятость ставок считается сама ───────────────────────────────────────────
-- Раньше seats_filled правили руками, и он неизбежно расходился с реальностью.
-- Теперь это производная от числа работающих людей на единице.
CREATE OR REPLACE FUNCTION product.recount_seats(p_position_id uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
BEGIN
    IF p_position_id IS NULL THEN RETURN; END IF;
    UPDATE product.staff_positions sp
       SET seats_filled = LEAST(
               sp.seats,
               (SELECT count(*) FROM product.employees e
                 WHERE e.staff_position_id = p_position_id
                   AND e.status IN ('onboarding','active','suspended'))
           )
     WHERE sp.id = p_position_id;
END $$;

CREATE OR REPLACE FUNCTION product.tg_employees_recount() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        PERFORM product.recount_seats(OLD.staff_position_id);
    END IF;
    IF TG_OP <> 'DELETE' THEN
        PERFORM product.recount_seats(NEW.staff_position_id);
    END IF;
    RETURN NULL;
END $$;

DROP TRIGGER IF EXISTS tr_employees_recount ON product.employees;
CREATE TRIGGER tr_employees_recount
    AFTER INSERT OR UPDATE OF staff_position_id, status OR DELETE ON product.employees
    FOR EACH ROW EXECUTE FUNCTION product.tg_employees_recount();

DROP TRIGGER IF EXISTS tr_employees_touch ON product.employees;
CREATE TRIGGER tr_employees_touch BEFORE UPDATE ON product.employees
    FOR EACH ROW EXECUTE FUNCTION core.tg_touch_updated_at();

-- ══ Словарь того, что спрашиваем у пациента ═══════════════════════════════════
-- Закрытый список намеренно: свободные теги превратились бы в свалку, а
-- главное — фиксированный набор гарантирует, что мы не спросим о здоровье.
CREATE TABLE IF NOT EXISTS product.review_tags (
    code    text PRIMARY KEY,
    name_ru text NOT NULL,
    name_uz text NOT NULL,
    sort    smallint NOT NULL DEFAULT 100
);

INSERT INTO product.review_tags (code, name_ru, name_uz, sort) VALUES
    ('politeness',  'Вежливость персонала',        'Xodimlarning xushmuomalaligi',   10),
    ('waiting',     'Время ожидания',              'Kutish vaqti',                   20),
    ('cleanliness', 'Чистота',                     'Tozalik',                        30),
    ('clarity',     'Понятно объяснили назначения','Tayinlovlar tushunarli tushuntirildi', 40),
    ('navigation',  'Легко нашёл кабинет',         'Xonani topish oson bo‘ldi',      50),
    ('price_info',  'Заранее понятна стоимость',   'Narx oldindan tushunarli',       60)
ON CONFLICT (code) DO NOTHING;

COMMENT ON TABLE product.review_tags IS
  'Аспекты сервиса. Про здоровье, диагноз и лечение не спрашиваем — ни здесь, '
  'ни в свободном комментарии это не предполагается.';

-- ══ Цели отзыва и QR ══════════════════════════════════════════════════════════
DO $$ BEGIN
    CREATE TYPE product.review_target_kind AS ENUM ('unit','employee');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Короткий публичный код, который зашит в QR. Не секрет — он висит на стене, —
-- но и не перечисляемый: 12 символов из безопасного алфавита без похожих
-- глифов, чтобы код можно было прочитать вслух и набрать руками.
CREATE OR REPLACE FUNCTION product.gen_review_slug()
RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
    alphabet constant text := '23456789abcdefghjkmnpqrstuvwxyz';
    result text := '';
BEGIN
    FOR _ IN 1..12 LOOP
        result := result || substr(alphabet, 1 + floor(random() * length(alphabet))::int, 1);
    END LOOP;
    RETURN result;
END $$;

CREATE TABLE IF NOT EXISTS product.review_targets (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id   uuid NOT NULL REFERENCES product.clinics(id) ON DELETE CASCADE,
    kind        product.review_target_kind NOT NULL,
    unit_id     uuid REFERENCES product.clinic_units(id) ON DELETE CASCADE,
    employee_id uuid REFERENCES product.employees(id) ON DELETE CASCADE,

    -- Код стабилен на весь срок жизни: QR печатают один раз и клеят на стену.
    -- «Динамически» относится к картинке, которая рисуется по запросу, а не
    -- к коду внутри неё.
    slug        text NOT NULL UNIQUE DEFAULT product.gen_review_slug(),

    -- Что увидит пациент: «Ортодонтия, 2 этаж» или «Врач Ахмедова А.».
    title       text NOT NULL,
    subtitle    text,
    is_active   boolean NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_review_target_shape CHECK (
        (kind = 'unit'     AND unit_id IS NOT NULL AND employee_id IS NULL) OR
        (kind = 'employee' AND employee_id IS NOT NULL AND unit_id IS NULL)
    )
);

COMMENT ON COLUMN product.review_targets.slug IS
  'Код в QR. Печатается один раз, поэтому неизменяем. Отключить цель можно '
  'через is_active — тогда страница скажет, что опрос закрыт.';

CREATE INDEX IF NOT EXISTS ix_review_targets_clinic ON product.review_targets (clinic_id, kind);
CREATE UNIQUE INDEX IF NOT EXISTS uq_review_target_unit
    ON product.review_targets (unit_id) WHERE kind = 'unit';
CREATE UNIQUE INDEX IF NOT EXISTS uq_review_target_employee
    ON product.review_targets (employee_id) WHERE kind = 'employee';

DROP TRIGGER IF EXISTS tr_review_targets_touch ON product.review_targets;
CREATE TRIGGER tr_review_targets_touch BEFORE UPDATE ON product.review_targets
    FOR EACH ROW EXECUTE FUNCTION core.tg_touch_updated_at();

-- ══ Отзывы ════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS product.reviews (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id      uuid NOT NULL REFERENCES product.clinics(id) ON DELETE CASCADE,
    target_id      uuid NOT NULL REFERENCES product.review_targets(id) ON DELETE CASCADE,

    rating         smallint NOT NULL,
    good_tags      text[] NOT NULL DEFAULT '{}',   -- что понравилось
    bad_tags       text[] NOT NULL DEFAULT '{}',   -- что стоит улучшить
    comment        text,

    -- Контакт только если пациент сам захотел, чтобы с ним связались.
    -- По умолчанию отзыв полностью анонимен.
    contact_phone  text,
    wants_callback boolean NOT NULL DEFAULT false,

    locale         text NOT NULL DEFAULT 'ru',
    -- Храним хэш адреса, а не адрес: для лимитов этого достаточно, а лишних
    -- персональных данных о пациенте у нас не появляется.
    ip_hash        text,
    is_flagged     boolean NOT NULL DEFAULT false,
    handled_at     timestamptz,
    handled_by     bigint REFERENCES product.users(id),
    created_at     timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_reviews_rating CHECK (rating BETWEEN 1 AND 5),
    CONSTRAINT ck_reviews_comment_len CHECK (comment IS NULL OR length(comment) <= 2000),
    CONSTRAINT ck_reviews_callback CHECK (
        NOT wants_callback OR contact_phone IS NOT NULL
    )
);

COMMENT ON TABLE product.reviews IS
  'Оценки пациентов. Видны только клинике: публичных рейтингов на пилоте нет.';

CREATE INDEX IF NOT EXISTS ix_reviews_clinic ON product.reviews (clinic_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_reviews_target ON product.reviews (target_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_reviews_low ON product.reviews (clinic_id, created_at DESC)
    WHERE rating <= 2;
-- Лимит «один отзыв с адреса на цель в час» проверяется по этому индексу.
CREATE INDEX IF NOT EXISTS ix_reviews_ratelimit ON product.reviews (target_id, ip_hash, created_at DESC);
