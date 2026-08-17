"""Вход сотрудника клиники и серверные сессии.

Решения и причины:
  * пароль — argon2id. В базе только хэш, ограничение
    ck_credentials_hash_is_argon2 не даёт записать что-то другое;
  * в cookie уходит СЛУЧАЙНЫЙ токен, в базе лежит его sha256. Утечка дампа не
    даёт войти под чужой сессией — по хэшу токен не восстановить;
  * счётчик неудачных попыток и блокировка живут в БД, а не в памяти процесса:
    перезапуск сервиса не должен сбрасывать защиту от перебора;
  * ответ на неизвестный email и на неверный пароль одинаковый, и в обоих
    случаях выполняется проверка хэша — иначе по времени ответа можно
    перечислить, какие адреса заведены.

Про RLS: таблицы clinics и clinic_members закрыты политикой по ishmed.clinic_id,
а на момент входа контекст ещё неизвестен. Поэтому членство читается через
product.user_clinic() — единственная SECURITY DEFINER функция, отвечающая на
вопрос «чей это сотрудник». Всё остальное после входа идёт уже в контексте.
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app import db
from app.config import settings

log = logging.getLogger(__name__)

ph = PasswordHasher()

MAX_FAILED_ATTEMPTS = 8
LOCK_DURATION = timedelta(minutes=15)

# Хэш заведомо недостижимого пароля: при неизвестном email тратим столько же
# времени, сколько на реальную проверку.
_DUMMY_HASH = ph.hash(secrets.token_urlsafe(32))


class AuthError(Exception):
    """Единая ошибка входа. Наружу отдаём одно сообщение независимо от причины."""

    def __init__(
        self,
        message: str = "Неверный email или пароль",
        *,
        retry_after: int | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _burn_time(password: str) -> None:
    """Считает хэш заведомо неверного пароля.

    Нужен, чтобы неизвестный email обрабатывался столько же времени, сколько
    существующий: иначе по задержке ответа перечисляются заведённые адреса.
    Результат сравнения не важен и подавляется явно.
    """
    with contextlib.suppress(Exception):
        ph.verify(_DUMMY_HASH, password)


async def authenticate(email: str, password: str) -> dict[str, Any]:
    """Проверяет пару email/пароль и возвращает контекст сотрудника с клиникой."""
    email = email.strip().lower()

    row = await db.fetch_one(
        """
        SELECT u.id, u.public_id, u.email, u.full_name, u.locale, u.role, u.is_blocked,
               c.password_hash, c.failed_attempts, c.locked_until
        FROM product.users u
        LEFT JOIN product.user_credentials c ON c.user_id = u.id
        WHERE lower(u.email) = %s
        """,
        (email,),
    )

    if row is None or not row.get("password_hash"):
        _burn_time(password)
        log.info("вход отклонён: неизвестный email или пароль не задан")
        raise AuthError

    if row["is_blocked"]:
        _burn_time(password)
        log.warning("вход отклонён: пользователь заблокирован id=%s", row["id"])
        raise AuthError

    now = datetime.now(UTC)
    if row["locked_until"] and row["locked_until"] > now:
        wait = int((row["locked_until"] - now).total_seconds())
        log.warning("вход отклонён: блокировка ещё %s c, user_id=%s", wait, row["id"])
        raise AuthError(
            "Слишком много неудачных попыток. Попробуйте позже.", retry_after=wait
        )

    try:
        ph.verify(row["password_hash"], password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        await _register_failure(row["id"], row["failed_attempts"] or 0)
        log.info("вход отклонён: неверный пароль, user_id=%s", row["id"])
        raise AuthError from None

    # Параметры argon2 со временем усиливаются — перехэшируем при удачном входе.
    if ph.check_needs_rehash(row["password_hash"]):
        await db.execute(
            "UPDATE product.user_credentials SET password_hash = %s WHERE user_id = %s",
            (ph.hash(password), row["id"]),
        )
        log.info("пароль перехэширован под новые параметры, user_id=%s", row["id"])

    if row["failed_attempts"] or row["locked_until"]:
        await db.execute(
            """
            UPDATE product.user_credentials
               SET failed_attempts = 0, locked_until = NULL
             WHERE user_id = %s
            """,
            (row["id"],),
        )

    clinic = await db.fetch_one(
        "SELECT clinic_id::text AS clinic_id, clinic_name, access_status, member_role "
        "FROM product.user_clinic(%s)",
        (row["id"],),
    )
    if clinic is None:
        log.error("у пользователя %s нет клиники — входить некуда", row["id"])
        raise AuthError("Аккаунт не привязан к клинике")
    if clinic["access_status"] != "active":
        log.warning("вход отклонён: клиника %s в статусе %s",
                    clinic["clinic_id"], clinic["access_status"])
        raise AuthError("Доступ клиники приостановлен")

    return {
        "user_id": row["id"],
        "public_id": str(row["public_id"]),
        "email": row["email"],
        "full_name": row["full_name"],
        "locale": row["locale"] or "ru",
        "clinic_id": clinic["clinic_id"],
        "clinic_name": clinic["clinic_name"],
        "member_role": clinic["member_role"],
    }


async def set_user_locale(user_id: int, locale: str) -> None:
    """Язык интерфейса сотрудника клиники.

    `product.users` без RLS сознательно (её читают до того, как известен
    тенант), поэтому обновляем напрямую и строго по id из сессии — чужую
    строку так не тронуть.
    """
    await db.execute(
        "UPDATE product.users SET locale = %s WHERE id = %s", (locale, user_id)
    )


async def _register_failure(user_id: int, current: int) -> None:
    attempts = current + 1
    if attempts >= MAX_FAILED_ATTEMPTS:
        await db.execute(
            """
            UPDATE product.user_credentials
               SET failed_attempts = %s, locked_until = now() + %s
             WHERE user_id = %s
            """,
            (attempts, LOCK_DURATION, user_id),
        )
        log.warning("аккаунт %s заблокирован после %s попыток", user_id, attempts)
    else:
        await db.execute(
            "UPDATE product.user_credentials SET failed_attempts = %s WHERE user_id = %s",
            (attempts, user_id),
        )


# ── Сессии ────────────────────────────────────────────────────────────────────

async def create_session(
    user_id: int, clinic_id: str, *, ip: str | None = None, user_agent: str | None = None
) -> str:
    """Создаёт сессию и возвращает ТОКЕН (не хэш) для установки в cookie."""
    token = secrets.token_urlsafe(32)
    await db.execute(
        """
        INSERT INTO product.sessions (token_hash, user_id, clinic_id, expires_at, ip, user_agent)
        VALUES (%s, %s, %s, now() + %s, %s, %s)
        """,
        (
            _hash_token(token),
            user_id,
            clinic_id,
            timedelta(hours=settings().session_ttl_hours),
            ip,
            (user_agent or "")[:500] or None,
        ),
    )
    return token


async def resolve_session(token: str | None) -> dict[str, Any] | None:
    """Контекст по токену из cookie либо None.

    Читает только таблицы без RLS (sessions, users). Название клиники здесь
    сознательно не берётся: clinics закрыта политикой, и её данные читаются
    уже после того, как контекст выставлен.
    """
    if not token:
        return None
    # member_role приходит из product.user_clinic: сама таблица clinic_members
    # закрыта политикой для роли employee, и прочитать её обычным SELECT нельзя.
    return await db.fetch_one(
        """
        SELECT s.id AS session_id, s.user_id, s.clinic_id::text AS clinic_id,
               s.expires_at, u.email, u.full_name, u.locale, u.role,
               uc.member_role::text AS member_role
        FROM product.sessions s
        JOIN product.users u ON u.id = s.user_id
        LEFT JOIN LATERAL product.user_clinic(s.user_id) uc ON true
        WHERE s.token_hash = %s
          AND s.revoked_at IS NULL
          AND s.expires_at > now()
          AND u.is_blocked = false
        """,
        (_hash_token(token),),
    )


async def revoke_session(token: str | None) -> None:
    if not token:
        return
    await db.execute(
        "UPDATE product.sessions SET revoked_at = now() "
        "WHERE token_hash = %s AND revoked_at IS NULL",
        (_hash_token(token),),
    )


async def purge_expired_sessions() -> int:
    """Уборка просроченных. По расписанию, не на каждый запрос."""
    row = await db.fetch_one(
        """
        WITH gone AS (
            DELETE FROM product.sessions
             WHERE expires_at < now() - interval '7 days'
            RETURNING 1
        ) SELECT count(*) AS n FROM gone
        """
    )
    return (row or {}).get("n", 0)
