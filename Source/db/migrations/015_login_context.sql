-- 015_login_context.sql
-- Разрыв замкнутого круга при входе.
--
-- RLS на product.clinics и product.clinic_members опирается на ishmed.clinic_id.
-- Но чтобы узнать clinic_id, нужно посмотреть членство пользователя — то есть
-- прочитать таблицу, закрытую этим самым контекстом. Прикладная роль в такой
-- ситуации получает пустой результат, и вход не работает никогда.
--
-- Решение: одна SECURITY DEFINER функция, которая отвечает ровно на один
-- вопрос — «к какой клинике принадлежит этот пользователь». Она не выдаёт
-- ничего больше и не заменяет RLS: после входа всё остальное читается уже
-- в контексте тенанта.

CREATE OR REPLACE FUNCTION product.user_clinic(p_user_id bigint)
RETURNS TABLE (
    clinic_id     uuid,
    clinic_name   text,
    access_status product.clinic_access,
    member_role   product.member_role
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
    SELECT c.id, c.name, c.access_status, cm.role
    FROM product.clinic_members cm
    JOIN product.clinics c ON c.id = cm.clinic_id
    WHERE cm.user_id = p_user_id
    ORDER BY cm.created_at
    LIMIT 1
$$;

COMMENT ON FUNCTION product.user_clinic IS
  'К какой клинике принадлежит пользователь. Нужна на входе, когда контекст '
  'тенанта ещё не выставлен. На P0 одна клиника на сотрудника (LIMIT 1).';

GRANT EXECUTE ON FUNCTION product.user_clinic(bigint) TO ishmed_app;
