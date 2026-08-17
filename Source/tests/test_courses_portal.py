"""Портал сотрудника: свои курсы, свой тест, свои отзывы.

Тесты идут против настоящей базы, потому что проверяют не питон, а права:
у прикладной роли отозвано чтение `course_options.is_correct`, назначения и
попытки закрыты политиками, а проверка теста живёт в SECURITY DEFINER функции.
На моках всё это «работает» всегда.

Здесь закреплены три ошибки, найденные при сборке портала:

  1. политика из 022 определяла «своё» подзапросом к `product.employees`,
     а та закрыта от роли employee — сотрудник не видел даже собственного
     назначения (исправлено в 038);
  2. `grade_attempt` не проверяла владельца: зная uuid, можно было сдать
     чужую попытку (037);
  3. `course_answer_key` отдавала правильные ответы любому в контексте
     клиники, включая того, кто этот тест сдаёт (037).
"""
from __future__ import annotations

import hashlib
import secrets

import httpx
import psycopg
import pytest
from argon2 import PasswordHasher

from app import db
from app.web.main import app
from tests.conftest import TEST_PASSWORD, admin_execute, admin_fetch_one

pytestmark = pytest.mark.asyncio

EMPLOYEE_EMAIL = "portal-courses@ishmed-tests.uz"


@pytest.fixture
async def world(clinic_account):
    """Клиника, двое в штате, у одного вход в портал, курс на обоих.

    Курс создаётся полностью: два урока, два вопроса по два варианта. Меньше
    нельзя — тест на «сдал/не сдал» требует, чтобы верный вариант отличался
    от неверного.
    """
    a = clinic_account
    token_hash = hashlib.sha256(secrets.token_urlsafe(24).encode()).hexdigest()

    await admin_execute("DELETE FROM product.users WHERE lower(email) = %s", (EMPLOYEE_EMAIL,))

    row = await admin_fetch_one(
        """
        WITH me AS (
            INSERT INTO product.employees (clinic_id, full_name, role_category, status)
            VALUES (%(clinic)s, 'TEST я сам', 'nurse', 'active') RETURNING id
        ), other AS (
            INSERT INTO product.employees (clinic_id, full_name, role_category, status)
            VALUES (%(clinic)s, 'TEST коллега', 'nurse', 'active') RETURNING id
        ), inv AS (
            INSERT INTO product.employee_invites (clinic_id, employee_id, token_hash, expires_at)
            SELECT %(clinic)s, me.id, %(hash)s, now() + interval '1 day' FROM me RETURNING id
        ), c AS (
            INSERT INTO product.courses (clinic_id, title, summary, pass_score, status)
            VALUES (%(clinic)s, 'TEST инфекционная безопасность', 'демо', 80, 'published')
            RETURNING id
        ), l AS (
            INSERT INTO product.course_lessons (course_id, clinic_id, position, title, content)
            SELECT c.id, %(clinic)s, g, 'Урок ' || g, 'Текст урока ' || g
              FROM c, generate_series(1, 2) g
            RETURNING id
        ), q AS (
            INSERT INTO product.course_questions
                (course_id, clinic_id, position, text, explanation)
            SELECT c.id, %(clinic)s, g, 'Вопрос ' || g, 'Потому что так в уроке'
              FROM c, generate_series(1, 2) g
            RETURNING id, position
        ), o_right AS (
            INSERT INTO product.course_options (question_id, clinic_id, position, text, is_correct)
            SELECT q.id, %(clinic)s, 1, 'верный ' || q.position, true FROM q
            RETURNING id
        ), o_wrong AS (
            INSERT INTO product.course_options (question_id, clinic_id, position, text, is_correct)
            SELECT q.id, %(clinic)s, 2, 'неверный ' || q.position, false FROM q
            RETURNING id
        ), asg_me AS (
            INSERT INTO product.course_assignments
                (clinic_id, course_id, employee_id, due_at)
            SELECT %(clinic)s, c.id, me.id, current_date + 7 FROM c, me RETURNING id
        ), asg_other AS (
            INSERT INTO product.course_assignments (clinic_id, course_id, employee_id)
            SELECT %(clinic)s, c.id, other.id FROM c, other RETURNING id
        ), att_other AS (
            INSERT INTO product.course_attempts (clinic_id, assignment_id, employee_id)
            SELECT %(clinic)s, asg_other.id, other.id FROM asg_other, other RETURNING id
        )
        SELECT (SELECT id FROM me)::text        AS my_employee_id,
               (SELECT id FROM other)::text     AS other_employee_id,
               (SELECT id FROM c)::text         AS course_id,
               (SELECT id FROM asg_me)::text    AS my_assignment_id,
               (SELECT id FROM asg_other)::text AS other_assignment_id,
               (SELECT id FROM att_other)::text AS other_attempt_id
        """,
        {"clinic": a["clinic_id"], "hash": token_hash},
    )
    assert row is not None

    user = await admin_fetch_one(
        "SELECT product.accept_employee_invite(%s, %s, %s) AS user_id",
        (token_hash, EMPLOYEE_EMAIL, PasswordHasher().hash(TEST_PASSWORD)),
    )
    assert user is not None

    yield {**a, **row, "employee_email": EMPLOYEE_EMAIL, "employee_user_id": user["user_id"]}

    await admin_execute("DELETE FROM product.users WHERE lower(email) = %s", (EMPLOYEE_EMAIL,))


def employee_scope(w):
    return db.scoped(
        clinic_id=w["clinic_id"], user_id=w["employee_user_id"], member_role="employee"
    )


def manager_scope(w):
    return db.scoped(clinic_id=w["clinic_id"], user_id=w["user_id"], member_role="owner")


# ── Рубеж БД ──────────────────────────────────────────────────────────────────

async def test_employee_sees_own_assignment(world):
    """Регрессия на 038: подзапрос политики к закрытой таблице делал своё
    назначение невидимым, и портал отдавал пустой список при живых данных."""
    async with employee_scope(world) as conn:
        cur = await conn.execute(
            "SELECT id::text FROM product.course_assignments"
        )
        rows = await cur.fetchall()
    assert [r["id"] for r in rows] == [world["my_assignment_id"]]


async def test_employee_does_not_see_colleague_assignment(world):
    async with employee_scope(world) as conn:
        cur = await conn.execute(
            "SELECT count(*) AS n FROM product.course_assignments WHERE id = %s",
            (world["other_assignment_id"],),
        )
        assert (await cur.fetchone())["n"] == 0


async def test_manager_sees_all_assignments(world):
    async with manager_scope(world) as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM product.course_assignments")
        assert (await cur.fetchone())["n"] == 2


async def test_employee_cannot_read_correct_flag(world):
    """Главная гарантия схемы обучения: колонки просто нет для этой роли."""
    async with employee_scope(world) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("SELECT is_correct FROM product.course_options LIMIT 1")


async def test_answer_key_only_for_manager(world):
    async with employee_scope(world) as conn:
        cur = await conn.execute(
            "SELECT count(*) AS n FROM product.course_answer_key(%s)", (world["course_id"],)
        )
        assert (await cur.fetchone())["n"] == 0, "сотрудник получил ключ к тесту"

    async with manager_scope(world) as conn:
        cur = await conn.execute(
            "SELECT count(*) AS n FROM product.course_answer_key(%s)", (world["course_id"],)
        )
        assert (await cur.fetchone())["n"] == 4


async def test_employee_cannot_mark_himself_passed(world):
    """Статус назначения двигают только функции БД. Иначе достаточно одного
    UPDATE, чтобы «пройти» обучение.

    Своё назначение сотрудник читает, поэтому строка находится, и отказ приходит
    от WITH CHECK — ошибкой, а не пустым результатом.
    """
    async with employee_scope(world) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute(
                "UPDATE product.course_assignments SET status = 'passed' WHERE id = %s",
                (world["my_assignment_id"],),
            )


async def test_cannot_grade_someone_elses_attempt(world):
    """Регрессия на 037: раньше хватало знать uuid чужой попытки."""
    async with employee_scope(world) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute(
                "SELECT * FROM product.grade_attempt(%s, '{}'::jsonb)",
                (world["other_attempt_id"],),
            )


async def test_cannot_read_someone_elses_attempt_questions(world):
    async with employee_scope(world) as conn:
        cur = await conn.execute(
            "SELECT count(*) AS n FROM product.attempt_questions(%s)",
            (world["other_attempt_id"],),
        )
        assert (await cur.fetchone())["n"] == 0


async def test_start_attempt_is_idempotent(world):
    """Перезагрузка страницы посреди теста не должна плодить попытки."""
    async with employee_scope(world) as conn:
        cur = await conn.execute(
            "SELECT product.start_course_attempt(%s) AS id", (world["course_id"],)
        )
        first = str((await cur.fetchone())["id"])
        cur = await conn.execute(
            "SELECT product.start_course_attempt(%s) AS id", (world["course_id"],)
        )
        second = str((await cur.fetchone())["id"])
    assert first == second


async def test_start_attempt_refuses_unassigned_course(world):
    """Курс есть в клинике, но не назначен — проходить нечего."""
    other = await admin_fetch_one(
        """
        INSERT INTO product.courses (clinic_id, title, pass_score, status)
        VALUES (%s, 'TEST чужой курс', 80, 'published') RETURNING id::text
        """,
        (world["clinic_id"],),
    )
    assert other is not None
    async with employee_scope(world) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("SELECT product.start_course_attempt(%s)", (other["id"],))


# ── Свои отзывы ───────────────────────────────────────────────────────────────

async def test_my_reviews_only_about_me(world):
    """Отзыв о коллеге сотруднику не показывается, а телефон пациента не
    выходит наружу вообще: его нет в выдаче функции."""
    await admin_execute(
        """
        WITH t_me AS (
            INSERT INTO product.review_targets (clinic_id, kind, employee_id, title)
            VALUES (%(clinic)s, 'employee', %(me)s, 'TEST я сам') RETURNING id
        ), t_other AS (
            INSERT INTO product.review_targets (clinic_id, kind, employee_id, title)
            VALUES (%(clinic)s, 'employee', %(other)s, 'TEST коллега') RETURNING id
        ), r_me AS (
            INSERT INTO product.reviews
                (clinic_id, target_id, rating, comment, contact_phone, wants_callback)
            SELECT %(clinic)s, t_me.id, 5, 'обо мне', '998900000001', true FROM t_me
            RETURNING id
        )
        INSERT INTO product.reviews (clinic_id, target_id, rating, comment)
        SELECT %(clinic)s, t_other.id, 2, 'о коллеге' FROM t_other
        """,
        {
            "clinic": world["clinic_id"],
            "me": world["my_employee_id"],
            "other": world["other_employee_id"],
        },
    )

    async with employee_scope(world) as conn:
        cur = await conn.execute("SELECT * FROM product.my_reviews(100)")
        rows = await cur.fetchall()
        cur = await conn.execute("SELECT * FROM product.my_review_stats()")
        stats = await cur.fetchone()

    assert [r["comment"] for r in rows] == ["обо мне"]
    assert "contact_phone" not in rows[0]
    assert stats["total"] == 1


async def test_reviews_table_still_closed_for_employee(world):
    """Функция открыла ровно одну дверь, а не таблицу целиком."""
    async with employee_scope(world) as conn:
        cur = await conn.execute("SELECT count(*) AS n FROM product.reviews")
        assert (await cur.fetchone())["n"] == 0


# ── Рубеж HTTP ────────────────────────────────────────────────────────────────

@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://app.ishmed.test") as c:
        yield c


async def _login(client, email: str) -> None:
    r = await client.post("/api/auth/login", json={"email": email, "password": TEST_PASSWORD})
    assert r.status_code == 200, r.text


async def test_login_returns_member_role(world, client):
    """Интерфейс выбирает кабинет или портал по этому полю."""
    r = await client.post(
        "/api/auth/login", json={"email": world["employee_email"], "password": TEST_PASSWORD}
    )
    assert r.json()["member_role"] == "employee"
    r = await client.get("/api/auth/me")
    assert r.json()["member_role"] == "employee"


async def test_portal_full_pass(world, client):
    """Сквозной проход: курсы → материал → тест → сдал."""
    await _login(client, world["employee_email"])

    r = await client.get("/api/portal/courses")
    assert r.status_code == 200, r.text
    courses = r.json()["courses"]
    assert len(courses) == 1
    assert courses[0]["course_id"] == world["course_id"]
    assert courses[0]["status"] == "assigned"

    r = await client.get(f"/api/portal/courses/{world['course_id']}")
    assert r.status_code == 200
    assert len(r.json()["lessons"]) == 2

    r = await client.post(f"/api/portal/courses/{world['course_id']}/attempt")
    assert r.status_code == 200, r.text
    attempt = r.json()
    assert len(attempt["questions"]) == 2
    # Правильность варианта не должна доехать до браузера ни под каким именем.
    assert "is_correct" not in r.text

    answers = {
        q["question_id"]: next(o["id"] for o in q["options"] if o["text"].startswith("верный"))
        for q in attempt["questions"]
    }
    r = await client.post(
        f"/api/portal/attempts/{attempt['attempt_id']}/submit", json={"answers": answers}
    )
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["score"] == 100
    assert result["passed"] is True
    assert len(result["review"]) == 2
    assert all(item["is_right"] for item in result["review"])

    # Статус назначения обновила функция БД, а не приложение.
    r = await client.get("/api/portal/courses")
    assert r.json()["courses"][0]["status"] == "passed"
    assert r.json()["courses"][0]["best_score"] == 100


async def test_portal_failed_attempt_keeps_course_open(world, client):
    await _login(client, world["employee_email"])
    r = await client.post(f"/api/portal/courses/{world['course_id']}/attempt")
    attempt = r.json()
    answers = {
        q["question_id"]: next(o["id"] for o in q["options"] if o["text"].startswith("неверный"))
        for q in attempt["questions"]
    }
    r = await client.post(
        f"/api/portal/attempts/{attempt['attempt_id']}/submit", json={"answers": answers}
    )
    assert r.json()["score"] == 0
    assert r.json()["passed"] is False

    r = await client.get("/api/portal/courses")
    assert r.json()["courses"][0]["status"] == "failed"


async def test_second_submit_of_same_attempt_rejected(world, client):
    await _login(client, world["employee_email"])
    attempt = (await client.post(f"/api/portal/courses/{world['course_id']}/attempt")).json()
    body = {"answers": {}}
    first = await client.post(f"/api/portal/attempts/{attempt['attempt_id']}/submit", json=body)
    assert first.status_code == 200
    second = await client.post(f"/api/portal/attempts/{attempt['attempt_id']}/submit", json=body)
    assert second.status_code == 409


async def test_portal_reviews_endpoint(world, client):
    await admin_execute(
        """
        WITH t AS (
            INSERT INTO product.review_targets (clinic_id, kind, employee_id, title)
            VALUES (%(clinic)s, 'employee', %(me)s, 'TEST я сам') RETURNING id
        )
        INSERT INTO product.reviews (clinic_id, target_id, rating, comment, bad_tags)
        SELECT %(clinic)s, t.id, 2, 'долго ждал', ARRAY['waiting'] FROM t
        """,
        {"clinic": world["clinic_id"], "me": world["my_employee_id"]},
    )
    await _login(client, world["employee_email"])
    r = await client.get("/api/portal/reviews")
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["reviews"]) == 1
    assert body["summary"]["low"] == 1
    # Словарь тегов нужен, чтобы портал показал название, а не код: раздел
    # /api/structure/dictionaries сотруднику отвечает 403.
    assert any(tag["code"] == "waiting" for tag in body["tags"])
    assert "contact_phone" not in r.text


@pytest.mark.parametrize("path", ["/api/courses", "/api/courses/results"])
async def test_manager_course_routes_forbidden_for_employee(world, client, path):
    await _login(client, world["employee_email"])
    r = await client.get(path)
    assert r.status_code == 403


async def test_manager_sees_courses_and_results(world, client):
    await _login(client, world["email"])
    r = await client.get("/api/courses")
    assert r.status_code == 200, r.text
    course = next(c for c in r.json()["courses"] if c["id"] == world["course_id"])
    assert course["assigned"] == 2
    assert course["lessons_count"] == 2
    assert course["questions_count"] == 2

    r = await client.get(f"/api/courses/{world['course_id']}")
    assert r.status_code == 200
    # Менеджеру верный вариант нужен: он этот тест и составлял.
    options = r.json()["questions"][0]["options"]
    assert sum(1 for o in options if o["is_correct"]) == 1

    r = await client.get(f"/api/courses/results?course_id={world['course_id']}")
    assert r.status_code == 200
    assert len(r.json()["assignments"]) == 2


async def test_manager_has_no_employee_card(world, client):
    """Менеджер не в штате: портал честно говорит, что ему тут нечего делать,
    вместо пустого списка курсов."""
    await _login(client, world["email"])
    r = await client.get("/api/portal/courses")
    assert r.status_code == 404
