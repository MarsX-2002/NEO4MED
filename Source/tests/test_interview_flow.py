"""Проверки публикации вакансии и авто-интервью.

Всё, что здесь проверяется, — обещания базы, а не поведение бота. Бот можно
переписать, и он не должен получить возможность обойти эти правила:
  * публикация без одобренного менеджером плана вопросов невозможна;
  * кандидат видит только опубликованные вакансии и только разрешённые поля;
  * интервью нельзя переиграть;
  * вопросы выдаются из плана по порядку, модель их не выбирает;
  * предел ходов считает база.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import psycopg
import pytest
import pytest_asyncio

from app import db
from tests.conftest import TEST_TG_ID_BASE, _purge, admin_execute, admin_fetch_one

pytestmark = pytest.mark.asyncio

QUESTIONS = [
    (1, "Сколько лет вы работаете стоматологом-терапевтом?", "опыт"),
    (2, "С какими случаями вы сталкивались чаще всего?", "опыт"),
    (3, "Какой график работы вам подходит?", "график"),
    (4, "На какую оплату вы рассчитываете?", "оплата"),
    (5, "Есть ли у вас действующий сертификат?", "документы"),
]


@pytest_asyncio.fixture(scope="function")
async def dental_job() -> AsyncIterator[dict]:
    """Стоматологическая клиника с черновиком вакансии и медик рядом.

    Вакансия создаётся именно черновиком без плана: половина проверок в том,
    что из этого состояния публикация и интервью недоступны.
    """
    await _purge()
    row = await admin_fetch_one(
        """
        WITH clinic AS (
            INSERT INTO product.clinics (name, is_demo)
            VALUES ('TEST стоматология', true) RETURNING id
        ), manager AS (
            INSERT INTO product.users (role, email, locale, consent_at, consent_version)
            VALUES ('clinic_user', %(mail)s, 'ru', now(), 'test') RETURNING id
        ), member AS (
            INSERT INTO product.clinic_members (clinic_id, user_id, role)
            SELECT clinic.id, manager.id, 'owner' FROM clinic, manager RETURNING clinic_id
        ), medic AS (
            INSERT INTO product.users (role, telegram_user_id, locale, consent_at, consent_version)
            VALUES ('medic', %(tg)s, 'ru', now(), 'test') RETURNING id
        ), other_medic AS (
            INSERT INTO product.users (role, telegram_user_id, locale, consent_at, consent_version)
            VALUES ('medic', %(tg2)s, 'ru', now(), 'test') RETURNING id
        ), job AS (
            INSERT INTO product.jobs
                (clinic_id, title, role_category, specialty, city, status, created_by)
            SELECT clinic.id, 'TEST стоматолог-терапевт', 'doctor', 'dentist_therapist',
                   'tashkent', 'draft', manager.id
            FROM clinic, manager RETURNING id, public_code
        )
        SELECT (SELECT id FROM clinic)      AS clinic_id,
               (SELECT id FROM manager)     AS manager_id,
               (SELECT id FROM medic)       AS medic_id,
               (SELECT id FROM other_medic) AS other_medic_id,
               (SELECT id FROM job)         AS job_id,
               (SELECT public_code FROM job) AS public_code,
               (SELECT clinic_id FROM member) AS member_clinic
        """,
        {
            "mail": "dental-manager@ishmed-tests.uz",
            "tg": TEST_TG_ID_BASE - 20,
            "tg2": TEST_TG_ID_BASE - 21,
        },
    )
    assert row is not None
    yield {k: (str(v) if hasattr(v, "hex") else v) for k, v in row.items()}
    await _purge()


async def _add_plan(job_id: str, *, approve: bool = True, count: int = 5) -> None:
    for ord_, question, intent in QUESTIONS[:count]:
        await admin_execute(
            "INSERT INTO product.job_questions (job_id, ord, question, intent) "
            "VALUES (%s, %s, %s, %s)",
            (job_id, ord_, question, intent),
        )
    if approve:
        await admin_execute(
            "UPDATE product.jobs SET interview_plan_status = 'approved' WHERE id = %s",
            (job_id,),
        )


# ─────────────────────────── публикация ───────────────────────────

async def test_publish_refuses_without_plan(dental_job: dict):
    """Публикация без вопросов запрещена: кандидат придёт, а спрашивать нечего."""
    async with db.scoped(
        clinic_id=dental_job["clinic_id"], user_id=dental_job["manager_id"], member_role="owner"
    ) as conn:
        with pytest.raises(psycopg.Error) as err:
            await conn.execute("SELECT * FROM product.publish_job(%s)", (dental_job["job_id"],))
    assert "план интервью не одобрен" in str(err.value)


async def test_publish_refuses_unapproved_plan(dental_job: dict):
    """Вопросы есть, но менеджер их не одобрил — публикации нет."""
    await _add_plan(dental_job["job_id"], approve=False)
    async with db.scoped(
        clinic_id=dental_job["clinic_id"], user_id=dental_job["manager_id"], member_role="owner"
    ) as conn:
        with pytest.raises(psycopg.Error) as err:
            await conn.execute("SELECT * FROM product.publish_job(%s)", (dental_job["job_id"],))
    assert "не одобрен" in str(err.value)


async def test_publish_refuses_short_plan(dental_job: dict):
    """Два вопроса — это не интервью."""
    await _add_plan(dental_job["job_id"], approve=True, count=2)
    async with db.scoped(
        clinic_id=dental_job["clinic_id"], user_id=dental_job["manager_id"], member_role="owner"
    ) as conn:
        with pytest.raises(psycopg.Error) as err:
            await conn.execute("SELECT * FROM product.publish_job(%s)", (dental_job["job_id"],))
    assert "меньше трёх вопросов" in str(err.value)


async def test_publish_succeeds_with_approved_plan(dental_job: dict):
    await _add_plan(dental_job["job_id"])
    async with db.scoped(
        clinic_id=dental_job["clinic_id"], user_id=dental_job["manager_id"], member_role="owner"
    ) as conn:
        cur = await conn.execute("SELECT * FROM product.publish_job(%s)", (dental_job["job_id"],))
        row = await cur.fetchone()
    assert row is not None
    assert row["questions_count"] == 5
    assert len(row["public_code"]) == 10, "код для deep link должен быть из десяти символов"


# ─────────────────────────── витрина кандидата ───────────────────────────

async def test_draft_job_invisible_to_candidate(dental_job: dict):
    """Черновик не должен утекать в бота."""
    row = await db.fetch_one(
        "SELECT count(*) AS n FROM product.list_published_jobs() WHERE job_id = %s",
        (dental_job["job_id"],),
    )
    assert row is not None and row["n"] == 0


async def test_published_job_visible_by_code(dental_job: dict):
    """Deep link по коду находит вакансию и её план."""
    await _add_plan(dental_job["job_id"])
    await admin_execute("SELECT product.publish_job(%s)", (dental_job["job_id"],))
    row = await db.fetch_one(
        "SELECT * FROM product.get_published_job(%s)", (dental_job["public_code"],)
    )
    assert row is not None, "по коду вакансия должна открываться без контекста клиники"
    assert row["title"] == "TEST стоматолог-терапевт"
    assert row["clinic_name"] == "TEST стоматология"
    assert row["questions_count"] == 5


async def test_showcase_hides_internal_fields(dental_job: dict):
    """Витрина не отдаёт исходный текст, извлечение и автора.

    Это гарантия набора колонок в самой функции: даже если в коде бота забудут
    отфильтровать поля, их там просто нет.
    """
    await _add_plan(dental_job["job_id"])
    await admin_execute("SELECT product.publish_job(%s)", (dental_job["job_id"],))
    row = await db.fetch_one(
        "SELECT * FROM product.get_published_job(%s)", (dental_job["public_code"],)
    )
    assert row is not None
    for leaked in ("source_text", "extraction", "created_by", "clinic_id"):
        assert leaked not in row, f"витрина не должна отдавать {leaked}"


async def test_closed_job_leaves_showcase(dental_job: dict):
    await _add_plan(dental_job["job_id"])
    await admin_execute("SELECT product.publish_job(%s)", (dental_job["job_id"],))
    await admin_execute("SELECT product.close_job(%s)", (dental_job["job_id"],))
    row = await db.fetch_one(
        "SELECT * FROM product.get_published_job(%s)", (dental_job["public_code"],)
    )
    assert row is None, "закрытая вакансия не должна открываться по ссылке"


# ─────────────────────────── интервью ───────────────────────────

async def test_interview_refuses_unpublished_job(dental_job: dict):
    """Пока вакансия черновик, интервью начать нельзя."""
    with pytest.raises(psycopg.Error) as err:
        await db.fetch_one(
            "SELECT * FROM product.open_interview(%s, %s)",
            (dental_job["medic_id"], dental_job["job_id"]),
        )
    assert "не опубликована" in str(err.value)


async def _publish(job_id: str) -> None:
    await _add_plan(job_id)
    await admin_execute("SELECT product.publish_job(%s)", (job_id,))


async def test_open_interview_cannot_be_replayed(dental_job: dict):
    """Повторный вход возвращает то же интервью, а не начинает новое.

    Иначе кандидат мог бы проходить собеседование заново, подбирая ответы.
    """
    await _publish(dental_job["job_id"])
    first = await db.fetch_one(
        "SELECT * FROM product.open_interview(%s, %s)",
        (dental_job["medic_id"], dental_job["job_id"]),
    )
    second = await db.fetch_one(
        "SELECT * FROM product.open_interview(%s, %s)",
        (dental_job["medic_id"], dental_job["job_id"]),
    )
    assert first is not None and second is not None
    assert first["is_new"] is True
    assert second["is_new"] is False
    assert first["interview_id"] == second["interview_id"]

    count = await admin_fetch_one(
        """SELECT count(*) AS n FROM product.interviews i
           JOIN product.applications a ON a.id = i.application_id
           WHERE a.job_id = %s""",
        (dental_job["job_id"],),
    )
    assert count is not None and count["n"] == 1


async def test_questions_served_in_plan_order(dental_job: dict):
    """Вопросы идут строго по порядку плана: иначе кандидатов не сравнить."""
    await _publish(dental_job["job_id"])
    opened = await db.fetch_one(
        "SELECT * FROM product.open_interview(%s, %s)",
        (dental_job["medic_id"], dental_job["job_id"]),
    )
    assert opened is not None
    interview_id = opened["interview_id"]

    seen = []
    for _ in range(5):
        nxt = await db.fetch_one(
            "SELECT * FROM product.next_interview_question(%s)", (interview_id,)
        )
        assert nxt is not None, "план ещё не исчерпан, вопрос обязан быть"
        seen.append(nxt["ord"])
        await db.fetch_one(
            "SELECT * FROM product.ask_interview_turn(%s, %s, %s)",
            (interview_id, nxt["question_id"], nxt["question"]),
        )
        await db.fetch_one(
            "SELECT * FROM product.record_interview_answer(%s, 'text', %s)",
            (interview_id, "ответ на вопрос"),
        )

    assert seen == [1, 2, 3, 4, 5]
    after = await db.fetch_one(
        "SELECT * FROM product.next_interview_question(%s)", (interview_id,)
    )
    assert after is None, "план исчерпан, новых вопросов быть не должно"


async def test_turn_budget_is_enforced_by_database(dental_job: dict):
    """Предел ходов держит база, а не только граф.

    Ставим бюджет в четыре хода и пробуем задать пятый.
    """
    await _publish(dental_job["job_id"])
    opened = await db.fetch_one(
        "SELECT * FROM product.open_interview(%s, %s)",
        (dental_job["medic_id"], dental_job["job_id"]),
    )
    assert opened is not None
    interview_id = opened["interview_id"]
    await admin_execute(
        "UPDATE product.interviews SET turn_budget = 4 WHERE id = %s", (interview_id,)
    )

    for _ in range(4):
        await db.fetch_one(
            "SELECT * FROM product.ask_interview_turn(%s, NULL, %s)",
            (interview_id, "уточняющий вопрос"),
        )
        await db.fetch_one(
            "SELECT * FROM product.record_interview_answer(%s, 'text', %s)",
            (interview_id, "ответ"),
        )

    with pytest.raises(psycopg.Error) as err:
        await db.fetch_one(
            "SELECT * FROM product.ask_interview_turn(%s, NULL, %s)",
            (interview_id, "лишний вопрос"),
        )
    assert "предел ходов" in str(err.value)


async def test_answer_lands_on_last_unanswered_question(dental_job: dict):
    await _publish(dental_job["job_id"])
    opened = await db.fetch_one(
        "SELECT * FROM product.open_interview(%s, %s)",
        (dental_job["medic_id"], dental_job["job_id"]),
    )
    assert opened is not None
    interview_id = opened["interview_id"]

    nxt = await db.fetch_one(
        "SELECT * FROM product.next_interview_question(%s)", (interview_id,)
    )
    assert nxt is not None
    await db.fetch_one(
        "SELECT * FROM product.ask_interview_turn(%s, %s, %s)",
        (interview_id, nxt["question_id"], nxt["question"]),
    )
    await db.fetch_one(
        "SELECT * FROM product.record_interview_answer(%s, 'voice', %s, %s, %s)",
        (interview_id, "восемь лет в терапии", "tg-file-id-1", 12),
    )

    turn = await admin_fetch_one(
        "SELECT * FROM product.interview_turns WHERE interview_id = %s ORDER BY ord",
        (interview_id,),
    )
    assert turn is not None
    assert turn["answer_kind"] == "voice"
    assert turn["answer_text"] == "восемь лет в терапии"
    assert turn["voice_file_id"] == "tg-file-id-1"
    assert turn["answered_at"] is not None

    state = await db.fetch_one("SELECT * FROM product.interview_state(%s)", (interview_id,))
    assert state is not None and state["answered_count"] == 1


async def test_answer_without_question_is_rejected(dental_job: dict):
    """Ответ в пустоту не принимается: без заданного вопроса его некуда положить."""
    await _publish(dental_job["job_id"])
    opened = await db.fetch_one(
        "SELECT * FROM product.open_interview(%s, %s)",
        (dental_job["medic_id"], dental_job["job_id"]),
    )
    assert opened is not None
    with pytest.raises(psycopg.Error) as err:
        await db.fetch_one(
            "SELECT * FROM product.record_interview_answer(%s, 'text', %s)",
            (opened["interview_id"], "я готов начать"),
        )
    assert "нет заданного вопроса" in str(err.value)


async def test_complete_interview_locks_it(dental_job: dict):
    """После завершения интервью новых вопросов не выдаётся."""
    await _publish(dental_job["job_id"])
    opened = await db.fetch_one(
        "SELECT * FROM product.open_interview(%s, %s)",
        (dental_job["medic_id"], dental_job["job_id"]),
    )
    assert opened is not None
    interview_id = opened["interview_id"]

    await db.execute(
        "SELECT product.complete_interview(%s, %s, %s, %s, %s)",
        (
            interview_id,
            "Стоматолог-терапевт, восемь лет опыта.",
            '{"experience_months": 96}',
            ["не сказал о сертификате"],
            ["уточнить готовность к субботам"],
        ),
    )
    state = await db.fetch_one("SELECT * FROM product.interview_state(%s)", (interview_id,))
    assert state is not None and state["status"] == "completed"

    nxt = await db.fetch_one(
        "SELECT * FROM product.next_interview_question(%s)", (interview_id,)
    )
    assert nxt is None, "законченное интервью не должно выдавать вопросы"

    with pytest.raises(psycopg.Error):
        await db.fetch_one(
            "SELECT * FROM product.ask_interview_turn(%s, NULL, %s)",
            (interview_id, "ещё один вопрос"),
        )


# ─────────────────────────── границы видимости ───────────────────────────

async def test_clinic_sees_own_interview_transcript(dental_job: dict):
    """Клиника читает саммари и полный транскрипт своего интервью."""
    await _publish(dental_job["job_id"])
    opened = await db.fetch_one(
        "SELECT * FROM product.open_interview(%s, %s)",
        (dental_job["medic_id"], dental_job["job_id"]),
    )
    assert opened is not None
    interview_id = opened["interview_id"]
    nxt = await db.fetch_one(
        "SELECT * FROM product.next_interview_question(%s)", (interview_id,)
    )
    assert nxt is not None
    await db.fetch_one(
        "SELECT * FROM product.ask_interview_turn(%s, %s, %s)",
        (interview_id, nxt["question_id"], nxt["question"]),
    )
    await db.fetch_one(
        "SELECT * FROM product.record_interview_answer(%s, 'text', %s)",
        (interview_id, "восемь лет"),
    )
    await db.execute(
        "SELECT product.complete_interview(%s, %s)", (interview_id, "Опытный терапевт.")
    )

    async with db.scoped(
        clinic_id=dental_job["clinic_id"], user_id=dental_job["manager_id"], member_role="owner"
    ) as conn:
        cur = await conn.execute(
            "SELECT summary FROM product.interviews WHERE id = %s", (interview_id,)
        )
        row = await cur.fetchone()
        assert row is not None and row["summary"] == "Опытный терапевт."

        cur = await conn.execute(
            "SELECT count(*) AS n FROM product.interview_turns WHERE interview_id = %s",
            (interview_id,),
        )
        turns = await cur.fetchone()
        assert turns is not None and turns["n"] == 1, "клиника должна видеть транскрипт"


async def test_other_clinic_cannot_read_interview(dental_job: dict):
    """Чужое интервью не видно даже менеджеру другой клиники."""
    await _publish(dental_job["job_id"])
    opened = await db.fetch_one(
        "SELECT * FROM product.open_interview(%s, %s)",
        (dental_job["medic_id"], dental_job["job_id"]),
    )
    assert opened is not None

    other = await admin_fetch_one(
        "INSERT INTO product.clinics (name, is_demo) VALUES ('TEST чужая клиника', true) "
        "RETURNING id"
    )
    assert other is not None
    async with db.scoped(
        clinic_id=str(other["id"]), user_id=dental_job["manager_id"], member_role="owner"
    ) as conn:
        cur = await conn.execute(
            "SELECT count(*) AS n FROM product.interviews WHERE id = %s",
            (opened["interview_id"],),
        )
        row = await cur.fetchone()
    assert row is not None and row["n"] == 0, "RLS обязана скрыть интервью чужой вакансии"


async def test_candidate_sees_only_own_interview(dental_job: dict):
    """Кандидат видит своё интервью и не видит чужое."""
    await _publish(dental_job["job_id"])
    mine = await db.fetch_one(
        "SELECT * FROM product.open_interview(%s, %s)",
        (dental_job["medic_id"], dental_job["job_id"]),
    )
    assert mine is not None

    async with db.scoped(user_id=dental_job["medic_id"]) as conn:
        cur = await conn.execute(
            "SELECT count(*) AS n FROM product.interviews WHERE id = %s", (mine["interview_id"],)
        )
        row = await cur.fetchone()
        assert row is not None and row["n"] == 1, "своё интервью кандидат читать должен"

    async with db.scoped(user_id=dental_job["other_medic_id"]) as conn:
        cur = await conn.execute(
            "SELECT count(*) AS n FROM product.interviews WHERE id = %s", (mine["interview_id"],)
        )
        row = await cur.fetchone()
        assert row is not None and row["n"] == 0, "чужое интервью кандидату недоступно"


async def test_manager_role_required_for_question_plan(dental_job: dict):
    """План вопросов правит только менеджер: сотруднику он недоступен."""
    await _add_plan(dental_job["job_id"])
    async with db.scoped(
        clinic_id=dental_job["clinic_id"],
        user_id=dental_job["manager_id"],
        member_role="employee",
    ) as conn:
        cur = await conn.execute(
            "SELECT count(*) AS n FROM product.job_questions WHERE job_id = %s",
            (dental_job["job_id"],),
        )
        row = await cur.fetchone()
    assert row is not None and row["n"] == 0, "сотрудник не должен видеть план интервью"
