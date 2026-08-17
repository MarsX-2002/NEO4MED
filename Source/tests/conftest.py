"""Общая обвязка тестов.

Тесты идут против РЕАЛЬНОЙ базы на сервере через туннель, а не против моков.
Половина того, что мы проверяем, — это права доступа, RLS и ограничения самой
БД. На моках такие вещи не тестируются: там они всегда «работают».

Разделение подключений принципиальное:
  * подготовка и уборка данных — под ВЛАДЕЛЬЦЕМ (DATABASE_URL). Владелец
    обходит RLS и имеет права на закрытые таблицы, поэтому засеять мир может
    только он;
  * сами проверки — под прикладной ролью (APP_DATABASE_URL), то есть ровно в
    тех условиях, в которых работают бот и веб.

Тестовые пользователи получают отрицательные telegram_user_id: реальные
Telegram ID всегда положительные, коллизия с живыми данными исключена.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import psycopg
import pytest
import pytest_asyncio
from psycopg.rows import dict_row

from app import db
from app.config import settings

TEST_TG_ID_BASE = -900_000
# Домен тестовых адресов. НЕ .invalid и НЕ .example: pydantic EmailStr
# отклоняет служебные TLD, и HTTP-вход отвечал бы 422 вместо 401 — тесты
# HTTP-слоя это поймали. Домен не существует, но валиден по форме.
TEST_EMAIL_DOMAIN = "@ishmed-tests.uz"


def _admin_dsn() -> str:
    admin = settings().admin_database_url
    if admin is None:
        pytest.skip("нет DATABASE_URL владельца — подготовка тестовых данных невозможна")
    return admin.get_secret_value()


async def admin_execute(sql: str, params: tuple | None = None) -> None:
    async with await psycopg.AsyncConnection.connect(_admin_dsn(), autocommit=True) as conn:
        await conn.execute(sql, params)


async def admin_fetch_one(sql: str, params: tuple | None = None) -> dict | None:
    async with await psycopg.AsyncConnection.connect(
        _admin_dsn(), autocommit=True, row_factory=dict_row
    ) as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchone()


async def _purge() -> None:
    """Убирает всё, что могли создать тесты.

    Каждый DELETE отдельным вызовом: psycopg не отправляет несколько команд
    в одном запросе с параметрами. Порядок ручной, а не через CASCADE — так
    сразу заметно, если появится таблица, о которой уборка не знает.
    """
    args = {"base": TEST_TG_ID_BASE, "mail": f"%{TEST_EMAIL_DOMAIN}"}
    statements = [
        """DELETE FROM product.consent_events
            WHERE actor_user_id IN (SELECT id FROM product.users
                                     WHERE telegram_user_id <= %(base)s
                                        OR email LIKE %(mail)s)""",
        """DELETE FROM product.candidate_contacts
            WHERE candidate_id IN (SELECT c.id FROM product.candidate_profiles c
                                    JOIN product.users u ON u.id = c.user_id
                                   WHERE u.telegram_user_id <= %(base)s)""",
        # Интервью и отклики уходят каскадом от вакансий, а вакансии — от
        # клиники. Кандидатов удаляем явно: они не привязаны к клинике.
        """DELETE FROM product.candidate_profiles
            WHERE user_id IN (SELECT id FROM product.users
                               WHERE telegram_user_id <= %(base)s)""",
        "DELETE FROM product.clinics WHERE name LIKE 'TEST %%'",
        """DELETE FROM product.users
            WHERE telegram_user_id <= %(base)s OR email LIKE %(mail)s""",
    ]
    async with await psycopg.AsyncConnection.connect(_admin_dsn(), autocommit=True) as conn:
        for sql in statements:
            await conn.execute(sql, args)


@pytest_asyncio.fixture(scope="function")
async def clean_test_users() -> AsyncIterator[None]:
    await _purge()
    yield
    await _purge()


@pytest_asyncio.fixture(scope="function")
async def fixture_world() -> AsyncIterator[dict]:
    """Минимальный мир для проверки приватности и изоляции:

      клиника A -- вакансия -- приглашение --> медик (профиль + контакт)
      клиника B -- своя вакансия, к приглашению отношения не имеет

    Приглашение создаётся в статусе sent: именно из этого состояния контакт
    обязан быть закрыт.
    """
    await _purge()

    row = await admin_fetch_one(
        """
        WITH clinic_a AS (
            INSERT INTO product.clinics (name, is_demo)
            VALUES ('TEST clinic A', true) RETURNING id
        ), clinic_b AS (
            INSERT INTO product.clinics (name, is_demo)
            VALUES ('TEST clinic B', true) RETURNING id
        ), user_a AS (
            INSERT INTO product.users (role, email, locale, consent_at, consent_version)
            VALUES ('clinic_user', %(mail_a)s, 'ru', now(), 'test') RETURNING id
        ), user_b AS (
            INSERT INTO product.users (role, email, locale, consent_at, consent_version)
            VALUES ('clinic_user', %(mail_b)s, 'ru', now(), 'test') RETURNING id
        ), medic AS (
            INSERT INTO product.users (role, telegram_user_id, locale, consent_at, consent_version)
            VALUES ('medic', %(tg)s, 'ru', now(), 'test') RETURNING id
        ), member_a AS (
            INSERT INTO product.clinic_members (clinic_id, user_id, role)
            SELECT clinic_a.id, user_a.id, 'owner' FROM clinic_a, user_a RETURNING clinic_id
        ), member_b AS (
            INSERT INTO product.clinic_members (clinic_id, user_id, role)
            SELECT clinic_b.id, user_b.id, 'owner' FROM clinic_b, user_b RETURNING clinic_id
        ), profile AS (
            INSERT INTO product.candidate_profiles
                (user_id, role_category, specialty, experience_months, skills,
                 languages, districts, schedule, salary_min_uzs, status, source)
            SELECT medic.id, 'nurse', 'procedural_nurse', 48,
                   ARRAY['injections','iv_therapy'], ARRAY['uz','ru'],
                   ARRAY['chilanzar','uchtepa'], ARRAY['shift'], 4000000,
                   'active', 'text'
            FROM medic RETURNING id
        ), contact AS (
            INSERT INTO product.candidate_contacts (candidate_id, phone, telegram_username)
            SELECT profile.id, '998901234567', 'demo_nurse' FROM profile RETURNING candidate_id
        ), job_a AS (
            INSERT INTO product.jobs
                (clinic_id, title, role_category, specialty, experience_min_months,
                 required_skills, required_languages, districts, schedule,
                 salary_min_uzs, salary_max_uzs, status, created_by)
            SELECT clinic_a.id, 'TEST процедурная медсестра', 'nurse', 'procedural_nurse', 24,
                   ARRAY['injections'], ARRAY['uz'], ARRAY['chilanzar'], ARRAY['shift'],
                   4000000, 6000000, 'active', user_a.id
            FROM clinic_a, user_a RETURNING id
        ), job_b AS (
            INSERT INTO product.jobs
                (clinic_id, title, role_category, specialty, status, created_by)
            SELECT clinic_b.id, 'TEST вакансия другой клиники', 'nurse', 'ward_nurse',
                   'active', user_b.id
            FROM clinic_b, user_b RETURNING id
        ), invitation AS (
            INSERT INTO product.invitations (job_id, candidate_id, status)
            SELECT job_a.id, profile.id, 'sent' FROM job_a, profile RETURNING id
        )
        SELECT (SELECT id FROM clinic_a)   AS clinic_a_id,
               (SELECT id FROM clinic_b)   AS clinic_b_id,
               (SELECT id FROM user_a)     AS clinic_user_id,
               (SELECT id FROM user_b)     AS other_clinic_user_id,
               (SELECT id FROM medic)      AS medic_user_id,
               (SELECT id FROM profile)    AS candidate_id,
               (SELECT id FROM job_a)      AS job_a_id,
               (SELECT id FROM job_b)      AS job_b_id,
               (SELECT id FROM invitation) AS invitation_id,
               (SELECT candidate_id FROM contact) AS contact_of,
               (SELECT clinic_id FROM member_a)   AS member_a_clinic,
               (SELECT clinic_id FROM member_b)   AS member_b_clinic
        """,
        {
            "mail_a": f"clinic-a{TEST_EMAIL_DOMAIN}",
            "mail_b": f"clinic-b{TEST_EMAIL_DOMAIN}",
            "tg": TEST_TG_ID_BASE - 10,
        },
    )
    assert row is not None
    yield {k: (str(v) if hasattr(v, "hex") else v) for k, v in row.items()}
    await _purge()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def close_db_pool() -> AsyncIterator[None]:
    yield
    await db.close_pool()


# Пароль для тестового аккаунта. Не секрет: живёт только в тестовой БД-записи,
# которая создаётся и удаляется в рамках одного теста.
TEST_PASSWORD = "test-Password-2026!"


@pytest_asyncio.fixture(scope="function")
async def clinic_account() -> AsyncIterator[dict]:
    """Клиника с сотрудником, у которого задан пароль.

    Хэш считаем настоящим argon2: ограничение ck_credentials_hash_is_argon2
    всё равно не пропустит подделку, а тест обязан ходить тем же путём,
    что и продакшен.
    """
    from argon2 import PasswordHasher

    await _purge()
    email = f"login-test{TEST_EMAIL_DOMAIN}"

    row = await admin_fetch_one(
        """
        WITH cl AS (
            INSERT INTO product.clinics (name, is_demo) VALUES ('TEST login clinic', true)
            RETURNING id
        ), u AS (
            INSERT INTO product.users (role, email, locale, consent_at, consent_version)
            VALUES ('clinic_user', %(email)s, 'ru', now(), 'test') RETURNING id
        ), cr AS (
            INSERT INTO product.user_credentials (user_id, password_hash)
            SELECT u.id, %(hash)s FROM u RETURNING user_id
        ), m AS (
            INSERT INTO product.clinic_members (clinic_id, user_id, role)
            SELECT cl.id, u.id, 'owner' FROM cl, u RETURNING clinic_id
        )
        SELECT (SELECT id FROM cl)::text AS clinic_id,
               (SELECT id FROM u)        AS user_id,
               (SELECT user_id FROM cr)  AS cred_user,
               (SELECT clinic_id FROM m)::text AS member_clinic
        """,
        {"email": email, "hash": PasswordHasher().hash(TEST_PASSWORD)},
    )
    assert row is not None
    yield {"email": email, "password": TEST_PASSWORD, **row}
    await _purge()
