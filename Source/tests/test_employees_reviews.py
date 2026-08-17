"""Штат и отзывы: автосчёт ставок и защита публичной формы.

Два свойства, которые нельзя проверить глазами и легко сломать правкой:

  * занятость ставки — производная от людей в штате, а не поле, которое кто-то
    помнит обновить;
  * публичная форма отзыва доступна без входа, но при этом не даёт ни читать
    чужие данные, ни заливать базу.
"""
from __future__ import annotations

import psycopg
import pytest

from app import db
from tests.conftest import admin_execute, admin_fetch_one

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def clinic_with_structure(fixture_world):
    """Дерево стоматологии: филиал → этаж → отделение, штатная единица на 2 ставки."""
    w = fixture_world
    row = await admin_fetch_one(
        """
        WITH branch AS (
            INSERT INTO product.clinic_units (clinic_id, name)
            VALUES (%(clinic)s, 'TEST филиал') RETURNING id
        ), fl AS (
            INSERT INTO product.clinic_units (clinic_id, parent_id, name)
            SELECT %(clinic)s, branch.id, 'TEST 2 этаж' FROM branch RETURNING id
        ), dep AS (
            INSERT INTO product.clinic_units (clinic_id, parent_id, name)
            SELECT %(clinic)s, fl.id, 'TEST ортодонтия' FROM fl RETURNING id
        ), pos AS (
            INSERT INTO product.staff_positions
                (clinic_id, unit_id, title, role_category, specialty, seats)
            SELECT %(clinic)s, dep.id, 'TEST ассистент', 'nurse', 'general_nurse', 2
            FROM dep RETURNING id
        )
        SELECT (SELECT id FROM dep)::text AS unit_id,
               (SELECT id FROM pos)::text AS position_id
        """,
        {"clinic": w["clinic_a_id"]},
    )
    assert row is not None
    return {**w, **row}


# ── Ставки считаются сами ─────────────────────────────────────────────────────

async def _seats(position_id: str) -> dict:
    row = await admin_fetch_one(
        "SELECT seats, seats_filled, seats_open FROM product.staff_positions WHERE id = %s",
        (position_id,),
    )
    assert row is not None
    return row


async def test_hiring_fills_seats(clinic_with_structure):
    w = clinic_with_structure
    assert (await _seats(w["position_id"]))["seats_open"] == 2

    async with db.scoped(clinic_id=w["clinic_a_id"], user_id=w["clinic_user_id"], member_role="owner") as conn:
        for name in ("TEST Ахмедова", "TEST Каримов"):
            await conn.execute(
                """
                INSERT INTO product.employees
                    (clinic_id, unit_id, staff_position_id, full_name, role_category, status)
                VALUES (%s, %s, %s, %s, 'nurse', 'active')
                """,
                (w["clinic_a_id"], w["unit_id"], w["position_id"], name),
            )

    after = await _seats(w["position_id"])
    assert after["seats_filled"] == 2
    assert after["seats_open"] == 0, "две ставки заняты — вакансии нет"


async def _seats_in(conn, position_id: str) -> dict:
    """Читает ставки ТЕМ ЖЕ соединением.

    Через отдельное подключение незакоммиченную транзакцию не видно, и проверка
    внутри блока db.scoped() показывала бы старое значение. Заодно это точнее:
    триггер обязан сработать сразу, а не после коммита.
    """
    cur = await conn.execute(
        "SELECT seats, seats_filled, seats_open FROM product.staff_positions WHERE id = %s",
        (position_id,),
    )
    return await cur.fetchone()


async def test_dismissal_frees_seat(clinic_with_structure):
    w = clinic_with_structure
    async with db.scoped(clinic_id=w["clinic_a_id"], user_id=w["clinic_user_id"], member_role="owner") as conn:
        await conn.execute(
            """
            INSERT INTO product.employees
                (clinic_id, unit_id, staff_position_id, full_name, role_category, status)
            VALUES (%s, %s, %s, 'TEST уходящий', 'nurse', 'active')
            """,
            (w["clinic_a_id"], w["unit_id"], w["position_id"]),
        )
        assert (await _seats_in(conn, w["position_id"]))["seats_filled"] == 1

        await conn.execute(
            """
            UPDATE product.employees
               SET status = 'dismissed', dismissed_at = current_date
             WHERE full_name = 'TEST уходящий'
            """
        )
        assert (await _seats_in(conn, w["position_id"]))["seats_filled"] == 0


async def test_seats_never_exceed_capacity(clinic_with_structure):
    """Если людей завели больше, чем ставок, счётчик упирается в предел, а не
    ломает ограничение seats_filled <= seats."""
    w = clinic_with_structure
    async with db.scoped(clinic_id=w["clinic_a_id"], user_id=w["clinic_user_id"], member_role="owner") as conn:
        for i in range(4):
            await conn.execute(
                """
                INSERT INTO product.employees
                    (clinic_id, unit_id, staff_position_id, full_name, role_category, status)
                VALUES (%s, %s, %s, %s, 'nurse', 'active')
                """,
                (w["clinic_a_id"], w["unit_id"], w["position_id"], f"TEST человек {i}"),
            )

    s = await _seats(w["position_id"])
    assert s["seats_filled"] == 2 and s["seats_open"] == 0


# ── Цели отзыва ───────────────────────────────────────────────────────────────

async def test_review_target_is_idempotent(clinic_with_structure):
    w = clinic_with_structure
    async with db.scoped(clinic_id=w["clinic_a_id"], user_id=w["clinic_user_id"], member_role="owner") as conn:
        cur = await conn.execute(
            "SELECT product.ensure_unit_review_target(%s) AS id", (w["unit_id"],)
        )
        first = (await cur.fetchone())["id"]
        cur = await conn.execute(
            "SELECT product.ensure_unit_review_target(%s) AS id", (w["unit_id"],)
        )
        second = (await cur.fetchone())["id"]
    assert first == second, "повторная выдача QR не должна создавать вторую цель"


async def test_cannot_create_target_for_other_clinic(clinic_with_structure):
    """Узел чужой клиники недоступен, даже если знать его UUID."""
    w = clinic_with_structure
    other_unit = await admin_fetch_one(
        """
        INSERT INTO product.clinic_units (clinic_id, name)
        VALUES (%s, 'TEST чужой узел') RETURNING id::text AS id
        """,
        (w["clinic_b_id"],),
    )
    assert other_unit is not None

    with pytest.raises(Exception) as exc:
        async with db.scoped(clinic_id=w["clinic_a_id"], user_id=w["clinic_user_id"], member_role="owner") as conn:
            await conn.execute(
                "SELECT product.ensure_unit_review_target(%s)", (other_unit["id"],)
            )
    assert "другой клиники" in str(exc.value)


# ── Публичная форма ───────────────────────────────────────────────────────────

async def _slug(unit_id: str, clinic_id: str, user_id: int) -> str:
    async with db.scoped(clinic_id=clinic_id, user_id=user_id) as conn:
        await conn.execute("SELECT product.ensure_unit_review_target(%s)", (unit_id,))
    row = await admin_fetch_one(
        "SELECT slug FROM product.review_targets WHERE unit_id = %s", (unit_id,)
    )
    assert row is not None
    return row["slug"]


async def test_public_page_works_without_any_context(clinic_with_structure):
    """Пациент приходит по QR без входа. Контекста тенанта нет, и RLS без него
    ничего не отдаёт — поэтому страница обслуживается отдельной функцией."""
    w = clinic_with_structure
    slug = await _slug(w["unit_id"], w["clinic_a_id"], w["clinic_user_id"])

    row = await db.fetch_one("SELECT * FROM product.public_review_target(%s)", (slug,))
    assert row is not None
    assert row["title"] == "TEST ортодонтия"
    assert row["is_active"] is True
    # Наружу не должно уходить ничего лишнего: ни clinic_id, ни сотрудников.
    assert set(row.keys()) == {"target_id", "title", "subtitle", "clinic_name", "is_active"}


async def test_review_accepted_and_visible_only_to_owner(clinic_with_structure):
    w = clinic_with_structure
    slug = await _slug(w["unit_id"], w["clinic_a_id"], w["clinic_user_id"])

    await db.fetch_one(
        "SELECT product.submit_review(%s, %s::smallint, %s, %s, %s, NULL, false, 'ru', %s)",
        (slug, 4, ["politeness"], ["waiting"], "Всё хорошо, ждал долго", "hash-a"),
    )

    async with db.scoped(clinic_id=w["clinic_a_id"], user_id=w["clinic_user_id"], member_role="owner") as conn:
        cur = await conn.execute("SELECT rating, bad_tags FROM product.reviews")
        mine = await cur.fetchall()
    assert len(mine) == 1 and mine[0]["rating"] == 4

    async with db.scoped(clinic_id=w["clinic_b_id"], user_id=w["other_clinic_user_id"], member_role="owner") as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM product.reviews")
        assert (await cur.fetchone())["n"] == 0, "чужая клиника не должна видеть отзывы"


async def test_rate_limit_per_device(clinic_with_structure):
    w = clinic_with_structure
    slug = await _slug(w["unit_id"], w["clinic_a_id"], w["clinic_user_id"])

    await db.fetch_one(
        "SELECT product.submit_review(%s, %s::smallint, '{}', '{}', NULL, NULL, false, 'ru', %s)",
        (slug, 5, "hash-flood"),
    )
    with pytest.raises(Exception) as exc:
        await db.fetch_one(
            "SELECT product.submit_review(%s, %s::smallint, '{}', '{}', NULL, NULL, false, 'ru', %s)",
            (slug, 1, "hash-flood"),
        )
    assert "уже принят" in str(exc.value)

    # Другое устройство не должно попадать под чужой лимит.
    await db.fetch_one(
        "SELECT product.submit_review(%s, %s::smallint, '{}', '{}', NULL, NULL, false, 'ru', %s)",
        (slug, 2, "hash-other"),
    )


async def test_unknown_tag_rejected(clinic_with_structure):
    """Публичная форма не должна быть каналом записи произвольных строк."""
    w = clinic_with_structure
    slug = await _slug(w["unit_id"], w["clinic_a_id"], w["clinic_user_id"])
    with pytest.raises(Exception) as exc:
        await db.fetch_one(
            "SELECT product.submit_review(%s, %s::smallint, %s, '{}', NULL, NULL, false, 'ru', %s)",
            (slug, 5, ["'; DROP TABLE product.reviews; --"], "hash-x"),
        )
    assert "неизвестный тег" in str(exc.value)


@pytest.mark.parametrize("rating", [0, 6, -1, 99])
async def test_rating_out_of_range_rejected(clinic_with_structure, rating: int):
    w = clinic_with_structure
    slug = await _slug(w["unit_id"], w["clinic_a_id"], w["clinic_user_id"])
    # Ловим именно ошибку базы: проверка диапазона живёт в функции и в
    # ограничении таблицы, а не в питоне, где её можно обойти другим вызовом.
    with pytest.raises(psycopg.Error) as exc:
        await db.fetch_one(
            "SELECT product.submit_review(%s, %s::smallint, '{}', '{}', NULL, NULL, false, 'ru', %s)",
            (slug, rating, f"hash-{rating}"),
        )
    assert "оценка" in str(exc.value) or "check" in str(exc.value).lower()


async def test_closed_survey_rejects_reviews(clinic_with_structure):
    w = clinic_with_structure
    slug = await _slug(w["unit_id"], w["clinic_a_id"], w["clinic_user_id"])
    await admin_execute(
        "UPDATE product.review_targets SET is_active = false WHERE slug = %s", (slug,)
    )
    with pytest.raises(Exception) as exc:
        await db.fetch_one(
            "SELECT product.submit_review(%s, %s::smallint, '{}', '{}', NULL, NULL, false, 'ru', %s)",
            (slug, 5, "hash-closed"),
        )
    assert "закрыт" in str(exc.value)


async def test_callback_requires_phone(clinic_with_structure):
    """Просьба перезвонить без телефона — бессмыслица, ограничение это ловит."""
    w = clinic_with_structure
    slug = await _slug(w["unit_id"], w["clinic_a_id"], w["clinic_user_id"])
    row = await db.fetch_one(
        "SELECT product.submit_review(%s, %s::smallint, '{}', '{}', NULL, NULL, true, 'ru', %s) AS id",
        (slug, 3, "hash-cb"),
    )
    assert row is not None
    saved = await admin_fetch_one(
        "SELECT wants_callback, contact_phone FROM product.reviews WHERE id = %s", (row["id"],)
    )
    assert saved is not None
    assert saved["wants_callback"] is False, "без телефона просьба о звонке не сохраняется"
