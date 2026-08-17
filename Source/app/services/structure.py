"""Структура клиники: подразделения и штатные единицы.

Иерархия любой глубины: филиал → этаж → отделение → кабинет. «Этаж» — такой же
узел, как «ортодонтия», просто на другом уровне. Отдельного типа для этажа нет
намеренно: у разных клиник вложенность разная, и жёсткие уровни пришлось бы
ломать на второй же клинике.

Все запросы идут по соединению с контекстом тенанта, поэтому фильтра по
clinic_id в SQL нет — его ставит RLS.
"""
from __future__ import annotations

from typing import Any


async def tree(conn) -> list[dict[str, Any]]:
    """Плоский список узлов с уровнем и путём, готовый к отрисовке деревом.

    Сортировка по пути, а не по имени: иначе дети оторвутся от родителей.
    Считаем на стороне БД, потому что рекурсию по узлам в питоне пришлось бы
    гонять запросами на каждый уровень.
    """
    cur = await conn.execute(
        """
        WITH RECURSIVE t AS (
            SELECT u.id, u.parent_id, u.name, u.district, 0 AS level,
                   ARRAY[lower(u.name)] AS path
            FROM product.clinic_units u
            WHERE u.parent_id IS NULL
            UNION ALL
            SELECT u.id, u.parent_id, u.name, u.district, t.level + 1,
                   t.path || lower(u.name)
            FROM product.clinic_units u
            JOIN t ON u.parent_id = t.id
        )
        SELECT t.id::text, t.parent_id::text, t.name, t.district, t.level,
               d.name_ru AS district_name,
               rt.slug   AS qr_slug,
               (SELECT count(*) FROM product.staff_positions sp WHERE sp.unit_id = t.id)
                   AS positions_count,
               (SELECT coalesce(sum(sp.seats), 0) FROM product.staff_positions sp
                 WHERE sp.unit_id = t.id) AS seats,
               (SELECT coalesce(sum(sp.seats_filled), 0) FROM product.staff_positions sp
                 WHERE sp.unit_id = t.id) AS seats_filled,
               (SELECT count(*) FROM product.employees e
                 WHERE e.unit_id = t.id AND e.status <> 'dismissed') AS employees_count,
               (SELECT count(*) FROM product.reviews r
                 JOIN product.review_targets x ON x.id = r.target_id
                WHERE x.kind = 'unit' AND x.unit_id = t.id) AS reviews_count
        FROM t
        LEFT JOIN product.districts d ON d.code = t.district
        LEFT JOIN product.review_targets rt ON rt.kind = 'unit' AND rt.unit_id = t.id
        ORDER BY t.path
        """
    )
    return await cur.fetchall()


async def create_unit(
    conn, *, clinic_id: str, name: str, parent_id: str | None, district: str | None
) -> dict[str, Any]:
    cur = await conn.execute(
        """
        INSERT INTO product.clinic_units (clinic_id, parent_id, name, district)
        VALUES (%s, %s, %s, %s)
        RETURNING id::text, parent_id::text, name, district
        """,
        (clinic_id, parent_id, name.strip(), district),
    )
    return await cur.fetchone()


async def rename_unit(conn, unit_id: str, name: str) -> dict[str, Any] | None:
    cur = await conn.execute(
        "UPDATE product.clinic_units SET name = %s WHERE id = %s "
        "RETURNING id::text, name",
        (name.strip(), unit_id),
    )
    return await cur.fetchone()


async def move_unit(conn, unit_id: str, parent_id: str | None) -> dict[str, Any] | None:
    """Перенос узла в другого родителя.

    Проверка на цикл обязательна: без неё можно сделать узел собственным
    предком, и рекурсивный обход дерева зациклится намертво.
    """
    if parent_id is not None:
        cur = await conn.execute(
            """
            WITH RECURSIVE up AS (
                SELECT id, parent_id FROM product.clinic_units WHERE id = %s
                UNION ALL
                SELECT u.id, u.parent_id FROM product.clinic_units u
                JOIN up ON up.parent_id = u.id
            )
            SELECT EXISTS (SELECT 1 FROM up WHERE id = %s) AS loops
            """,
            (parent_id, unit_id),
        )
        row = await cur.fetchone()
        if row and row["loops"]:
            raise ValueError("нельзя перенести узел внутрь его собственного потомка")
        if parent_id == unit_id:
            raise ValueError("узел не может быть родителем самому себе")

    cur = await conn.execute(
        "UPDATE product.clinic_units SET parent_id = %s WHERE id = %s "
        "RETURNING id::text, parent_id::text",
        (parent_id, unit_id),
    )
    return await cur.fetchone()


async def delete_unit(conn, unit_id: str) -> str | None:
    """Удаляет узел, если он пуст.

    Каскад в БД снёс бы вместе с узлом его детей, штатные единицы, QR-цели и
    отзывы. Терять отзывы пациентов из-за случайного клика нельзя, поэтому
    удаление разрешаем только у пустого узла.
    """
    cur = await conn.execute(
        """
        SELECT (SELECT count(*) FROM product.clinic_units c WHERE c.parent_id = %(id)s) AS children,
               (SELECT count(*) FROM product.staff_positions p WHERE p.unit_id = %(id)s) AS positions,
               (SELECT count(*) FROM product.employees e
                 WHERE e.unit_id = %(id)s AND e.status <> 'dismissed') AS employees,
               (SELECT count(*) FROM product.reviews r
                 JOIN product.review_targets x ON x.id = r.target_id
                WHERE x.unit_id = %(id)s) AS reviews
        """,
        {"id": unit_id},
    )
    blockers = await cur.fetchone()
    if blockers is None:
        return "узел не найден"
    if blockers["children"]:
        return "внутри есть другие подразделения"
    if blockers["positions"]:
        return "к узлу привязаны штатные единицы"
    if blockers["employees"]:
        return "в узле есть сотрудники"
    if blockers["reviews"]:
        return "по узлу уже есть отзывы, удаление стёрло бы их"

    await conn.execute("DELETE FROM product.clinic_units WHERE id = %s", (unit_id,))
    return None


# ── Штатные единицы ───────────────────────────────────────────────────────────

async def positions(conn, unit_id: str | None = None) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        SELECT sp.id::text, sp.unit_id::text, sp.title, sp.role_category, sp.specialty,
               sp.seats, sp.seats_filled, sp.seats_open,
               u.name AS unit_name,
               rc.name_ru AS role_name, s.name_ru AS specialty_name
        FROM product.staff_positions sp
        LEFT JOIN product.clinic_units u ON u.id = sp.unit_id
        LEFT JOIN product.role_categories rc ON rc.code = sp.role_category
        LEFT JOIN product.specialties s ON s.code = sp.specialty
        WHERE (%s::uuid IS NULL OR sp.unit_id = %s::uuid)
        ORDER BY u.name NULLS FIRST, sp.title
        """,
        (unit_id, unit_id),
    )
    return await cur.fetchall()


async def create_position(
    conn,
    *,
    clinic_id: str,
    unit_id: str | None,
    title: str,
    role_category: str,
    specialty: str | None,
    seats: int,
) -> dict[str, Any]:
    cur = await conn.execute(
        """
        INSERT INTO product.staff_positions
            (clinic_id, unit_id, title, role_category, specialty, seats)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id::text, unit_id::text, title, seats, seats_filled, seats_open
        """,
        (clinic_id, unit_id, title.strip(), role_category, specialty, seats),
    )
    return await cur.fetchone()


async def update_position_seats(conn, position_id: str, seats: int) -> dict[str, Any] | None:
    cur = await conn.execute(
        "UPDATE product.staff_positions SET seats = %s WHERE id = %s "
        "RETURNING id::text, seats, seats_filled, seats_open",
        (seats, position_id),
    )
    return await cur.fetchone()


async def dictionaries(conn) -> dict[str, list[dict[str, Any]]]:
    """Словари для выпадающих списков. Один запрос вместо трёх круговых."""
    out: dict[str, list[dict[str, Any]]] = {}
    for key, sql in (
        ("roles", "SELECT code, name_ru, name_uz FROM product.role_categories ORDER BY sort"),
        (
            "specialties",
            "SELECT code, role_category, name_ru, name_uz FROM product.specialties "
            "ORDER BY role_category, name_ru",
        ),
        ("districts", "SELECT code, name_ru, name_uz FROM product.districts ORDER BY name_ru"),
        ("review_tags", "SELECT code, name_ru, name_uz FROM product.review_tags ORDER BY sort"),
    ):
        cur = await conn.execute(sql)
        out[key] = await cur.fetchall()
    return out
