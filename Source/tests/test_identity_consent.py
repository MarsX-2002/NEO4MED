"""A2: согласие фиксируется до приёма персональных данных.

Проверяем не текст кнопки, а инвариант: пока consent_at пуст, признавать
согласие нельзя; повторное согласие не переписывает исходное время;
и БД сама не даёт записать половину согласия.
"""
from __future__ import annotations

import pytest

from app import db
from app.services import users
from tests.conftest import TEST_TG_ID_BASE

pytestmark = pytest.mark.asyncio

TG = TEST_TG_ID_BASE - 1


async def test_new_medic_has_no_consent(clean_test_users):
    user = await users.ensure_medic(TG, full_name="Тест Медик")

    assert user["role"] == "medic"
    assert user["consent_at"] is None, "новый пользователь не должен считаться согласившимся"
    assert users.has_consent(user) is False


async def test_ensure_medic_is_idempotent(clean_test_users):
    first = await users.ensure_medic(TG)
    second = await users.ensure_medic(TG, full_name="Уточнённое Имя")

    assert first["id"] == second["id"], "повторный /start не должен создавать второго пользователя"
    assert second["full_name"] == "Уточнённое Имя"


async def test_consent_recorded_and_not_overwritten(clean_test_users):
    await users.ensure_medic(TG)

    await users.record_consent(TG)
    after_first = await users.get_by_telegram_id(TG)
    assert after_first is not None
    assert after_first["consent_at"] is not None
    assert after_first["consent_version"] is not None
    assert users.has_consent(after_first) is True

    # Повторное нажатие «Согласен» не должно сдвигать дату: иначе мы потеряем
    # момент, когда человек согласился впервые.
    await users.record_consent(TG)
    after_second = await users.get_by_telegram_id(TG)
    assert after_second is not None
    assert after_second["consent_at"] == after_first["consent_at"]


async def test_locale_roundtrip(clean_test_users):
    await users.ensure_medic(TG)
    for locale in ("uz", "ru"):
        await users.set_locale(TG, locale)
        user = await users.get_by_telegram_id(TG)
        assert user is not None and user["locale"] == locale


async def test_half_consent_rejected_by_database(clean_test_users):
    """Ограничение ck_users_consent_pair: время и версия согласия только вместе.

    Проверяем именно БД, а не сервис: сервис можно обойти новым кодом,
    ограничение — нет.
    """
    await users.ensure_medic(TG)
    with pytest.raises(Exception) as exc:
        await db.execute(
            "UPDATE product.users SET consent_at = now() WHERE telegram_user_id = %s", (TG,)
        )
    assert "ck_users_consent_pair" in str(exc.value)


async def test_medic_requires_telegram_id():
    """Медик без Telegram ID не должен существовать: identity P0 — это Telegram."""
    with pytest.raises(Exception) as exc:
        await db.execute(
            "INSERT INTO product.users (role, email) VALUES ('medic', 'nobody@example.com')"
        )
    assert "ck_users_medic_from_telegram" in str(exc.value)
