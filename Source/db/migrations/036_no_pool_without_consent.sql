-- 036_no_pool_without_consent.sql
-- Собеседование не переводит человека в общий поиск.
--
-- Отмена одного решения из 034. Там `apply_interview_extraction` переводила
-- профиль из `draft` в `active` после интервью — казалось разумным: человек
-- прошёл собеседование, значит профиль готов.
--
-- Показал тест изоляции тенантов: он считает активные профили, видимые клинике,
-- и вместо одного увидел шесть. Причина в политике p_candidates_own, которая
-- существует с 013: активный профиль виден ЛЮБОЙ клинике в контексте тенанта.
-- То есть человек, откликнувшийся в одну клинику, автоматически попадал в
-- каталог для всех остальных. Он на это не соглашался: в тексте согласия речь
-- о том, что карточку видит клиника, а не о том, что его выложат в общий пул.
--
-- Для менеджера смена статуса ничего и не давала: видимость своих откликов
-- обеспечивает отдельная ветка политики, добавленная в той же 034. Так что
-- убираем перевод статуса и ничего не теряем.
--
-- Когда появится матчинг, публикация профиля в пул станет отдельным осознанным
-- действием человека — с кнопкой и понятной формулировкой, а не побочным
-- эффектом собеседования.

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

    -- Статус не трогаем. Профиль остаётся черновиком, и это не недоделка:
    -- черновик виден клинике, к которой человек откликнулся, и никому больше.
    UPDATE product.candidate_profiles c
       SET experience_months = COALESCE(v_months, c.experience_months),
           salary_min_uzs    = COALESCE(v_salary, c.salary_min_uzs),
           skills            = COALESCE(v_skills, c.skills),
           languages         = COALESCE(v_langs, c.languages),
           extraction        = v_data,
           source            = 'text'
     WHERE c.id = v_candidate;

    RETURN v_candidate;
END $$;

COMMENT ON FUNCTION product.apply_interview_extraction IS
  'Переносит interviews.extraction в поля профиля кандидата. Статус профиля '
  'не меняет: попадание в общий каталог клиник — отдельное решение человека, '
  'а не следствие собеседования. График и специальность не трогает, там коды '
  'словарей, а модель отдаёт свободный текст.';

-- Возвращаем в черновики тех, кого 034 успела перевести в active: они не
-- просили выкладывать их карточку всем клиникам. Условие узкое — только те,
-- у кого профиль создан откликом и заполнен из интервью.
UPDATE product.candidate_profiles c
   SET status = 'draft'
 WHERE c.status = 'active'
   AND c.extraction IS NOT NULL
   AND EXISTS (
       SELECT 1 FROM product.applications a
        JOIN product.interviews i ON i.application_id = a.id
       WHERE a.candidate_id = c.id AND i.extraction IS NOT NULL
   );
