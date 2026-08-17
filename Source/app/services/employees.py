"""Сотрудники клиники.

Сотрудник — работающий человек, в отличие от кандидата (ищет работу) и от
пользователя кабинета (менеджер). Занятость ставки не выставляется руками:
триггер в БД пересчитывает её по числу людей на единице, поэтому здесь мы
только перемещаем человека, а счётчик становится следствием.
"""
from __future__ import annotations

from typing import Any


async def listing(
    conn,
    *,
    unit_id: str | None = None,
    status: str | None = None,
    search: str | None = None,
) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        SELECT e.id::text, e.full_name, e.status, e.hired_at, e.dismissed_at,
               e.work_phone, e.work_email, e.role_category, e.specialty,
               e.unit_id::text, e.staff_position_id::text,
               u.name  AS unit_name,
               sp.title AS position_title,
               rc.name_ru AS role_name,
               s.name_ru  AS specialty_name,
               rt.slug    AS qr_slug,
               (SELECT count(*) FROM product.reviews r WHERE r.target_id = rt.id) AS reviews_count,
               (SELECT round(avg(r.rating), 1) FROM product.reviews r WHERE r.target_id = rt.id)
                   AS avg_rating
        FROM product.employees e
        LEFT JOIN product.clinic_units u ON u.id = e.unit_id
        LEFT JOIN product.staff_positions sp ON sp.id = e.staff_position_id
        LEFT JOIN product.role_categories rc ON rc.code = e.role_category
        LEFT JOIN product.specialties s ON s.code = e.specialty
        LEFT JOIN product.review_targets rt ON rt.kind = 'employee' AND rt.employee_id = e.id
        WHERE (%(unit)s::uuid IS NULL OR e.unit_id = %(unit)s::uuid)
          AND (%(status)s::text IS NULL OR e.status::text = %(status)s)
          AND (%(q)s::text IS NULL OR e.full_name ILIKE '%%' || %(q)s || '%%')
        ORDER BY (e.status = 'dismissed'), u.name NULLS LAST, e.full_name
        """,
        {"unit": unit_id, "status": status, "q": search},
    )
    return await cur.fetchall()


async def create(
    conn,
    *,
    clinic_id: str,
    full_name: str,
    unit_id: str | None,
    staff_position_id: str | None,
    role_category: str | None,
    specialty: str | None,
    work_phone: str | None,
    work_email: str | None,
    hired_at: str | None,
    status: str = "active",
) -> dict[str, Any]:
    cur = await conn.execute(
        """
        INSERT INTO product.employees
            (clinic_id, unit_id, staff_position_id, full_name, role_category, specialty,
             work_phone, work_email, hired_at, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id::text, full_name, status
        """,
        (
            clinic_id, unit_id, staff_position_id, full_name.strip(),
            role_category, specialty,
            (work_phone or "").strip() or None,
            (work_email or "").strip() or None,
            hired_at, status,
        ),
    )
    return await cur.fetchone()


async def update(conn, employee_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    """Частичное обновление.

    Список разрешённых полей закрытый: без него клиент смог бы прислать
    clinic_id и увести сотрудника в другой тенант. RLS такую запись отклонит,
    но полагаться на второй рубеж там, где можно не открывать первый, незачем.
    """
    allowed = {
        "full_name", "unit_id", "staff_position_id", "role_category", "specialty",
        "work_phone", "work_email", "hired_at", "status", "note",
    }
    patch = {k: v for k, v in fields.items() if k in allowed}
    if not patch:
        return None

    sets = ", ".join(f"{k} = %({k})s" for k in patch)
    patch["id"] = employee_id
    cur = await conn.execute(
        f"UPDATE product.employees SET {sets} WHERE id = %(id)s "  # noqa: S608 — имена полей из белого списка выше
        "RETURNING id::text, full_name, status, unit_id::text, staff_position_id::text",
        patch,
    )
    return await cur.fetchone()


async def dismiss(conn, employee_id: str, dismissed_at: str | None = None) -> dict[str, Any] | None:
    """Увольнение. Ставка освободится сама — триггер пересчитает счётчик."""
    cur = await conn.execute(
        """
        UPDATE product.employees
           SET status = 'dismissed',
               dismissed_at = coalesce(%s::date, current_date)
         WHERE id = %s AND status <> 'dismissed'
        RETURNING id::text, full_name, dismissed_at
        """,
        (dismissed_at, employee_id),
    )
    return await cur.fetchone()


async def summary(conn) -> dict[str, Any]:
    cur = await conn.execute(
        """
        SELECT count(*) FILTER (WHERE status <> 'dismissed')            AS active,
               count(*) FILTER (WHERE status = 'onboarding')            AS onboarding,
               count(*) FILTER (WHERE status = 'dismissed')             AS dismissed,
               (SELECT coalesce(sum(seats_open), 0)
                  FROM product.staff_positions)                         AS seats_open
        FROM product.employees
        """
    )
    return await cur.fetchone()
