-- 019_unit_name_uniqueness.sql
-- Починка уникальности названий подразделений.
--
-- Что было не так. Ограничение UNIQUE (clinic_id, parent_id, name) выглядело
-- достаточным, но у корневых узлов parent_id равен NULL, а в уникальных
-- индексах Postgres по умолчанию NULL не равен NULL. Значит для филиалов
-- ограничение не работало вовсе: повторный запуск сида создал второй
-- «Филиал на Чиланзаре», и ON CONFLICT его не поймал.
--
-- В PostgreSQL 15+ есть NULLS NOT DISTINCT — ровно для этого случая.
-- Заодно делаем сравнение регистронезависимым: «Ортодонтия» и «ортодонтия»
-- в одном отделении — это опечатка, а не два кабинета.

-- ── Что делаем с уже возникшими дубликатами ───────────────────────────────────
-- Не сливаем. Первая попытка сливать ветки упёрлась в то, что у обеих копий
-- «Филиала» есть свой «2 этаж», и перенос детей сам создавал конфликт. Дальше
-- пришлось бы сливать рекурсивно, а это способ незаметно перемешать или
-- потерять узлы.
--
-- Поэтому переименовываем: данные целы, ветки не перепутаны, а человек видит
-- в интерфейсе «(копия 2)» и решает сам — удалить или оставить. Обратимая
-- операция вместо необратимой.
DO $$
DECLARE
    dup record;
    n   integer;
BEGIN
    FOR dup IN
        SELECT u.id, u.name
        FROM product.clinic_units u
        WHERE EXISTS (
            SELECT 1 FROM product.clinic_units o
            WHERE o.clinic_id = u.clinic_id
              AND lower(o.name) = lower(u.name)
              AND (o.parent_id = u.parent_id OR (o.parent_id IS NULL AND u.parent_id IS NULL))
              AND o.created_at < u.created_at
        )
        ORDER BY u.created_at
    LOOP
        n := 2;
        -- Подбираем свободный суффикс: копий может быть и три.
        WHILE EXISTS (
            SELECT 1 FROM product.clinic_units x
            JOIN product.clinic_units me ON me.id = dup.id
            WHERE x.clinic_id = me.clinic_id
              AND (x.parent_id = me.parent_id OR (x.parent_id IS NULL AND me.parent_id IS NULL))
              AND lower(x.name) = lower(dup.name || ' (копия ' || n || ')')
        ) LOOP
            n := n + 1;
        END LOOP;

        UPDATE product.clinic_units
           SET name = dup.name || ' (копия ' || n || ')'
         WHERE id = dup.id;

        RAISE NOTICE 'дубликат «%» переименован в «% (копия %)»', dup.name, dup.name, n;
    END LOOP;
END $$;

-- Старое ограничение снимаем: оно не покрывало корневые узлы.
ALTER TABLE product.clinic_units
    DROP CONSTRAINT IF EXISTS clinic_units_clinic_id_parent_id_name_key;

-- Новый индекс: NULLS NOT DISTINCT закрывает корни, lower(name) — опечатки
-- в регистре. Индекс, а не CONSTRAINT, потому что по выражению ограничение
-- объявить нельзя.
CREATE UNIQUE INDEX IF NOT EXISTS uq_clinic_units_name
    ON product.clinic_units (clinic_id, parent_id, lower(name))
    NULLS NOT DISTINCT;

COMMENT ON INDEX product.uq_clinic_units_name IS
  'NULLS NOT DISTINCT обязателен: у филиалов parent_id = NULL, и без него '
  'уникальность корневых узлов не работает вовсе.';
