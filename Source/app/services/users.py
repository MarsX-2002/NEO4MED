"""Работа с product.users.

Единственное место, где создаётся и обновляется identity. Хендлеры бота
и веб-роуты в SQL по пользователям не лезут.
"""
from __future__ import annotations

from typing import Any

from app import db
from app.config import settings
from app.i18n import DEFAULT_LOCALE


async def id_and_locale(telegram_user_id: int) -> tuple[int | None, str]:
    """product.users.id и язык человека одним запросом.

    Функции базы принимают ВНУТРЕННИЙ идентификатор, а не telegram_user_id.
    Оба bigint, поэтому перепутать их легко и никакой ошибки типов не будет —
    только пустой результат или сломанный внешний ключ.
    """
    user = await get_by_telegram_id(telegram_user_id)
    if user is None:
        return None, DEFAULT_LOCALE
    return int(user["id"]), user.get("locale") or DEFAULT_LOCALE


async def get_by_telegram_id(telegram_user_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        """
        SELECT id, public_id, role, telegram_user_id, email, locale,
               full_name, consent_at, consent_version, is_blocked
        FROM product.users
        WHERE telegram_user_id = %s
        """,
        (telegram_user_id,),
    )


async def ensure_medic(telegram_user_id: int, full_name: str | None = None) -> dict[str, Any]:
    """Регистрирует медика при первом контакте.

    Согласие здесь НЕ выставляется: сначала запись identity, потом отдельным
    действием согласие. Иначе получится, что мы завели человека в базу и
    сами же за него согласились.
    """
    row = await db.fetch_one(
        """
        INSERT INTO product.users (role, telegram_user_id, full_name)
        VALUES ('medic', %s, %s)
        ON CONFLICT (telegram_user_id) DO UPDATE
            SET full_name = COALESCE(EXCLUDED.full_name, product.users.full_name)
        RETURNING id, public_id, role, telegram_user_id, email, locale,
                  full_name, consent_at, consent_version, is_blocked
        """,
        (telegram_user_id, full_name),
    )
    assert row is not None
    return row


async def set_locale(telegram_user_id: int, locale: str) -> None:
    await db.execute(
        "UPDATE product.users SET locale = %s WHERE telegram_user_id = %s",
        (locale, telegram_user_id),
    )


async def record_consent(telegram_user_id: int) -> None:
    """Фиксирует согласие. Повторное нажатие не сдвигает исходное время."""
    await db.execute(
        """
        UPDATE product.users
           SET consent_at = COALESCE(consent_at, now()),
               consent_version = COALESCE(consent_version, %s)
         WHERE telegram_user_id = %s
        """,
        (settings().consent_version, telegram_user_id),
    )


def has_consent(user: dict[str, Any] | None) -> bool:
    return bool(user and user.get("consent_at"))


# ── Контакт медика ────────────────────────────────────────────────────────────
# Телефон лежит в product.candidate_contacts, на которую у прикладной роли нет
# ни одного права. Отсюда только запись через SECURITY DEFINER функцию: прочитать
# то, что записали, этим путём нельзя — и это не побочный эффект, а смысл.


class NoProfile(Exception):
    """Телефон некуда привязать: профиля кандидата ещё нет."""


async def save_contact(user_id: int, phone: str | None, username: str | None) -> None:
    """Сохраняет телефон и username медика.

    `user_id` — внутренний product.users.id, не telegram_user_id.
    """
    try:
        await db.execute(
            "SELECT product.save_contact(%s, %s, %s)", (user_id, phone, username)
        )
    except Exception as e:
        # Единственная ожидаемая причина — профиля нет. Функция поднимает
        # no_data_found, и различать это по тексту надёжнее, чем по классу:
        # psycopg отдаёт общий DatabaseError для plpgsql RAISE.
        if "профиль кандидата не найден" in str(e):
            raise NoProfile(str(e)) from None
        raise


async def candidate_card(user_id: int) -> dict[str, Any] | None:
    """Своя карточка медика. Телефона в ней нет — только факт `has_contact`."""
    return await db.fetch_one(
        "SELECT * FROM product.my_candidate_card(%s)", (user_id,)
    )


async def forget(user_id: int) -> None:
    """Удаление профиля по требованию человека. Обещано в тексте согласия."""
    await db.execute("SELECT product.forget_candidate(%s)", (user_id,))
