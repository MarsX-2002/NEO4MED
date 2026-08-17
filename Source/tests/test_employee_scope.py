"""Сотрудник не видит кабинет клиники.

Эти тесты появились после того, как я сам открыл дыру: чтобы сотрудник попал
в контекст своей клиники и увидел курсы, приглашение добавляло его в
clinic_members с ролью recruiter. RLS различает тенанты, а не роли — и
сотрудник получил список коллег, отзывы пациентов и вакансии. Проверено на
живом аккаунте, /api/employees отдавал ему всё.

Теперь у управленческих таблиц два рубежа: политика требует product.is_manager()
и роут отдаёт 403. Тесты закрепляют оба.
"""
from __future__ import annotations

import httpx
import pytest

from app import db
from app.web.main import app
from tests.conftest import TEST_PASSWORD, admin_execute, admin_fetch_one

pytestmark = pytest.mark.asyncio

# Таблицы, которые сотрудник не должен видеть ни при каком контексте.
MANAGEMENT_TABLES = [
    "product.employees",
    "product.reviews",
    "product.review_targets",
    "product.jobs",
    "product.staff_positions",
    "product.clinic_members",
    "product.kb_documents",
]


@pytest.fixture
async def employee_account(clinic_account):
    """Сотрудник с доступом в портал, заведённый через реальное приглашение."""
    import hashlib
    import secrets

    from argon2 import PasswordHasher

    a = clinic_account
    token = secrets.token_urlsafe(24)
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    row = await admin_fetch_one(
        """
        WITH e AS (
            INSERT INTO product.employees (clinic_id, full_name, role_category, status)
            VALUES (%(clinic)s, 'TEST сотрудник портала', 'nurse', 'active')
            RETURNING id
        ), i AS (
            INSERT INTO product.employee_invites (clinic_id, employee_id, token_hash, expires_at)
            SELECT %(clinic)s, e.id, %(hash)s, now() + interval '1 day' FROM e
            RETURNING id
        )
        SELECT (SELECT id FROM e)::text AS employee_id
        """,
        {"clinic": a["clinic_id"], "hash": token_hash},
    )
    assert row is not None

    email = "portal-test@ishmed-tests.uz"
    await admin_execute("DELETE FROM product.users WHERE lower(email) = %s", (email,))
    user = await admin_fetch_one(
        "SELECT product.accept_employee_invite(%s, %s, %s) AS user_id",
        (token_hash, email, PasswordHasher().hash(TEST_PASSWORD)),
    )
    assert user is not None

    yield {**a, "employee_id": row["employee_id"], "employee_email": email,
           "employee_user_id": user["user_id"]}

    await admin_execute("DELETE FROM product.users WHERE lower(email) = %s", (email,))


# ── Рубеж БД ──────────────────────────────────────────────────────────────────

async def test_invite_grants_employee_role_not_recruiter(employee_account):
    """Именно это и было причиной дыры: роль recruiter давала полный доступ."""
    row = await admin_fetch_one(
        """
        SELECT cm.role::text AS role
        FROM product.clinic_members cm
        WHERE cm.user_id = %s
        """,
        (employee_account["employee_user_id"],),
    )
    assert row is not None
    assert row["role"] == "employee"


@pytest.mark.parametrize("table", MANAGEMENT_TABLES)
async def test_employee_context_sees_no_management_data(employee_account, table: str):
    w = employee_account
    async with db.scoped(
        clinic_id=w["clinic_id"], user_id=w["employee_user_id"], member_role="employee"
    ) as conn:
        cur = await conn.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — таблицы из списка выше
        assert (await cur.fetchone())["n"] == 0, f"сотрудник видит {table}"


async def test_manager_context_still_sees_data(employee_account):
    """Обратная сторона: закрыв доступ сотруднику, нельзя ослепить менеджера.
    Первая версия правки именно это и сделала."""
    w = employee_account
    async with db.scoped(
        clinic_id=w["clinic_id"], user_id=w["user_id"], member_role="owner"
    ) as conn:
        cur = await conn.execute("SELECT product.is_manager() AS m")
        assert (await cur.fetchone())["m"] is True
        cur = await conn.execute("SELECT count(*) AS n FROM product.employees")
        assert (await cur.fetchone())["n"] >= 1


async def test_role_defaults_to_least_privilege(employee_account):
    """Контекст без роли обязан вести себя как сотрудник, а не как менеджер:
    неизвестность должна уменьшать права."""
    w = employee_account
    async with db.scoped(clinic_id=w["clinic_id"], user_id=w["user_id"]) as conn:
        cur = await conn.execute("SELECT product.is_manager() AS m")
        assert (await cur.fetchone())["m"] is False
        cur = await conn.execute("SELECT count(*) AS n FROM product.employees")
        assert (await cur.fetchone())["n"] == 0


async def test_employee_sees_own_card_through_function(employee_account):
    """Свои данные сотруднику нужны, и они отдаются узкой функцией, а не
    доступом к product.employees."""
    w = employee_account
    async with db.scoped(
        clinic_id=w["clinic_id"], user_id=w["employee_user_id"], member_role="employee"
    ) as conn:
        cur = await conn.execute("SELECT * FROM product.my_employee_card()")
        rows = await cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["full_name"] == "TEST сотрудник портала"


# ── Рубеж HTTP ────────────────────────────────────────────────────────────────

@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://app.ishmed.test") as c:
        yield c


@pytest.mark.parametrize(
    "path",
    ["/api/structure", "/api/employees", "/api/reviews", "/api/reviews/targets"],
)
async def test_management_routes_forbidden_for_employee(employee_account, client, path: str):
    w = employee_account
    r = await client.post(
        "/api/auth/login", json={"email": w["employee_email"], "password": TEST_PASSWORD}
    )
    assert r.status_code == 200, r.text

    r = await client.get(path)
    assert r.status_code == 403, f"{path} отдал {r.status_code} вместо 403"
    assert "только сотрудникам кабинета" in r.json()["detail"]


async def test_management_routes_allowed_for_manager(employee_account, client):
    w = employee_account
    r = await client.post(
        "/api/auth/login", json={"email": w["email"], "password": TEST_PASSWORD}
    )
    assert r.status_code == 200
    r = await client.get("/api/employees")
    assert r.status_code == 200
    assert "employees" in r.json()
