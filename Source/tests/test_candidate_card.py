"""Карточка кандидата и телефон: путь от интервью до менеджера.

До миграций 034–035 этот путь был разорван в трёх местах, и каждое стоило
демонстрации:

  * разобранное на интервью оставалось в `interviews.extraction`, а профиль
    кандидата стоял пустым черновиком;
  * черновик клиника не видела вовсе — политика отдавала только `active`,
    поэтому в списке откликов были прочерки даже там, где данные есть;
  * `reveal_application_contact` при отсутствии телефона возвращала пустоту и
    при этом писала в журнал согласий «контакт открыт».

Проверяем именно обещания базы. Бот и кабинет могут быть переписаны — обойти
эти правила они не должны получить возможности.
"""
from __future__ import annotations

import json
from typing import ClassVar

import psycopg
import pytest

from app import db
from tests.conftest import admin_execute, admin_fetch_one

pytestmark = pytest.mark.asyncio

EXTRACTION = {
    "experience_months": 60,
    "salary_expectation_uzs": 5000000,
    "skills": ["Лечение корневых каналов под микроскопом", "  ", "Реставрации"],
    "languages": ["Русский — свободно", "Узбекский — свободно"],
    # Свободный текст, а не коды словаря: именно поэтому график в профиль
    # не переносится.
    "schedule": ["Сменный график", "Готов работать по субботам"],
}


async def _application_with_interview(
    world: dict, *, extraction: dict | None = None, status: str = "completed"
) -> dict:
    """Отклик и закрытое интервью поверх готового мира.

    Идём мимо `open_interview` намеренно: она требует одобренного плана, а
    здесь проверяется не публикация, а то, что происходит с профилем ПОСЛЕ
    разговора.
    """
    row = await admin_fetch_one(
        """
        WITH app AS (
            INSERT INTO product.applications (job_id, candidate_id)
            VALUES (%(job)s, %(cand)s) RETURNING id
        ), iv AS (
            INSERT INTO product.interviews
                (application_id, status, extraction, summary, finished_at)
            SELECT app.id, %(st)s::product.interview_status, %(ex)s::jsonb,
                   'Стоматолог-терапевт, пять лет опыта.',
                   CASE WHEN %(st)s = 'in_progress' THEN NULL ELSE now() END
            FROM app RETURNING id
        )
        SELECT (SELECT id FROM app) AS application_id,
               (SELECT id FROM iv)  AS interview_id
        """,
        {
            "job": world["job_a_id"],
            "cand": world["candidate_id"],
            "st": status,
            "ex": json.dumps(extraction) if extraction is not None else None,
        },
    )
    assert row is not None
    return {k: str(v) for k, v in row.items()}


async def _blank_profile(world: dict) -> None:
    """Возвращает профиль в то состояние, в котором его оставляет
    `open_interview`: черновик без полей."""
    await admin_execute(
        """
        UPDATE product.candidate_profiles
           SET status = 'draft', experience_months = NULL, salary_min_uzs = NULL,
               skills = '{}', languages = '{}', extraction = NULL
         WHERE id = %s
        """,
        (world["candidate_id"],),
    )


# ─────────────────────────── перенос в профиль ───────────────────────────

async def test_extraction_lands_in_profile(fixture_world: dict):
    """Опыт, навыки, языки и ожидания по оплате доезжают до полей профиля."""
    await _blank_profile(fixture_world)
    iv = await _application_with_interview(fixture_world, extraction=EXTRACTION)

    await db.execute("SELECT product.apply_interview_extraction(%s)", (iv["interview_id"],))

    card = await admin_fetch_one(
        "SELECT * FROM product.candidate_profiles WHERE id = %s",
        (fixture_world["candidate_id"],),
    )
    assert card is not None
    assert card["experience_months"] == 60
    assert int(card["salary_min_uzs"]) == 5000000
    # Пустая строка из массива отфильтрована: «  » в списке навыков — мусор,
    # а не навык.
    assert card["skills"] == [
        "Лечение корневых каналов под микроскопом",
        "Реставрации",
    ]
    assert len(card["languages"]) == 2
    assert card["extraction"]["schedule"] == EXTRACTION["schedule"]


async def test_interview_does_not_put_person_in_the_pool(fixture_world: dict):
    """Собеседование заполняет карточку, но не выкладывает её всем клиникам.

    Первая версия 034 переводила профиль в `active`, и тест изоляции тенантов
    поймал последствие: активный профиль по политике p_candidates_own виден
    ЛЮБОЙ клинике. Человек откликнулся в одну клинику, а попадал в каталог для
    всех. Согласия на это он не давал (миграция 036).
    """
    await _blank_profile(fixture_world)
    iv = await _application_with_interview(fixture_world, extraction=EXTRACTION)

    await db.execute("SELECT product.apply_interview_extraction(%s)", (iv["interview_id"],))

    row = await admin_fetch_one(
        "SELECT status::text, experience_months FROM product.candidate_profiles WHERE id = %s",
        (fixture_world["candidate_id"],),
    )
    assert row is not None
    assert row["status"] == "draft", "статус профиля собеседование не меняет"
    assert row["experience_months"] == 60, "но данные из интервью в карточке есть"


async def test_pool_stays_closed_for_other_clinics(fixture_world: dict):
    """Клиника, к которой человек не откликался, его карточки не видит —
    даже после пройденного собеседования у конкурента."""
    await _blank_profile(fixture_world)
    iv = await _application_with_interview(fixture_world, extraction=EXTRACTION)
    await db.execute("SELECT product.apply_interview_extraction(%s)", (iv["interview_id"],))

    async with db.scoped(
        clinic_id=fixture_world["clinic_b_id"],
        user_id=fixture_world["other_clinic_user_id"],
        member_role="owner",
    ) as conn:
        cur = await conn.execute(
            "SELECT id FROM product.candidate_profiles WHERE id = %s",
            (fixture_world["candidate_id"],),
        )
        assert await cur.fetchone() is None


async def test_schedule_and_specialty_are_not_guessed(fixture_world: dict):
    """График свободным текстом в поле кодов не попадает.

    `profile.schedule` — коды словаря, по которым будет работать матчинг.
    Фраза «Готов работать по субботам» сломала бы фильтр молча, поэтому сырой
    текст остаётся только в extraction.
    """
    await _blank_profile(fixture_world)
    await admin_execute(
        "UPDATE product.candidate_profiles SET schedule = '{}', specialty = NULL WHERE id = %s",
        (fixture_world["candidate_id"],),
    )
    iv = await _application_with_interview(fixture_world, extraction=EXTRACTION)

    await db.execute("SELECT product.apply_interview_extraction(%s)", (iv["interview_id"],))

    row = await admin_fetch_one(
        "SELECT schedule, specialty FROM product.candidate_profiles WHERE id = %s",
        (fixture_world["candidate_id"],),
    )
    assert row is not None
    assert row["schedule"] == []
    assert row["specialty"] is None


async def test_deleted_profile_is_not_resurrected(fixture_world: dict):
    """Право на забвение сильнее удобства: удалённый профиль не оживает."""
    iv = await _application_with_interview(fixture_world, extraction=EXTRACTION)
    await admin_execute(
        "UPDATE product.candidate_profiles SET status = 'deleted' WHERE id = %s",
        (fixture_world["candidate_id"],),
    )

    await db.execute("SELECT product.apply_interview_extraction(%s)", (iv["interview_id"],))

    row = await admin_fetch_one(
        "SELECT status::text FROM product.candidate_profiles WHERE id = %s",
        (fixture_world["candidate_id"],),
    )
    assert row is not None and row["status"] == "deleted"


async def test_garbage_extraction_does_not_break_closing(fixture_world: dict):
    """Модель вернула строку вместо числа — интервью всё равно закрывается.

    Приведение типов уронило бы завершение разговора, а это последнее, чем
    можно рисковать: человек уже ответил на вопросы.
    """
    await _blank_profile(fixture_world)
    iv = await _application_with_interview(
        fixture_world,
        extraction={"experience_months": "пять лет", "skills": "не массив",
                    "salary_expectation_uzs": -1},
    )

    await db.execute("SELECT product.apply_interview_extraction(%s)", (iv["interview_id"],))

    row = await admin_fetch_one(
        "SELECT experience_months, salary_min_uzs, skills FROM product.candidate_profiles "
        "WHERE id = %s",
        (fixture_world["candidate_id"],),
    )
    assert row is not None
    assert row["experience_months"] is None
    assert row["salary_min_uzs"] is None
    assert row["skills"] == []


async def test_empty_extraction_leaves_profile_alone(fixture_world: dict):
    """Модель ничего не установила — статус не двигаем."""
    await _blank_profile(fixture_world)
    iv = await _application_with_interview(fixture_world, extraction={})

    await db.execute("SELECT product.apply_interview_extraction(%s)", (iv["interview_id"],))

    row = await admin_fetch_one(
        "SELECT status::text FROM product.candidate_profiles WHERE id = %s",
        (fixture_world["candidate_id"],),
    )
    assert row is not None and row["status"] == "draft"


# ─────────────────────────── видимость карточки ───────────────────────────

async def test_clinic_sees_own_applicant_even_in_draft(fixture_world: dict):
    """Клиника видит карточку того, кто откликнулся к ней.

    Пока интервью идёт, профиль ещё черновик. Раньше в этот момент менеджер
    видел прочерки и не понимал, сломался кабинет или кандидат ничего не
    рассказал.
    """
    await _blank_profile(fixture_world)
    await _application_with_interview(fixture_world, extraction=None, status="in_progress")

    async with db.scoped(
        clinic_id=fixture_world["clinic_a_id"],
        user_id=fixture_world["clinic_user_id"],
        member_role="owner",
    ) as conn:
        cur = await conn.execute(
            "SELECT id, status::text FROM product.candidate_profiles WHERE id = %s",
            (fixture_world["candidate_id"],),
        )
        row = await cur.fetchone()
    assert row is not None, "клиника должна видеть карточку своего отклика"
    assert row["status"] == "draft"


async def test_other_clinic_does_not_see_draft_applicant(fixture_world: dict):
    """Чужой черновик не виден: видимость даёт отклик именно к этой клинике."""
    await _blank_profile(fixture_world)
    await _application_with_interview(fixture_world, extraction=None, status="in_progress")

    async with db.scoped(
        clinic_id=fixture_world["clinic_b_id"],
        user_id=fixture_world["other_clinic_user_id"],
        member_role="owner",
    ) as conn:
        cur = await conn.execute(
            "SELECT id FROM product.candidate_profiles WHERE id = %s",
            (fixture_world["candidate_id"],),
        )
        assert await cur.fetchone() is None


async def test_deleted_profile_hidden_from_own_clinic(fixture_world: dict):
    """Удалённый профиль не виден даже клинике, к которой человек откликался."""
    await _application_with_interview(fixture_world, extraction=EXTRACTION)
    await admin_execute(
        "UPDATE product.candidate_profiles SET status = 'deleted' WHERE id = %s",
        (fixture_world["candidate_id"],),
    )

    async with db.scoped(
        clinic_id=fixture_world["clinic_a_id"],
        user_id=fixture_world["clinic_user_id"],
        member_role="owner",
    ) as conn:
        cur = await conn.execute(
            "SELECT id FROM product.candidate_profiles WHERE id = %s",
            (fixture_world["candidate_id"],),
        )
        assert await cur.fetchone() is None


async def test_applications_listing_shows_candidate_name(fixture_world: dict):
    """В списке откликов есть имя. Имя — не контакт, позвонить по нему нельзя."""
    from app.services import job_store

    await _application_with_interview(fixture_world, extraction=EXTRACTION)
    await admin_execute(
        "UPDATE product.users SET full_name = 'TEST Бекжон Алиев' WHERE id = %s",
        (fixture_world["medic_user_id"],),
    )

    async with db.scoped(
        clinic_id=fixture_world["clinic_a_id"],
        user_id=fixture_world["clinic_user_id"],
        member_role="owner",
    ) as conn:
        rows = await job_store.applications(conn, fixture_world["job_a_id"])

    assert len(rows) == 1
    assert rows[0]["candidate_name"] == "TEST Бекжон Алиев"
    assert rows[0]["experience_months"] == 48
    # Телефона в списке нет и быть не должно: он открывается отдельным действием.
    assert "phone" not in rows[0]


# ─────────────────────────── своя карточка медика ───────────────────────────

async def test_my_card_never_returns_phone(fixture_world: dict):
    """Бот получает факт наличия телефона, а не сам телефон.

    У роли `ishmed_app` нет прав на `candidate_contacts`, и функция не должна
    становиться обходным путём: `p_user_id` приходит из кода, а код ошибается.
    """
    card = await db.fetch_one(
        "SELECT * FROM product.my_candidate_card(%s)", (fixture_world["medic_user_id"],)
    )
    assert card is not None
    assert card["has_contact"] is True  # в fixture_world телефон есть
    assert "phone" not in card
    assert "telegram_username" not in card


async def test_my_card_counts_applications(fixture_world: dict):
    await _application_with_interview(fixture_world, extraction=EXTRACTION)

    card = await db.fetch_one(
        "SELECT * FROM product.my_candidate_card(%s)", (fixture_world["medic_user_id"],)
    )
    assert card is not None
    assert card["applications_total"] == 1
    assert card["interviews_done"] == 1


async def test_my_card_hidden_after_forget(fixture_world: dict):
    await db.execute(
        "SELECT product.forget_candidate(%s)", (fixture_world["medic_user_id"],)
    )
    card = await db.fetch_one(
        "SELECT * FROM product.my_candidate_card(%s)", (fixture_world["medic_user_id"],)
    )
    assert card is None


# ─────────────────────────── контакт: запись и раскрытие ───────────────────────────

async def test_reveal_fails_when_candidate_left_no_phone(fixture_world: dict):
    """Нет телефона — явный отказ, и никакой записи в журнал согласий.

    Раньше функция возвращала пустоту и всё равно писала «контакт открыт».
    Журнал согласий — юридический документ: ложная запись в нём хуже
    отсутствующей.
    """
    app = await _application_with_interview(fixture_world, extraction=EXTRACTION)
    await admin_execute(
        "DELETE FROM product.candidate_contacts WHERE candidate_id = %s",
        (fixture_world["candidate_id"],),
    )
    await admin_execute(
        "UPDATE product.applications SET status = 'accepted', responded_at = now() "
        "WHERE id = %s",
        (app["application_id"],),
    )

    with pytest.raises(psycopg.Error) as err:
        await db.fetch_one(
            "SELECT * FROM product.reveal_application_contact(%s, %s)",
            (app["application_id"], fixture_world["clinic_user_id"]),
        )
    assert "не оставил контакт" in str(err.value)

    events = await admin_fetch_one(
        "SELECT count(*) AS n FROM product.consent_events "
        "WHERE application_id = %s AND event_type = 'contact_revealed'",
        (app["application_id"],),
    )
    assert events is not None and events["n"] == 0, (
        "раскрытия не было — записи в журнале быть не должно"
    )


async def test_saved_contact_becomes_revealable(fixture_world: dict):
    """Полный путь: медик отдал телефон, клиника приняла отклик, контакт открыт."""
    from app.services import users

    app = await _application_with_interview(fixture_world, extraction=EXTRACTION)
    await admin_execute(
        "DELETE FROM product.candidate_contacts WHERE candidate_id = %s",
        (fixture_world["candidate_id"],),
    )

    # Так это делает бот: сохраняем через сервис, а не прямым INSERT —
    # прямого пути у прикладной роли и нет.
    await users.save_contact(fixture_world["medic_user_id"], "998907654321", "test_medic")

    await admin_execute(
        "UPDATE product.applications SET status = 'accepted', responded_at = now() "
        "WHERE id = %s",
        (app["application_id"],),
    )
    row = await db.fetch_one(
        "SELECT * FROM product.reveal_application_contact(%s, %s)",
        (app["application_id"], fixture_world["clinic_user_id"]),
    )
    assert row is not None
    assert row["phone"] == "998907654321"
    assert row["telegram_username"] == "test_medic"


async def test_contact_still_closed_before_accept(fixture_world: dict):
    """Телефон есть, но отклик не принят — контакт закрыт."""
    app = await _application_with_interview(fixture_world, extraction=EXTRACTION)

    with pytest.raises(psycopg.Error) as err:
        await db.fetch_one(
            "SELECT * FROM product.reveal_application_contact(%s, %s)",
            (app["application_id"], fixture_world["clinic_user_id"]),
        )
    assert "контакт закрыт" in str(err.value)


async def test_save_contact_without_profile_is_rejected(fixture_world: dict):
    """Телефон некуда привязать без профиля — сервис говорит это внятно."""
    from app.services import users

    row = await admin_fetch_one(
        "INSERT INTO product.users (role, telegram_user_id) VALUES ('medic', %s) RETURNING id",
        (-900_999,),
    )
    assert row is not None
    with pytest.raises(users.NoProfile):
        await users.save_contact(int(row["id"]), "998901112233", "nobody")
    await admin_execute("DELETE FROM product.users WHERE id = %s", (row["id"],))


# ─────────────────────────── уведомление о принятии ───────────────────────────

class FakeBot:
    """Двойник aiogram.Bot: запоминает отправленное вместо похода в Telegram.

    В тестах настоящий Bot недопустим: telegram_user_id в фикстуре
    отрицательный, но ошибиться и написать живому человеку — вопрос одной
    опечатки.
    """

    sent: ClassVar[list[dict]] = []

    class _Session:
        async def close(self) -> None:
            return None

    def __init__(self, *args, **kwargs):
        self.session = FakeBot._Session()

    async def send_message(self, chat_id, text, **kwargs):
        FakeBot.sent.append({"chat_id": chat_id, "text": text, **kwargs})


async def test_accept_notifies_candidate_and_asks_for_phone(
    fixture_world: dict, monkeypatch
):
    """Клиника приняла отклик — человек узнаёт об этом, и у него просят номер.

    До этого принятие не выходило за пределы базы: менеджер нажимал «Принять»,
    а кандидат об этом никогда не узнавал. Если он не оставил телефон, отклик
    становился тупиком с двух сторон.
    """
    from app.services import notify

    app = await _application_with_interview(fixture_world, extraction=EXTRACTION)
    await admin_execute(
        "DELETE FROM product.candidate_contacts WHERE candidate_id = %s",
        (fixture_world["candidate_id"],),
    )
    FakeBot.sent = []
    monkeypatch.setattr(notify, "Bot", FakeBot)

    async with db.scoped(
        clinic_id=fixture_world["clinic_a_id"],
        user_id=fixture_world["clinic_user_id"],
        member_role="owner",
    ) as conn:
        ok = await notify.application_accepted(conn, app["application_id"])

    assert ok is True
    assert len(FakeBot.sent) == 1
    msg = FakeBot.sent[0]
    assert "TEST процедурная медсестра" in msg["text"]
    assert msg["reply_markup"] is not None, "без телефона должна быть кнопка отправки номера"


async def test_accept_does_not_ask_phone_twice(fixture_world: dict, monkeypatch):
    """Телефон уже есть — просить его снова незачем."""
    from app.services import notify

    app = await _application_with_interview(fixture_world, extraction=EXTRACTION)
    FakeBot.sent = []
    monkeypatch.setattr(notify, "Bot", FakeBot)

    async with db.scoped(
        clinic_id=fixture_world["clinic_a_id"],
        user_id=fixture_world["clinic_user_id"],
        member_role="owner",
    ) as conn:
        await notify.application_accepted(conn, app["application_id"])

    assert FakeBot.sent[0]["reply_markup"] is None


async def test_other_clinic_cannot_notify_someone_elses_candidate(
    fixture_world: dict, monkeypatch
):
    """Чужой отклик не найдётся: уведомление ходит в базу под RLS."""
    from app.services import notify

    app = await _application_with_interview(fixture_world, extraction=EXTRACTION)
    FakeBot.sent = []
    monkeypatch.setattr(notify, "Bot", FakeBot)

    async with db.scoped(
        clinic_id=fixture_world["clinic_b_id"],
        user_id=fixture_world["other_clinic_user_id"],
        member_role="owner",
    ) as conn:
        ok = await notify.application_accepted(conn, app["application_id"])

    assert ok is False
    assert FakeBot.sent == []
