"""Изоляция тенантов через RLS.

Проверяем, что клиника видит только своё, причём без единого WHERE clinic_id
в самих запросах: если изоляция держится на том, что программист не забыл
условие, она не изоляция. Здесь запросы намеренно написаны «наивно».
"""
from __future__ import annotations

import pytest

from app import db

pytestmark = pytest.mark.asyncio


async def _jobs_titles(conn) -> list[str]:
    """Намеренно без фильтра по клинике: всё отсекает RLS."""
    cur = await conn.execute("SELECT title FROM product.jobs ORDER BY title")
    return [r["title"] for r in await cur.fetchall()]


async def test_without_context_nothing_is_visible(fixture_world):
    """Не выставленный контекст означает «ничего не видно», а не «видно всё».
    Это важнее, чем кажется: забытый scoped() должен ломать функциональность,
    а не открывать все тенанты."""
    async with db.scoped() as conn:
        assert await _jobs_titles(conn) == []
        cur = await conn.execute("SELECT count(*) AS n FROM product.clinics")
        assert (await cur.fetchone())["n"] == 0


async def test_clinic_sees_only_own_jobs(fixture_world):
    w = fixture_world

    async with db.scoped(clinic_id=w["clinic_a_id"], user_id=w["clinic_user_id"], member_role="owner") as conn:
        titles = await _jobs_titles(conn)
    assert titles == ["TEST процедурная медсестра"]

    async with db.scoped(clinic_id=w["clinic_b_id"], user_id=w["other_clinic_user_id"], member_role="owner") as conn:
        titles = await _jobs_titles(conn)
    assert titles == ["TEST вакансия другой клиники"]


async def test_clinic_cannot_read_other_clinic_by_direct_id(fixture_world):
    """Даже зная UUID чужой вакансии, прочитать её нельзя."""
    w = fixture_world
    async with db.scoped(clinic_id=w["clinic_a_id"], user_id=w["clinic_user_id"], member_role="owner") as conn:
        cur = await conn.execute(
            "SELECT title FROM product.jobs WHERE id = %s", (w["job_b_id"],)
        )
        assert await cur.fetchone() is None


async def test_clinic_cannot_write_into_another_tenant(fixture_world):
    """WITH CHECK не даёт подсунуть чужой clinic_id при вставке."""
    w = fixture_world
    with pytest.raises(Exception) as exc:
        async with db.scoped(clinic_id=w["clinic_a_id"], user_id=w["clinic_user_id"], member_role="owner") as conn:
            await conn.execute(
                """
                INSERT INTO product.jobs (clinic_id, title, role_category, status)
                VALUES (%s, 'TEST подлог', 'nurse', 'active')
                """,
                (w["clinic_b_id"],),
            )
    assert "row-level security" in str(exc.value).lower()


async def test_clinic_cannot_move_own_job_to_another_tenant(fixture_world):
    w = fixture_world
    with pytest.raises(Exception) as exc:
        async with db.scoped(clinic_id=w["clinic_a_id"], user_id=w["clinic_user_id"], member_role="owner") as conn:
            await conn.execute(
                "UPDATE product.jobs SET clinic_id = %s WHERE id = %s",
                (w["clinic_b_id"], w["job_a_id"]),
            )
    assert "row-level security" in str(exc.value).lower()


async def test_medic_sees_own_profile_without_clinic_context(fixture_world):
    w = fixture_world
    async with db.scoped(user_id=w["medic_user_id"]) as conn:
        cur = await conn.execute(
            "SELECT specialty, experience_months FROM product.candidate_profiles"
        )
        rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["specialty"] == "procedural_nurse"


async def test_clinic_sees_active_profiles_but_never_contacts(fixture_world):
    """Клиника обязана видеть анонимную карточку, чтобы подбирать кандидатов,
    и не должна иметь доступа к таблице контактов ни при каком контексте."""
    w = fixture_world
    async with db.scoped(clinic_id=w["clinic_a_id"], user_id=w["clinic_user_id"], member_role="owner") as conn:
        # Считаем именно свой профиль, а не все активные в базе: тесты идут
        # против общей базы, и живые данные не должны ронять проверку. На этом
        # тест один раз и упал — из-за пяти настоящих кандидатов.
        cur = await conn.execute(
            "SELECT specialty, districts FROM product.candidate_profiles "
            "WHERE status = 'active' AND id = %s",
            (w["candidate_id"],),
        )
        rows = await cur.fetchall()
        assert len(rows) == 1 and rows[0]["specialty"] == "procedural_nurse"

        with pytest.raises(Exception) as exc:
            await conn.execute("SELECT phone FROM product.candidate_contacts")
        assert "denied" in str(exc.value).lower()


async def test_invitation_visible_to_both_sides_only(fixture_world):
    w = fixture_world

    async with db.scoped(clinic_id=w["clinic_a_id"], user_id=w["clinic_user_id"], member_role="owner") as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM product.invitations")
        assert (await cur.fetchone())["n"] == 1

    async with db.scoped(user_id=w["medic_user_id"]) as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM product.invitations")
        assert (await cur.fetchone())["n"] == 1

    # Третья сторона приглашения не видит вовсе
    async with db.scoped(clinic_id=w["clinic_b_id"], user_id=w["other_clinic_user_id"], member_role="owner") as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM product.invitations")
        assert (await cur.fetchone())["n"] == 0


async def test_context_does_not_leak_between_transactions(fixture_world):
    """Соединения переиспользуются из пула. Контекст ставится через
    set_config(..., is_local => true), то есть умирает вместе с транзакцией.
    Если бы он оставался, следующий запрос увидел бы данные предыдущей клиники —
    самая опасная ошибка в многотенантной системе.
    """
    w = fixture_world

    async with db.scoped(clinic_id=w["clinic_a_id"], user_id=w["clinic_user_id"], member_role="owner") as conn:
        assert await _jobs_titles(conn) == ["TEST процедурная медсестра"]

    # Тот же пул, следующая транзакция без контекста
    for _ in range(3):
        async with db.scoped() as conn:
            assert await _jobs_titles(conn) == [], "контекст протёк между транзакциями"

    async with db.scoped(clinic_id=w["clinic_b_id"], user_id=w["other_clinic_user_id"], member_role="owner") as conn:
        assert await _jobs_titles(conn) == ["TEST вакансия другой клиники"]


async def test_rls_is_enabled_on_all_tenant_tables():
    """Страховка от новой таблицы, которую забыли закрыть политикой."""
    rows = await db.fetch_all(
        """
        SELECT c.relname AS table_name, c.relrowsecurity AS rls
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'product' AND c.relkind = 'r'
          AND c.relname IN ('clinics','clinic_members','clinic_units','staff_positions',
                            'jobs','matches','invitations','consent_events',
                            'intake_sessions','candidate_profiles')
        ORDER BY 1
        """
    )
    assert len(rows) == 10
    off = [r["table_name"] for r in rows if not r["rls"]]
    assert off == [], f"RLS выключен на: {off}"
