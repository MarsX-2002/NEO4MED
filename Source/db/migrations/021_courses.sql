-- 021_courses.sql
-- Обучение: курс с материалом и тестом в конце.
--
-- Главное решение схемы — правильные ответы не должны попадать к сотруднику.
-- Если просто отдать вопросы с вариантами, флаг is_correct уедет в браузер
-- вместе с JSON, и тест превратится в формальность. Поэтому:
--   * у прикладной роли отозвано право читать колонку is_correct;
--   * вопросы для прохождения отдаёт функция, которая её не возвращает;
--   * проверка ответов идёт в SECURITY DEFINER функции на стороне БД.
-- Это не паранойя: «мы не забудем убрать поле в сериализаторе» — обещание,
-- а отозванный GRANT — гарантия.

DO $$ BEGIN
    CREATE TYPE product.course_status AS ENUM ('draft', 'published', 'archived');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE product.assignment_status AS ENUM ('assigned', 'in_progress', 'passed', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ══ Курс ══════════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS product.courses (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id     uuid NOT NULL REFERENCES product.clinics(id) ON DELETE CASCADE,
    title         text NOT NULL,
    summary       text,

    -- Для кого курс. Пусто — значит для всех: правила внутреннего распорядка
    -- касаются и медсестры, и рентген-лаборанта.
    role_category text REFERENCES product.role_categories(code),
    specialty     text REFERENCES product.specialties(code),

    -- Порог прохождения в процентах. Настраиваемый, потому что «правила
    -- обработки инструментов» и «как здороваться с пациентом» — не одна цена
    -- ошибки.
    pass_score    smallint NOT NULL DEFAULT 80,
    status        product.course_status NOT NULL DEFAULT 'draft',
    created_by    bigint REFERENCES product.users(id),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_courses_pass_score CHECK (pass_score BETWEEN 1 AND 100)
);

CREATE INDEX IF NOT EXISTS ix_courses_clinic ON product.courses (clinic_id, status);

-- ══ Материал ══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS product.course_lessons (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id  uuid NOT NULL REFERENCES product.courses(id) ON DELETE CASCADE,
    clinic_id  uuid NOT NULL REFERENCES product.clinics(id) ON DELETE CASCADE,
    position   smallint NOT NULL DEFAULT 1,
    title      text NOT NULL,
    content    text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (course_id, position)
);

-- ══ Вопросы теста ═════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS product.course_questions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id   uuid NOT NULL REFERENCES product.courses(id) ON DELETE CASCADE,
    clinic_id   uuid NOT NULL REFERENCES product.clinics(id) ON DELETE CASCADE,
    position    smallint NOT NULL DEFAULT 1,
    text        text NOT NULL,
    -- Пояснение показываем ПОСЛЕ попытки: тест не только проверяет, но и учит.
    explanation text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (course_id, position)
);

CREATE TABLE IF NOT EXISTS product.course_options (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id uuid NOT NULL REFERENCES product.course_questions(id) ON DELETE CASCADE,
    clinic_id   uuid NOT NULL REFERENCES product.clinics(id) ON DELETE CASCADE,
    position    smallint NOT NULL DEFAULT 1,
    text        text NOT NULL,
    is_correct  boolean NOT NULL DEFAULT false,
    UNIQUE (question_id, position)
);

COMMENT ON COLUMN product.course_options.is_correct IS
  'Правильность варианта. Прикладной роли читать эту колонку ЗАПРЕЩЕНО: '
  'иначе ответы уедут в браузер сотрудника. Проверку делает product.grade_attempt.';

-- ══ Назначения ════════════════════════════════════════════════════════════════
-- Назначение материализуем на человека, а не храним «курс для всех медсестёр»
-- вычисляемым: иначе нельзя ответить на вопрос «кто именно не прошёл», а это
-- и есть то, зачем менеджеру раздел.
CREATE TABLE IF NOT EXISTS product.course_assignments (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id   uuid NOT NULL REFERENCES product.clinics(id) ON DELETE CASCADE,
    course_id   uuid NOT NULL REFERENCES product.courses(id) ON DELETE CASCADE,
    employee_id uuid NOT NULL REFERENCES product.employees(id) ON DELETE CASCADE,
    status      product.assignment_status NOT NULL DEFAULT 'assigned',
    due_at      date,
    assigned_by bigint REFERENCES product.users(id),
    assigned_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    best_score  smallint,
    UNIQUE (course_id, employee_id)
);

CREATE INDEX IF NOT EXISTS ix_assignments_employee
    ON product.course_assignments (employee_id, status);
CREATE INDEX IF NOT EXISTS ix_assignments_course
    ON product.course_assignments (course_id, status);

-- ══ Попытки ═══════════════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS product.course_attempts (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clinic_id     uuid NOT NULL REFERENCES product.clinics(id) ON DELETE CASCADE,
    assignment_id uuid NOT NULL REFERENCES product.course_assignments(id) ON DELETE CASCADE,
    employee_id   uuid NOT NULL REFERENCES product.employees(id) ON DELETE CASCADE,

    -- Что человек ответил. Храним, чтобы можно было разобрать спорную попытку
    -- и увидеть, какой вопрос валит всех — это сигнал о плохом материале,
    -- а не о плохих сотрудниках.
    answers       jsonb NOT NULL DEFAULT '{}',
    score         smallint,
    correct_count smallint,
    total_count   smallint,
    passed        boolean,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,

    CONSTRAINT ck_attempts_score CHECK (score IS NULL OR score BETWEEN 0 AND 100)
);

CREATE INDEX IF NOT EXISTS ix_attempts_assignment
    ON product.course_attempts (assignment_id, started_at DESC);

-- ══ Триггеры ══════════════════════════════════════════════════════════════════
DROP TRIGGER IF EXISTS tr_courses_touch ON product.courses;
CREATE TRIGGER tr_courses_touch BEFORE UPDATE ON product.courses
    FOR EACH ROW EXECUTE FUNCTION core.tg_touch_updated_at();

-- ══ Владелец и RLS ════════════════════════════════════════════════════════════
DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['courses','course_lessons','course_questions','course_options',
                             'course_assignments','course_attempts']
    LOOP
        EXECUTE format('ALTER TABLE product.%I OWNER TO ezgumed', t);
        EXECUTE format('ALTER TABLE product.%I ENABLE ROW LEVEL SECURITY', t);
        -- DROP перед CREATE: миграция должна переживать повторный прогон,
        -- если предыдущий упал на середине файла.
        EXECUTE format('DROP POLICY IF EXISTS p_%1$s_tenant ON product.%1$s', t);
        EXECUTE format(
            'CREATE POLICY p_%1$s_tenant ON product.%1$s '
            'USING (clinic_id = product.current_clinic_id()) '
            'WITH CHECK (clinic_id = product.current_clinic_id())', t);
    END LOOP;
END $$;

-- ══ Права: колонка с ответами закрыта ═════════════════════════════════════════
GRANT SELECT, INSERT, UPDATE, DELETE ON
      product.courses, product.course_lessons, product.course_questions,
      product.course_assignments TO ishmed_app;
GRANT SELECT, INSERT, UPDATE ON product.course_attempts TO ishmed_app;

-- Варианты ответов: полный доступ, КРОМЕ чтения is_correct.
-- Менеджеру правильные ответы нужны — он их и задаёт, — но приложение получает
-- их отдельным путём: через функцию, отдающую только для редактирования курса
-- владельцем клиники. Для прохождения теста колонка недоступна вовсе.
GRANT INSERT, UPDATE, DELETE ON product.course_options TO ishmed_app;
GRANT SELECT (id, question_id, clinic_id, position, text) ON product.course_options TO ishmed_app;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA product TO ishmed_app;

-- ══ Выдача вопросов для прохождения ═══════════════════════════════════════════
-- Без правильных ответов. Порядок вариантов перемешиваем на основе id попытки,
-- чтобы «правильный всегда первый» не стало стратегией сдачи.
CREATE OR REPLACE FUNCTION product.attempt_questions(p_attempt_id uuid)
RETURNS TABLE (
    question_id uuid,
    -- Не «position»: это слово зарезервировано и в объявлении RETURNS TABLE
    -- Postgres его не принимает.
    ord         smallint,
    text        text,
    options     jsonb
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
    SELECT q.id, q.position, q.text,
           (SELECT jsonb_agg(jsonb_build_object('id', o.id, 'text', o.text)
                             ORDER BY md5(o.id::text || p_attempt_id::text))
              FROM product.course_options o WHERE o.question_id = q.id) AS options
    FROM product.course_attempts a
    JOIN product.course_assignments asg ON asg.id = a.assignment_id
    JOIN product.course_questions q ON q.course_id = asg.course_id
    WHERE a.id = p_attempt_id
    ORDER BY q.position
$$;

-- ══ Проверка попытки ══════════════════════════════════════════════════════════
-- Считает балл на стороне БД. answers: {"<question_id>": "<option_id>"}.
CREATE OR REPLACE FUNCTION product.grade_attempt(
    p_attempt_id uuid,
    p_answers    jsonb
)
RETURNS TABLE (score smallint, correct_count smallint, total_count smallint, passed boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE
    a          record;
    v_total    smallint;
    v_correct  smallint;
    v_score    smallint;
    v_pass     smallint;
    v_passed   boolean;
BEGIN
    SELECT at.*, asg.course_id, c.pass_score
      INTO a
      FROM product.course_attempts at
      JOIN product.course_assignments asg ON asg.id = at.assignment_id
      JOIN product.courses c ON c.id = asg.course_id
     WHERE at.id = p_attempt_id
     FOR UPDATE OF at;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'попытка не найдена' USING ERRCODE = 'no_data_found';
    END IF;
    IF a.finished_at IS NOT NULL THEN
        RAISE EXCEPTION 'попытка уже завершена' USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT count(*) INTO v_total
      FROM product.course_questions q WHERE q.course_id = a.course_id;
    IF v_total = 0 THEN
        RAISE EXCEPTION 'в курсе нет вопросов' USING ERRCODE = 'check_violation';
    END IF;

    SELECT count(*) INTO v_correct
      FROM product.course_questions q
      JOIN product.course_options o ON o.question_id = q.id AND o.is_correct
     WHERE q.course_id = a.course_id
       AND p_answers ->> q.id::text = o.id::text;

    v_score := round(v_correct::numeric * 100 / v_total);
    v_pass  := a.pass_score;
    v_passed := v_score >= v_pass;

    UPDATE product.course_attempts
       SET answers = p_answers, score = v_score, correct_count = v_correct,
           total_count = v_total, passed = v_passed, finished_at = now()
     WHERE id = p_attempt_id;

    -- Лучший результат и статус назначения обновляем здесь же: два места,
    -- которые считают одно и то же, однажды разойдутся.
    UPDATE product.course_assignments asg
       SET best_score = GREATEST(coalesce(asg.best_score, 0), v_score),
           status = CASE WHEN v_passed THEN 'passed'::product.assignment_status
                         ELSE 'failed'::product.assignment_status END,
           completed_at = CASE WHEN v_passed THEN now() ELSE asg.completed_at END
     WHERE asg.id = a.assignment_id;

    RETURN QUERY SELECT v_score, v_correct, v_total, v_passed;
END $$;

COMMENT ON FUNCTION product.grade_attempt IS
  'Проверяет ответы на стороне БД. Прикладная роль не может прочитать '
  'is_correct, поэтому иначе проверить и невозможно — так и задумано.';

-- Разбор попытки после сдачи: что было верно и почему. Показываем только
-- завершённую попытку — иначе это способ подсмотреть ответы.
CREATE OR REPLACE FUNCTION product.attempt_review(p_attempt_id uuid)
RETURNS TABLE (
    question_id    uuid,
    ord            smallint,
    text           text,
    explanation    text,
    chosen_id      uuid,
    correct_id     uuid,
    is_right       boolean
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
    SELECT q.id, q.position, q.text, q.explanation,
           (a.answers ->> q.id::text)::uuid AS chosen_id,
           co.id AS correct_id,
           (a.answers ->> q.id::text) = co.id::text AS is_right
    FROM product.course_attempts a
    JOIN product.course_assignments asg ON asg.id = a.assignment_id
    JOIN product.course_questions q ON q.course_id = asg.course_id
    LEFT JOIN product.course_options co ON co.question_id = q.id AND co.is_correct
    WHERE a.id = p_attempt_id AND a.finished_at IS NOT NULL
    ORDER BY q.position
$$;

-- Правильные ответы для РЕДАКТИРОВАНИЯ курса менеджером. Отдельная функция,
-- потому что колонка закрыта для прикладной роли целиком.
CREATE OR REPLACE FUNCTION product.course_answer_key(p_course_id uuid)
RETURNS TABLE (question_id uuid, option_id uuid, is_correct boolean)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
    SELECT o.question_id, o.id, o.is_correct
    FROM product.course_options o
    JOIN product.course_questions q ON q.id = o.question_id
    WHERE q.course_id = p_course_id
      AND q.clinic_id = product.current_clinic_id()
$$;

GRANT EXECUTE ON FUNCTION
      product.attempt_questions(uuid),
      product.grade_attempt(uuid, jsonb),
      product.attempt_review(uuid),
      product.course_answer_key(uuid)
      TO ishmed_app;
