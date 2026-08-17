"""Отзывы пациентов и QR-цели.

Отзывы видит только клиника: публичных рейтингов на пилоте нет, поэтому здесь
нет и понятия «опубликовать». Есть поток оценок, отдельно низкие и отметка
«разобрано» — то, чем менеджер реально пользуется.

Создание QR-цели и приём отзыва идут через SECURITY DEFINER функции в БД:
цель выводит clinic_id из самого узла, а приём проверяет лимиты. Здесь мы их
только вызываем.
"""
from __future__ import annotations

import hashlib
from typing import Any


def hash_ip(ip: str | None, salt: str) -> str | None:
    """Хэш адреса для лимитов.

    Сам адрес не храним: для «один отзыв с устройства в час» достаточно хэша,
    а лишних данных о пациенте у нас не появляется. Соль берём из ключа
    приложения, чтобы хэш нельзя было сверить с готовой радужной таблицей.
    """
    if not ip:
        return None
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:32]


# ── QR-цели ───────────────────────────────────────────────────────────────────

async def ensure_unit_target(conn, unit_id: str) -> dict[str, Any] | None:
    await conn.execute("SELECT product.ensure_unit_review_target(%s)", (unit_id,))
    cur = await conn.execute(
        """
        SELECT rt.id::text, rt.slug, rt.title, rt.is_active
        FROM product.review_targets rt
        WHERE rt.kind = 'unit' AND rt.unit_id = %s
        """,
        (unit_id,),
    )
    return await cur.fetchone()


async def ensure_employee_target(conn, employee_id: str) -> dict[str, Any] | None:
    await conn.execute("SELECT product.ensure_employee_review_target(%s)", (employee_id,))
    cur = await conn.execute(
        """
        SELECT rt.id::text, rt.slug, rt.title, rt.is_active
        FROM product.review_targets rt
        WHERE rt.kind = 'employee' AND rt.employee_id = %s
        """,
        (employee_id,),
    )
    return await cur.fetchone()


async def targets(conn) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        SELECT rt.id::text, rt.kind::text, rt.slug, rt.title, rt.subtitle, rt.is_active,
               u.name AS unit_name, e.full_name AS employee_name,
               (SELECT count(*) FROM product.reviews r WHERE r.target_id = rt.id) AS reviews_count,
               (SELECT round(avg(r.rating), 1) FROM product.reviews r WHERE r.target_id = rt.id)
                   AS avg_rating
        FROM product.review_targets rt
        LEFT JOIN product.clinic_units u ON u.id = rt.unit_id
        LEFT JOIN product.employees e ON e.id = rt.employee_id
        ORDER BY rt.kind, rt.title
        """
    )
    return await cur.fetchall()


async def set_target_active(conn, target_id: str, is_active: bool) -> dict[str, Any] | None:
    """Отключение опроса вместо удаления цели.

    Код напечатан и наклеен на стену: удалить цель значит превратить наклейку
    в битую ссылку без объяснения. Отключённый опрос честно скажет, что он
    закрыт.
    """
    cur = await conn.execute(
        "UPDATE product.review_targets SET is_active = %s WHERE id = %s "
        "RETURNING id::text, slug, is_active",
        (is_active, target_id),
    )
    return await cur.fetchone()


# ── Отзывы ────────────────────────────────────────────────────────────────────

async def listing(
    conn,
    *,
    target_id: str | None = None,
    max_rating: int | None = None,
    only_unhandled: bool = False,
    only_callback: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        SELECT r.id::text, r.rating, r.good_tags, r.bad_tags, r.comment,
               r.contact_phone, r.wants_callback, r.created_at, r.handled_at,
               rt.kind::text AS target_kind, rt.title AS target_title,
               u.name AS unit_name, e.full_name AS employee_name,
               -- Вложения ехали в никуда: бот их писал, а кабинет не читал.
               -- Расшифровка голосового приезжает текстом, фото — только
               -- идентификатором: сам файл отдаёт отдельный прокси, чтобы
               -- фотографии пациентов не лежали у нас на диске.
               COALESCE(
                   (SELECT jsonb_agg(jsonb_build_object(
                               'id', ra.id::text,
                               'kind', ra.kind::text,
                               'transcript', ra.transcript,
                               'duration', ra.duration)
                            ORDER BY ra.created_at)
                      FROM product.review_attachments ra
                     WHERE ra.review_id = r.id),
                   '[]'::jsonb
               ) AS attachments
        FROM product.reviews r
        JOIN product.review_targets rt ON rt.id = r.target_id
        LEFT JOIN product.clinic_units u ON u.id = rt.unit_id
        LEFT JOIN product.employees e ON e.id = rt.employee_id
        WHERE (%(target)s::uuid IS NULL OR r.target_id = %(target)s::uuid)
          AND (%(maxr)s::int IS NULL OR r.rating <= %(maxr)s)
          AND (NOT %(unhandled)s OR r.handled_at IS NULL)
          AND (NOT %(callback)s OR r.wants_callback)
        ORDER BY r.created_at DESC
        LIMIT %(limit)s
        """,
        {
            "target": target_id,
            "maxr": max_rating,
            "unhandled": only_unhandled,
            "callback": only_callback,
            "limit": min(limit, 500),
        },
    )
    return await cur.fetchall()


async def summary(conn) -> dict[str, Any]:
    cur = await conn.execute(
        """
        SELECT count(*)                                             AS total,
               count(*) FILTER (WHERE created_at > now() - interval '7 days') AS last_week,
               round(avg(rating), 2)                                AS avg_rating,
               count(*) FILTER (WHERE rating <= 2)                  AS low,
               count(*) FILTER (WHERE rating <= 2 AND handled_at IS NULL) AS low_unhandled,
               count(*) FILTER (WHERE wants_callback AND handled_at IS NULL) AS callbacks_pending
        FROM product.reviews
        """
    )
    return await cur.fetchone()


async def tag_stats(conn) -> list[dict[str, Any]]:
    """Что чаще всего просят улучшить. Именно это менеджер и должен видеть
    первым: не средний балл, а конкретную причину."""
    cur = await conn.execute(
        """
        SELECT t.code, t.name_ru,
               count(*) FILTER (WHERE t.code = ANY(r.bad_tags)) AS bad,
               count(*) FILTER (WHERE t.code = ANY(r.good_tags)) AS good
        FROM product.review_tags t
        CROSS JOIN product.reviews r
        GROUP BY t.code, t.name_ru, t.sort
        HAVING count(*) FILTER (WHERE t.code = ANY(r.bad_tags))
             + count(*) FILTER (WHERE t.code = ANY(r.good_tags)) > 0
        ORDER BY bad DESC, good DESC
        """
    )
    return await cur.fetchall()


async def mark_handled(conn, review_id: str, user_id: int) -> dict[str, Any] | None:
    cur = await conn.execute(
        """
        UPDATE product.reviews
           SET handled_at = now(), handled_by = %s
         WHERE id = %s AND handled_at IS NULL
        RETURNING id::text, handled_at
        """,
        (user_id, review_id),
    )
    return await cur.fetchone()


# ── Портал сотрудника: отзывы о себе ─────────────────────────────────────────
# product.reviews закрыта политикой для роли employee целиком, поэтому здесь
# только вызовы SECURITY DEFINER функций. Обычным SELECT сотрудник не увидит
# даже отзывы о себе — и это правильный порядок: сначала явное разрешение,
# потом данные.

async def my_listing(conn, limit: int = 100) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        SELECT review_id::text AS id, rating, good_tags, bad_tags, comment,
               locale, handled_at, created_at
        FROM product.my_reviews(%s)
        """,
        (limit,),
    )
    return await cur.fetchall()


async def my_summary(conn) -> dict[str, Any]:
    cur = await conn.execute(
        "SELECT total, last_week, avg_rating, low FROM product.my_review_stats()"
    )
    return await cur.fetchone() or {}


async def tag_dictionary(conn) -> list[dict[str, Any]]:
    """Словарь аспектов сервиса.

    Портал не может взять его из `/api/structure/dictionaries`: тот раздел
    менеджерский и отдаёт сотруднику 403. Сама таблица — словарь без тенанта,
    читать её роли можно.
    """
    cur = await conn.execute(
        "SELECT code, name_ru, name_uz FROM product.review_tags ORDER BY sort"
    )
    return await cur.fetchall()


# ── Публичная часть ───────────────────────────────────────────────────────────

async def public_target(conn, slug: str) -> dict[str, Any] | None:
    cur = await conn.execute(
        "SELECT * FROM product.public_review_target(%s)", (slug,)
    )
    return await cur.fetchone()


async def submit(
    conn,
    *,
    slug: str,
    rating: int,
    good_tags: list[str],
    bad_tags: list[str],
    comment: str | None,
    contact_phone: str | None,
    wants_callback: bool,
    locale: str,
    ip_hash: str | None,
) -> str:
    cur = await conn.execute(
        """
        SELECT product.submit_review(
            %s, %s::smallint, %s::text[], %s::text[], %s, %s, %s, %s, %s
        ) AS id
        """,
        (
            slug, rating, good_tags, bad_tags, comment,
            contact_phone, wants_callback, locale, ip_hash,
        ),
    )
    row = await cur.fetchone()
    return str(row["id"])
