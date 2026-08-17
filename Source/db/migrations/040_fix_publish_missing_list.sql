-- 040_fix_publish_missing_list.sql
-- `publish_my_profile` падала на сборке списка недостающих полей.
--
-- В 039 было написано `v_miss := v_miss || 'specialty'`. Для text[] это не
-- «добавь элемент», а «склей два массива»: у оператора `||` есть перегрузка
-- anyarray || anyarray, и нетипизированный литерал Postgres приводит к массиву
-- раньше, чем к элементу. Отсюда `malformed array literal: "specialty"`.
--
-- Поймал тест на неполной карточке. Заметить это иначе было трудно: первая
-- ветка проверяет role_category, и на профиле без роли ошибка не возникает —
-- строка просто не исполняется. Падало только на карточке, где роль есть, а
-- специальности нет, то есть на самом частом случае незаполненной анкеты.
--
-- Лечится явным приведением каждого элемента к text.

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

    -- ::text у каждого литерала обязателен, см. заголовок файла.
    IF c.role_category IS NULL     THEN v_miss := v_miss || 'role_category'::text; END IF;
    IF c.specialty IS NULL         THEN v_miss := v_miss || 'specialty'::text; END IF;
    IF c.experience_months IS NULL THEN v_miss := v_miss || 'experience_months'::text; END IF;
    IF coalesce(array_length(c.districts, 1), 0) = 0 THEN
        v_miss := v_miss || 'districts'::text;
    END IF;
    IF coalesce(array_length(c.schedule, 1), 0) = 0 THEN
        v_miss := v_miss || 'schedule'::text;
    END IF;

    v_phone := EXISTS (SELECT 1 FROM product.candidate_contacts cc
                        WHERE cc.candidate_id = c.id);

    IF coalesce(array_length(v_miss, 1), 0) > 0 THEN
        RETURN QUERY SELECT c.id, false, v_miss, v_phone;
        RETURN;
    END IF;

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
GRANT EXECUTE ON FUNCTION product.publish_my_profile(bigint) TO ishmed_app;

COMMENT ON FUNCTION product.publish_my_profile IS
  'Выводит карточку в общий поиск клиник. Неполную не выводит и возвращает '
  'список недостающих полей. Пишет profile_published в журнал согласий: '
  'видимость всем клиникам — обещание, которое обязано быть зафиксировано.';
