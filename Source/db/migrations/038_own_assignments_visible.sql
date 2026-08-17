-- 038_own_assignments_visible.sql
-- Сотрудник не видел даже своих назначений. Причина — рекурсия прав.
--
-- Политики из 022 определяли «своё» подзапросом:
--
--     employee_id IN (SELECT e.id FROM product.employees e
--                      WHERE e.user_id = product.current_user_id())
--
-- Подзапрос внутри политики выполняется с правами ТЕКУЩЕЙ роли, то есть на
-- product.employees к нему применяется её же политика. А она после 022
-- требует product.is_manager(). Для сотрудника подзапрос всегда пуст, значит
-- и условие всегда ложно: /api/portal/courses отдавал пустой список при живом
-- назначении в базе.
--
-- Тот же класс ошибки уже был поймано в 034 на applications → candidate_profiles
-- и решён так же: принадлежность вычисляет SECURITY DEFINER функция, а не
-- подзапрос политики. Запомнить правило: политика не может ссылаться на
-- таблицу, которая закрыта от той же роли.

CREATE OR REPLACE FUNCTION product.my_employee_id()
RETURNS uuid
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, product
AS $$
    SELECT e.id
    FROM product.employees e
    WHERE e.user_id = product.current_user_id()
      AND e.clinic_id = product.current_clinic_id()
$$;

COMMENT ON FUNCTION product.my_employee_id IS
  'Карточка сотрудника текущего пользователя. Нужна политикам обучения: '
  'product.employees закрыта для роли employee, и подзапрос к ней внутри '
  'политики всегда пуст. Отдаёт ТОЛЬКО свой id — перечислить чужие нельзя.';

GRANT EXECUTE ON FUNCTION product.my_employee_id() TO ishmed_app;

-- ══ Назначения и попытки: своё видно снова ════════════════════════════════════
DROP POLICY IF EXISTS p_course_assignments_scope ON product.course_assignments;
CREATE POLICY p_course_assignments_scope ON product.course_assignments
    USING (
        clinic_id = product.current_clinic_id()
        AND (product.is_manager() OR employee_id = product.my_employee_id())
    )
    -- Запись по-прежнему только менеджеру: назначает курс он, а статус
    -- двигают SECURITY DEFINER функции (start_course_attempt, grade_attempt).
    -- Иначе сотрудник поставил бы себе passed напрямую.
    WITH CHECK (clinic_id = product.current_clinic_id() AND product.is_manager());

DROP POLICY IF EXISTS p_course_attempts_scope ON product.course_attempts;
CREATE POLICY p_course_attempts_scope ON product.course_attempts
    USING (
        clinic_id = product.current_clinic_id()
        AND (product.is_manager() OR employee_id = product.my_employee_id())
    )
    WITH CHECK (
        clinic_id = product.current_clinic_id()
        AND employee_id = product.my_employee_id()
    );
