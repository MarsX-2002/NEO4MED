-- 017_reviews_security.sql
-- Публичная страница отзыва и защита от заливки.
--
-- Задача с двух сторон:
--   * пациент приходит по QR БЕЗ регистрации, значит контекста тенанта нет,
--     а RLS без контекста не показывает ничего — та же ситуация, что была
--     при входе клиники;
--   * форма на публичном адресе, напечатанном на стене, — приглашение для
--     спама и для конкурента.
--
-- Оба вопроса решает одна пара SECURITY DEFINER функций: они отвечают ровно
-- на два вопроса («что это за цель» и «принять отзыв») и не дают ничего больше.
-- Прикладная роль при этом остаётся без прямого доступа к чужим тенантам.

-- ══ Владелец, RLS, права ══════════════════════════════════════════════════════
ALTER TABLE product.employees      OWNER TO ezgumed;
ALTER TABLE product.review_targets OWNER TO ezgumed;
ALTER TABLE product.reviews        OWNER TO ezgumed;
ALTER TABLE product.review_tags    OWNER TO ezgumed;

ALTER TABLE product.employees      ENABLE ROW LEVEL SECURITY;
ALTER TABLE product.review_targets ENABLE ROW LEVEL SECURITY;
ALTER TABLE product.reviews        ENABLE ROW LEVEL SECURITY;

CREATE POLICY p_employees_tenant ON product.employees
    USING (clinic_id = product.current_clinic_id())
    WITH CHECK (clinic_id = product.current_clinic_id());

CREATE POLICY p_review_targets_tenant ON product.review_targets
    USING (clinic_id = product.current_clinic_id())
    WITH CHECK (clinic_id = product.current_clinic_id());

CREATE POLICY p_reviews_tenant ON product.reviews
    USING (clinic_id = product.current_clinic_id())
    WITH CHECK (clinic_id = product.current_clinic_id());

GRANT SELECT ON product.review_tags TO ishmed_app;
GRANT SELECT, INSERT, UPDATE ON product.employees, product.review_targets TO ishmed_app;
-- Отзывы приложение читает и помечает обработанными, но не создаёт напрямую:
-- вставка идёт через функцию, где проверяются лимиты.
GRANT SELECT, UPDATE ON product.reviews TO ishmed_app;
GRANT DELETE ON product.employees TO ishmed_app;

-- ══ Что видит пациент ═════════════════════════════════════════════════════════
CREATE OR REPLACE FUNCTION product.public_review_target(p_slug text)
RETURNS TABLE (
    target_id   uuid,
    title       text,
    subtitle    text,
    clinic_name text,
    is_active   boolean
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
    SELECT rt.id, rt.title, rt.subtitle, c.name, rt.is_active
    FROM product.review_targets rt
    JOIN product.clinics c ON c.id = rt.clinic_id
    WHERE rt.slug = p_slug
$$;

COMMENT ON FUNCTION product.public_review_target IS
  'Минимум для публичной страницы: что оцениваем и чья это клиника. '
  'Ни сотрудников, ни отзывов, ни идентификаторов тенанта наружу не отдаёт.';

-- ══ Приём отзыва ══════════════════════════════════════════════════════════════
-- Лимиты внутри функции, а не в приложении: страница публичная, и обойти
-- проверку, которая живёт в питоне, проще, чем ту, что в базе.
CREATE OR REPLACE FUNCTION product.submit_review(
    p_slug           text,
    p_rating         smallint,
    p_good_tags      text[]  DEFAULT '{}',
    p_bad_tags       text[]  DEFAULT '{}',
    p_comment        text    DEFAULT NULL,
    p_contact_phone  text    DEFAULT NULL,
    p_wants_callback boolean DEFAULT false,
    p_locale         text    DEFAULT 'ru',
    p_ip_hash        text    DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE
    tgt        product.review_targets;
    recent_cnt integer;
    new_id     uuid;
    known_tags text[];
BEGIN
    SELECT * INTO tgt FROM product.review_targets WHERE slug = p_slug;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'опрос не найден' USING ERRCODE = 'no_data_found';
    END IF;
    IF NOT tgt.is_active THEN
        RAISE EXCEPTION 'опрос закрыт' USING ERRCODE = 'insufficient_privilege';
    END IF;

    IF p_rating IS NULL OR p_rating < 1 OR p_rating > 5 THEN
        RAISE EXCEPTION 'оценка должна быть от 1 до 5' USING ERRCODE = 'check_violation';
    END IF;

    -- Один отзыв с одного адреса на одну цель в час. Порог мягкий намеренно:
    -- в клинике за одним Wi-Fi сидит много разных пациентов, и жёсткий лимит
    -- отсёк бы честные отзывы вместе со спамом.
    IF p_ip_hash IS NOT NULL THEN
        SELECT count(*) INTO recent_cnt
        FROM product.reviews
        WHERE target_id = tgt.id AND ip_hash = p_ip_hash
          AND created_at > now() - interval '1 hour';
        IF recent_cnt >= 1 THEN
            RAISE EXCEPTION 'отзыв с этого устройства уже принят'
                USING ERRCODE = 'too_many_connections';
        END IF;
    END IF;

    -- Теги принимаем только из словаря: иначе публичная форма станет каналом
    -- для записи произвольных строк в базу.
    SELECT array_agg(code) INTO known_tags FROM product.review_tags;
    IF NOT (coalesce(p_good_tags, '{}') <@ coalesce(known_tags, '{}')
            AND coalesce(p_bad_tags, '{}') <@ coalesce(known_tags, '{}')) THEN
        RAISE EXCEPTION 'неизвестный тег' USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO product.reviews (
        clinic_id, target_id, rating, good_tags, bad_tags, comment,
        contact_phone, wants_callback, locale, ip_hash
    ) VALUES (
        tgt.clinic_id, tgt.id, p_rating,
        coalesce(p_good_tags, '{}'), coalesce(p_bad_tags, '{}'),
        nullif(btrim(left(coalesce(p_comment, ''), 2000)), ''),
        nullif(btrim(coalesce(p_contact_phone, '')), ''),
        coalesce(p_wants_callback, false) AND nullif(btrim(coalesce(p_contact_phone, '')), '') IS NOT NULL,
        coalesce(p_locale, 'ru'),
        p_ip_hash
    )
    RETURNING id INTO new_id;

    RETURN new_id;
END $$;

COMMENT ON FUNCTION product.submit_review IS
  'Приём отзыва с публичной страницы. Проверяет активность опроса, диапазон '
  'оценки, лимит по устройству и принадлежность тегов словарю.';

-- ══ Выдача цели отзыва для узла или сотрудника ════════════════════════════════
-- Создание цели вынесено в функцию, чтобы clinic_id и вид цели нельзя было
-- рассогласовать: они выводятся из самого узла, а не приходят из запроса.
CREATE OR REPLACE FUNCTION product.ensure_unit_review_target(p_unit_id uuid)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE
    v_clinic uuid;
    v_name   text;
    v_id     uuid;
BEGIN
    SELECT clinic_id, name INTO v_clinic, v_name
    FROM product.clinic_units WHERE id = p_unit_id;
    IF v_clinic IS NULL THEN
        RAISE EXCEPTION 'подразделение не найдено' USING ERRCODE = 'no_data_found';
    END IF;
    IF v_clinic <> product.current_clinic_id() THEN
        RAISE EXCEPTION 'подразделение другой клиники' USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT id INTO v_id FROM product.review_targets
     WHERE kind = 'unit' AND unit_id = p_unit_id;
    IF v_id IS NOT NULL THEN RETURN v_id; END IF;

    INSERT INTO product.review_targets (clinic_id, kind, unit_id, title)
    VALUES (v_clinic, 'unit', p_unit_id, v_name)
    RETURNING id INTO v_id;
    RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION product.ensure_employee_review_target(p_employee_id uuid)
RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
DECLARE
    v_clinic uuid;
    v_name   text;
    v_id     uuid;
BEGIN
    SELECT clinic_id, full_name INTO v_clinic, v_name
    FROM product.employees WHERE id = p_employee_id;
    IF v_clinic IS NULL THEN
        RAISE EXCEPTION 'сотрудник не найден' USING ERRCODE = 'no_data_found';
    END IF;
    IF v_clinic <> product.current_clinic_id() THEN
        RAISE EXCEPTION 'сотрудник другой клиники' USING ERRCODE = 'insufficient_privilege';
    END IF;

    SELECT id INTO v_id FROM product.review_targets
     WHERE kind = 'employee' AND employee_id = p_employee_id;
    IF v_id IS NOT NULL THEN RETURN v_id; END IF;

    INSERT INTO product.review_targets (clinic_id, kind, employee_id, title)
    VALUES (v_clinic, 'employee', p_employee_id, v_name)
    RETURNING id INTO v_id;
    RETURN v_id;
END $$;

GRANT EXECUTE ON FUNCTION
      product.public_review_target(text),
      product.submit_review(text, smallint, text[], text[], text, text, boolean, text, text),
      product.ensure_unit_review_target(uuid),
      product.ensure_employee_review_target(uuid),
      product.recount_seats(uuid)
      TO ishmed_app;
