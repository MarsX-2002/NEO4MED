"""A10: до accept сервис не возвращает контакт. A11: после invite+accept — возвращает.

Проверяем не сервисный слой, а саму базу. Смысл в том, что вызовы инструментов
выбирает LLM-агент: его можно уговорить попробовать выдать телефон. База уговорам
не поддаётся — прав на таблицу контактов у прикладной роли нет вовсе.
"""
from __future__ import annotations

import uuid

import pytest

from app import db
from tests.conftest import admin_execute, admin_fetch_one

pytestmark = pytest.mark.asyncio


# ── Права на таблицу ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "sql",
    [
        "SELECT phone FROM product.candidate_contacts",
        "SELECT count(*) FROM product.candidate_contacts",
        "INSERT INTO product.candidate_contacts (candidate_id, phone) "
        "VALUES (gen_random_uuid(), '998900000000')",
        "UPDATE product.candidate_contacts SET phone = '998900000000' WHERE false",
        "DELETE FROM product.candidate_contacts WHERE false",
    ],
)
async def test_app_role_has_no_access_to_contacts(sql: str):
    """Ни чтения, ни записи. Записи тоже нельзя: иначе запрет на SELECT
    обходился бы через UPDATE ... RETURNING."""
    with pytest.raises(Exception) as exc:
        await db.execute(sql)
    assert "denied" in str(exc.value).lower(), sql


async def test_contacts_table_grants_are_empty():
    row = await db.fetch_one(
        """
        SELECT count(*) AS grants
        FROM information_schema.role_table_grants
        WHERE table_schema = 'product' AND table_name = 'candidate_contacts'
          AND grantee = 'ishmed_app'
        """
    )
    assert row is not None
    assert row["grants"] == 0, "у ishmed_app не должно быть ни одного права на контакты"


# ── Поведение reveal_contact ──────────────────────────────────────────────────

async def test_contact_closed_while_invitation_is_sent(fixture_world):
    w = fixture_world
    with pytest.raises(Exception) as exc:
        await db.fetch_one(
            "SELECT * FROM product.reveal_contact(%s, %s)",
            (w["invitation_id"], w["clinic_user_id"]),
        )
    msg = str(exc.value)
    assert "контакт закрыт" in msg, msg
    assert "sent" in msg


async def test_contact_open_after_accept(fixture_world):
    w = fixture_world
    await admin_execute(
        "UPDATE product.invitations SET status='accepted', responded_at=now() WHERE id=%s",
        (w["invitation_id"],),
    )

    row = await db.fetch_one(
        "SELECT * FROM product.reveal_contact(%s, %s)",
        (w["invitation_id"], w["clinic_user_id"]),
    )
    assert row is not None
    assert row["phone"] == "998901234567"
    assert row["telegram_username"] == "demo_nurse"


async def test_medic_can_see_own_contact_after_accept(fixture_world):
    w = fixture_world
    await admin_execute(
        "UPDATE product.invitations SET status='accepted', responded_at=now() WHERE id=%s",
        (w["invitation_id"],),
    )
    row = await db.fetch_one(
        "SELECT * FROM product.reveal_contact(%s, %s)",
        (w["invitation_id"], w["medic_user_id"]),
    )
    assert row is not None and row["phone"] == "998901234567"


async def test_stranger_cannot_reveal_even_after_accept(fixture_world):
    """Посторонний сотрудник другой клиники не участник приглашения."""
    w = fixture_world
    await admin_execute(
        "UPDATE product.invitations SET status='accepted', responded_at=now() WHERE id=%s",
        (w["invitation_id"],),
    )
    with pytest.raises(Exception) as exc:
        await db.fetch_one(
            "SELECT * FROM product.reveal_contact(%s, %s)",
            (w["invitation_id"], w["other_clinic_user_id"]),
        )
    assert "не участник" in str(exc.value)


async def test_decline_does_not_reveal(fixture_world):
    """A9: отказ ничего не раскрывает."""
    w = fixture_world
    await admin_execute(
        "UPDATE product.invitations SET status='declined', responded_at=now() WHERE id=%s",
        (w["invitation_id"],),
    )
    with pytest.raises(Exception) as exc:
        await db.fetch_one(
            "SELECT * FROM product.reveal_contact(%s, %s)",
            (w["invitation_id"], w["clinic_user_id"]),
        )
    assert "контакт закрыт" in str(exc.value)


async def test_reveal_is_logged(fixture_world):
    """Раскрытие контакта обязано оставлять след в журнале согласия."""
    w = fixture_world
    await admin_execute(
        "UPDATE product.invitations SET status='accepted', responded_at=now() WHERE id=%s",
        (w["invitation_id"],),
    )
    await db.fetch_one(
        "SELECT * FROM product.reveal_contact(%s, %s)",
        (w["invitation_id"], w["clinic_user_id"]),
    )
    row = await admin_fetch_one(
        """
        SELECT event_type, actor_user_id, meta->>'as' AS side
        FROM product.consent_events
        WHERE invitation_id = %s AND event_type = 'contact_revealed'
        """,
        (w["invitation_id"],),
    )
    assert row is not None
    assert row["actor_user_id"] == w["clinic_user_id"]
    assert row["side"] == "clinic"


async def test_reveal_on_unknown_invitation():
    with pytest.raises(Exception) as exc:
        await db.fetch_one(
            "SELECT * FROM product.reveal_contact(%s, %s)", (str(uuid.uuid4()), 1)
        )
    assert "не найдено" in str(exc.value)


# ── Запись и удаление контакта ────────────────────────────────────────────────

async def test_save_contact_works_only_through_function(fixture_world):
    w = fixture_world
    await db.execute(
        "SELECT product.save_contact(%s, %s, %s)",
        (w["medic_user_id"], "998905554433", "updated_nurse"),
    )
    row = await admin_fetch_one(
        "SELECT phone, telegram_username FROM product.candidate_contacts WHERE candidate_id=%s",
        (w["candidate_id"],),
    )
    assert row is not None
    assert row["phone"] == "998905554433"
    assert row["telegram_username"] == "updated_nurse"


async def test_forget_candidate_erases_contact(fixture_world):
    """Обещание из текста согласия: профиль можно удалить в любой момент."""
    w = fixture_world
    await db.execute("SELECT product.forget_candidate(%s)", (w["medic_user_id"],))

    contact = await admin_fetch_one(
        "SELECT 1 AS x FROM product.candidate_contacts WHERE candidate_id=%s",
        (w["candidate_id"],),
    )
    assert contact is None, "контакт должен быть стёрт"

    profile = await admin_fetch_one(
        "SELECT status, transcript, skills FROM product.candidate_profiles WHERE id=%s",
        (w["candidate_id"],),
    )
    assert profile is not None
    assert profile["status"] == "deleted"
    assert profile["transcript"] is None
    assert profile["skills"] == []

    inv = await admin_fetch_one(
        "SELECT status FROM product.invitations WHERE id=%s", (w["invitation_id"],)
    )
    assert inv is not None and inv["status"] == "withdrawn"
