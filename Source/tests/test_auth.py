"""Вход клиники и сессии.

Самый чувствительный к ошибкам код в кабинете, и до сих пор он был проверен
только руками через curl. Ручная проверка не ловит регрессию: достаточно
однажды поменять условие в SQL, и «вход работает» превратится в «входит кто
угодно», а тесты промолчат.

Проверяем поведение, а не реализацию: что именно система отвечает, что
сохраняет в базе и чего не сохраняет.
"""
from __future__ import annotations

import time

import pytest

from app.services import auth
from tests.conftest import TEST_PASSWORD, admin_execute, admin_fetch_one

pytestmark = pytest.mark.asyncio


# ── Проверка пароля ───────────────────────────────────────────────────────────

async def test_login_with_correct_credentials(clinic_account):
    a = clinic_account
    ctx = await auth.authenticate(a["email"], TEST_PASSWORD)

    assert ctx["user_id"] == a["user_id"]
    assert ctx["clinic_id"] == a["clinic_id"]
    assert ctx["clinic_name"] == "TEST login clinic"
    assert ctx["member_role"] == "owner"


async def test_email_is_case_insensitive(clinic_account):
    a = clinic_account
    ctx = await auth.authenticate(a["email"].upper(), TEST_PASSWORD)
    assert ctx["user_id"] == a["user_id"]


async def test_wrong_password_rejected(clinic_account):
    with pytest.raises(auth.AuthError) as exc:
        await auth.authenticate(clinic_account["email"], "not-the-password")
    assert exc.value.message == "Неверный email или пароль"


async def test_unknown_email_gives_identical_message(clinic_account):
    """Разные сообщения для «нет такого адреса» и «неверный пароль» превращают
    форму входа в справочник заведённых email."""
    with pytest.raises(auth.AuthError) as unknown:
        await auth.authenticate("nobody-here@ishmed-tests.uz", "whatever")
    with pytest.raises(auth.AuthError) as wrong:
        await auth.authenticate(clinic_account["email"], "wrong")
    assert unknown.value.message == wrong.value.message


async def test_unknown_email_costs_comparable_time(clinic_account):
    """Проверка хэша занимает десятки миллисекунд. Если для неизвестного адреса
    её пропустить, разница во времени ответа выдаст существование аккаунта."""
    t0 = time.perf_counter()
    with pytest.raises(auth.AuthError):
        await auth.authenticate(clinic_account["email"], "wrong")
    known = time.perf_counter() - t0

    t0 = time.perf_counter()
    with pytest.raises(auth.AuthError):
        await auth.authenticate("nobody-here@ishmed-tests.uz", "wrong")
    unknown = time.perf_counter() - t0

    # Порог мягкий намеренно: измеряем на живой базе через туннель, шум велик.
    # Ловим не «одинаково», а «не мгновенно».
    assert unknown > known * 0.2, (
        f"неизвестный email обработан за {unknown:.3f}s против {known:.3f}s — "
        "проверка хэша пропущена"
    )


# ── Блокировка после перебора ─────────────────────────────────────────────────

async def test_lockout_after_repeated_failures(clinic_account):
    a = clinic_account
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        with pytest.raises(auth.AuthError):
            await auth.authenticate(a["email"], "wrong")

    with pytest.raises(auth.AuthError) as exc:
        await auth.authenticate(a["email"], "wrong")
    assert "Слишком много" in exc.value.message
    assert exc.value.retry_after and exc.value.retry_after > 0


async def test_correct_password_rejected_while_locked(clinic_account):
    """Иначе перебор просто продолжался бы до удачи."""
    a = clinic_account
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        with pytest.raises(auth.AuthError):
            await auth.authenticate(a["email"], "wrong")

    with pytest.raises(auth.AuthError) as exc:
        await auth.authenticate(a["email"], TEST_PASSWORD)
    assert "Слишком много" in exc.value.message


async def test_successful_login_resets_counter(clinic_account):
    a = clinic_account
    for _ in range(3):
        with pytest.raises(auth.AuthError):
            await auth.authenticate(a["email"], "wrong")

    before = await admin_fetch_one(
        "SELECT failed_attempts FROM product.user_credentials WHERE user_id = %s",
        (a["user_id"],),
    )
    assert before is not None and before["failed_attempts"] == 3

    await auth.authenticate(a["email"], TEST_PASSWORD)

    after = await admin_fetch_one(
        "SELECT failed_attempts, locked_until FROM product.user_credentials WHERE user_id = %s",
        (a["user_id"],),
    )
    assert after is not None
    assert after["failed_attempts"] == 0
    assert after["locked_until"] is None


# ── Блокировки на уровне аккаунта и клиники ───────────────────────────────────

async def test_blocked_user_cannot_login(clinic_account):
    a = clinic_account
    await admin_execute("UPDATE product.users SET is_blocked = true WHERE id = %s", (a["user_id"],))
    with pytest.raises(auth.AuthError):
        await auth.authenticate(a["email"], TEST_PASSWORD)


async def test_suspended_clinic_cannot_login(clinic_account):
    a = clinic_account
    await admin_execute(
        "UPDATE product.clinics SET access_status = 'suspended' WHERE id = %s",
        (a["clinic_id"],),
    )
    with pytest.raises(auth.AuthError) as exc:
        await auth.authenticate(a["email"], TEST_PASSWORD)
    assert "приостановлен" in exc.value.message


async def test_user_without_clinic_cannot_login(clinic_account):
    a = clinic_account
    await admin_execute(
        "DELETE FROM product.clinic_members WHERE user_id = %s", (a["user_id"],)
    )
    with pytest.raises(auth.AuthError) as exc:
        await auth.authenticate(a["email"], TEST_PASSWORD)
    assert "не привязан" in exc.value.message


# ── Сессии ────────────────────────────────────────────────────────────────────

async def test_session_roundtrip(clinic_account):
    a = clinic_account
    token = await auth.create_session(a["user_id"], a["clinic_id"], ip="127.0.0.1", user_agent="pytest")

    ctx = await auth.resolve_session(token)
    assert ctx is not None
    assert ctx["user_id"] == a["user_id"]
    assert ctx["clinic_id"] == a["clinic_id"]


async def test_token_is_never_stored_in_plaintext(clinic_account):
    """В базе должен лежать только sha256 токена: утечка дампа не должна
    давать возможность войти под чужой сессией."""
    a = clinic_account
    token = await auth.create_session(a["user_id"], a["clinic_id"])

    row = await admin_fetch_one(
        "SELECT token_hash FROM product.sessions WHERE user_id = %s", (a["user_id"],)
    )
    assert row is not None
    assert row["token_hash"] != token
    assert len(row["token_hash"]) == 64          # sha256 в hex
    assert token not in row["token_hash"]


async def test_revoked_session_is_dead(clinic_account):
    a = clinic_account
    token = await auth.create_session(a["user_id"], a["clinic_id"])
    assert await auth.resolve_session(token) is not None

    await auth.revoke_session(token)
    assert await auth.resolve_session(token) is None


async def test_expired_session_is_dead(clinic_account):
    a = clinic_account
    token = await auth.create_session(a["user_id"], a["clinic_id"])
    await admin_execute(
        "UPDATE product.sessions SET expires_at = now() - interval '1 minute' WHERE user_id = %s",
        (a["user_id"],),
    )
    assert await auth.resolve_session(token) is None


async def test_blocked_user_session_stops_working(clinic_account):
    """Блокировка сотрудника должна действовать сразу, а не после истечения
    его текущей сессии."""
    a = clinic_account
    token = await auth.create_session(a["user_id"], a["clinic_id"])
    await admin_execute("UPDATE product.users SET is_blocked = true WHERE id = %s", (a["user_id"],))
    assert await auth.resolve_session(token) is None


async def test_garbage_token_is_rejected():
    assert await auth.resolve_session("не-настоящий-токен") is None
    assert await auth.resolve_session("") is None
    assert await auth.resolve_session(None) is None


async def test_purge_removes_only_long_expired(clinic_account):
    a = clinic_account
    fresh = await auth.create_session(a["user_id"], a["clinic_id"])
    stale = await auth.create_session(a["user_id"], a["clinic_id"])

    # «Просроченная неделю назад» — та, что подлежит уборке.
    import hashlib

    await admin_execute(
        "UPDATE product.sessions SET expires_at = now() - interval '30 days' "
        "WHERE token_hash = %s",
        (hashlib.sha256(stale.encode()).hexdigest(),),
    )

    await auth.purge_expired_sessions()

    assert await auth.resolve_session(fresh) is not None, "живую сессию удалять нельзя"
    row = await admin_fetch_one(
        "SELECT 1 AS x FROM product.sessions WHERE token_hash = %s",
        (hashlib.sha256(stale.encode()).hexdigest(),),
    )
    assert row is None, "давно просроченная сессия должна быть удалена"
