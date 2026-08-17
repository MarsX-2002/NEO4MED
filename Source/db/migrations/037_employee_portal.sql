-- 037_employee_portal.sql
-- Портал сотрудника: свои курсы, свои отзывы.
--
-- Схема обучения (021) и ролевые политики (022) появились раньше, чем портал.
-- Когда я стал подключать к ним питон, выяснилось три вещи, которые надо
-- закрыть в БАЗЕ, а не в роутах:
--
--   1. Функции попытки (attempt_questions, grade_attempt, attempt_review) —
--      SECURITY DEFINER БЕЗ проверки владельца. Достаточно знать uuid чужой
--      попытки, чтобы прочитать её вопросы, сдать её за человека или увидеть
--      разбор. uuid не угадывается, но «спасает неперечислимость» — это не
--      гарантия, а отсрочка. Тот же класс дыры, что уже записан в долги по
--      attach_to_review.
--   2. course_answer_key отдавала правильные ответы любому в контексте
--      клиники, то есть и сотруднику. Смысл закрытой колонки is_correct
--      обнулялся одной функцией.
--   3. Сотрудник не может сам двинуть своё назначение в in_progress:
--      p_course_assignments_scope разрешает запись только менеджеру. Это
--      правильно (иначе он поставил бы себе passed), но значит, что начало
--      попытки обязано идти через функцию.
--
-- Плюс новое: сотрудник должен видеть отзывы пациентов о себе, а
-- product.reviews закрыта для его роли целиком. Отдаём узкой функцией — так
-- же, как своя карточка отдаётся через my_employee_card.

-- ══ Свои отзывы ═══════════════════════════════════════════════════════════════
-- Телефон пациента и флаг «перезвоните мне» сюда НЕ попадают. Обратный звонок —
-- работа менеджера, а сотруднику для обратной связи нужна оценка и текст.
-- Вложения тоже не отдаём: фотография, снятая пациентом в клинике, адресована
-- руководству, а не тому, о ком отзыв.
CREATE OR REPLACE FUNCTION product.my_reviews(p_limit integer DEFAULT 100)
RETURNS TABLE (
    review_id  uuid,
    rating     smallint,
    good_tags  text[],
    bad_tags   text[],
    comment    text,
    locale     text,
    handled_at timestamptz,
    created_at timestamptz
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
    SELECT r.id, r.rating, r.good_tags, r.bad_tags, r.comment,
           r.locale, r.handled_at, r.created_at
    FROM product.reviews r
    JOIN product.review_targets rt ON rt.id = r.target_id AND rt.kind = 'employee'
    JOIN product.employees e ON e.id = rt.employee_id
    WHERE e.user_id = product.current_user_id()
      AND e.clinic_id = product.current_clinic_id()
    ORDER BY r.created_at DESC
    LIMIT LEAST(coalesce(p_limit, 100), 500)
$$;

COMMENT ON FUNCTION product.my_reviews IS
  'Отзывы пациентов о самом себе. product.reviews закрыта для роли employee '
  'политикой, поэтому это единственная дверь. Телефон пациента и вложения не '
  'отдаются: они адресованы руководству клиники.';

-- Средние по себе считаем тут же: иначе портал сложил бы их из выданных строк
-- и цифра поехала бы при первом же LIMIT.
CREATE OR REPLACE FUNCTION product.my_review_stats()
RETURNS TABLE (
    total     bigint,
    last_week bigint,
    avg_rating numeric,
    low       bigint
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
    SELECT count(*),
           count(*) FILTER (WHERE r.created_at > now() - interval '7 days'),
           round(avg(r.rating), 2),
           count(*) FILTER (WHERE r.rating <= 2)
    FROM product.reviews r
    JOIN product.review_targets rt ON rt.id = r.target_id AND rt.kind = 'employee'
    JOIN product.employees e ON e.id = rt.employee_id
    WHERE e.user_id = product.current_user_id()
      AND e.clinic_id = product.current_clinic_id()
$$;

-- ══ Начало попытки ════════════════════════════════════════════════════════════
-- Идемпотентна по образцу open_interview: незавершённая попытка возвращается
-- та же самая. Иначе перезагрузка страницы посреди теста плодила бы попытки,
-- и «сколько раз он сдавал» перестало бы что-то значить.
CREATE OR REPLACE FUNCTION product.start_course_attempt(p_course_id uuid)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE
    v_employee uuid;
    v_clinic   uuid;
    v_asg      uuid;
    v_status   product.assignment_status;
    v_attempt  uuid;
    v_questions integer;
BEGIN
    SELECT e.id, e.clinic_id INTO v_employee, v_clinic
      FROM product.employees e
     WHERE e.user_id = product.current_user_id()
       AND e.clinic_id = product.current_clinic_id();
    IF v_employee IS NULL THEN
        RAISE EXCEPTION 'у пользователя нет карточки сотрудника'
            USING ERRCODE = 'insufficient_privilege';
    END IF;

    -- Курс должен быть назначен именно этому человеку. Права на прохождение
    -- даёт назначение, а не наличие курса в клинике.
    SELECT asg.id, asg.status INTO v_asg, v_status
      FROM product.course_assignments asg
      JOIN product.courses c ON c.id = asg.course_id
     WHERE asg.course_id = p_course_id
       AND asg.employee_id = v_employee
       AND c.status = 'published'
     FOR UPDATE OF asg;
    IF v_asg IS NULL THEN
        RAISE EXCEPTION 'курс не назначен' USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT count(*) INTO v_questions
      FROM product.course_questions q WHERE q.course_id = p_course_id;
    IF v_questions = 0 THEN
        RAISE EXCEPTION 'в курсе нет вопросов' USING ERRCODE = 'check_violation';
    END IF;

    SELECT at.id INTO v_attempt
      FROM product.course_attempts at
     WHERE at.assignment_id = v_asg AND at.finished_at IS NULL
     ORDER BY at.started_at DESC
     LIMIT 1;

    IF v_attempt IS NULL THEN
        INSERT INTO product.course_attempts (clinic_id, assignment_id, employee_id)
        VALUES (v_clinic, v_asg, v_employee)
        RETURNING id INTO v_attempt;
    END IF;

    -- Статус двигаем только вперёд от 'assigned': у сдавшего курса пересдача
    -- не должна стирать пройдено.
    IF v_status = 'assigned' THEN
        UPDATE product.course_assignments SET status = 'in_progress' WHERE id = v_asg;
    END IF;

    RETURN v_attempt;
END $$;

COMMENT ON FUNCTION product.start_course_attempt IS
  'Начинает или возвращает незавершённую попытку по назначенному курсу. '
  'Через функцию, потому что сотруднику запись в course_assignments запрещена '
  'политикой — и это правильно: иначе он поставил бы себе passed сам.';

-- ══ Владелец попытки ══════════════════════════════════════════════════════════
-- Вопросы попытки: та же выдача, что в 021, плюс проверка, чья попытка.
-- Менеджеру смотреть вопросы через эту функцию тоже можно: он их и написал.
CREATE OR REPLACE FUNCTION product.attempt_questions(p_attempt_id uuid)
RETURNS TABLE (
    question_id uuid,
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
      AND a.clinic_id = product.current_clinic_id()
      AND (
          product.is_manager()
          OR a.employee_id IN (
              SELECT e.id FROM product.employees e
               WHERE e.user_id = product.current_user_id()
          )
      )
    ORDER BY q.position
$$;

COMMENT ON FUNCTION product.attempt_questions IS
  'Вопросы попытки без правильных ответов и с перемешанными вариантами. '
  'Чужую попытку не отдаёт: до 037 хватало знать её uuid.';

-- Проверка ответов. Сдавать может ТОЛЬКО сам сотрудник: менеджер, ставящий
-- балл за человека, — это не проверка знаний, а подделка журнала обучения.
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
    IF a.clinic_id <> product.current_clinic_id()
       OR NOT EXISTS (
           SELECT 1 FROM product.employees e
            WHERE e.id = a.employee_id AND e.user_id = product.current_user_id()
       ) THEN
        RAISE EXCEPTION 'это не ваша попытка' USING ERRCODE = 'insufficient_privilege';
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

    UPDATE product.course_assignments asg
       SET best_score = GREATEST(coalesce(asg.best_score, 0), v_score),
           -- Провал после сдачи не отменяет сдачу: пересдача «на интерес»
           -- не должна отправлять человека обратно в список должников.
           status = CASE WHEN v_passed OR asg.status = 'passed'
                         THEN 'passed'::product.assignment_status
                         ELSE 'failed'::product.assignment_status END,
           completed_at = CASE WHEN v_passed THEN coalesce(asg.completed_at, now())
                              ELSE asg.completed_at END
     WHERE asg.id = a.assignment_id;

    RETURN QUERY SELECT v_score, v_correct, v_total, v_passed;
END $$;

COMMENT ON FUNCTION product.grade_attempt IS
  'Проверяет ответы на стороне БД: прикладная роль не может прочитать '
  'is_correct, поэтому иначе проверить и невозможно — так и задумано. '
  'Сдать чужую попытку нельзя (037).';

-- Разбор попытки. Здесь менеджер допущен: он должен видеть, на чём валятся
-- люди, — это сигнал о плохом материале, а не о плохих сотрудниках.
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
    WHERE a.id = p_attempt_id
      AND a.finished_at IS NOT NULL
      AND a.clinic_id = product.current_clinic_id()
      AND (
          product.is_manager()
          OR a.employee_id IN (
              SELECT e.id FROM product.employees e
               WHERE e.user_id = product.current_user_id()
          )
      )
    ORDER BY q.position
$$;

-- ══ Ключ ответов — только менеджеру ═══════════════════════════════════════════
-- Была ровно та дыра, от которой закрывали колонку: SECURITY DEFINER функция
-- отдавала is_correct любому в контексте клиники, включая обучающегося.
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
      AND product.is_manager()
$$;

COMMENT ON FUNCTION product.course_answer_key IS
  'Правильные ответы для редактирования курса. Только менеджеру: без этой '
  'проверки (до 037) сотрудник получал ключ к тесту, который сдаёт.';

GRANT EXECUTE ON FUNCTION
      product.my_reviews(integer),
      product.my_review_stats(),
      product.start_course_attempt(uuid)
      TO ishmed_app;
