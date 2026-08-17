"""Пул подключений к Postgres.

База удалённая: локально ходим через SSH-туннель на 127.0.0.1:15432, на сервере
напрямую в localhost. Для приложения разницы нет, отличается только DSN в .env.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import settings

log = logging.getLogger(__name__)

_pool: AsyncConnectionPool | None = None


async def open_pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        s = settings()
        _pool = AsyncConnectionPool(
            conninfo=s.dsn,
            min_size=s.db_pool_min,
            max_size=s.db_pool_max,
            kwargs={"row_factory": dict_row, "application_name": "ishmed"},
            open=False,
        )
        await _pool.open(wait=True, timeout=15)
        log.info("пул к базе открыт")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("пул к базе закрыт")


@asynccontextmanager
async def connection() -> AsyncIterator[Any]:
    pool = await open_pool()
    async with pool.connection() as conn:
        yield conn


@asynccontextmanager
async def scoped(
    *,
    clinic_id: str | None = None,
    user_id: int | None = None,
    member_role: str | None = None,
) -> AsyncIterator[Any]:
    """Соединение с контекстом запроса для RLS.

    Политики в схеме product читают ishmed.clinic_id, ishmed.user_id и
    ishmed.member_role. Контекст ставится через set_config(..., is_local => true),
    то есть живёт до конца транзакции и не может протечь на следующий запрос из
    того же соединения пула — это принципиально, иначе одна клиника увидела бы
    данные другой.

    Роль обязательна для управленческих таблиц: они закрыты политикой
    product.is_manager(). Не передали роль — сотрудник и менеджер увидят
    одинаково мало, а не одинаково много.

    Не выставленный контекст означает «ничего не видно», а не «видно всё».
    """
    pool = await open_pool()
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            "SELECT set_config('ishmed.clinic_id',   %s, true),"
            "       set_config('ishmed.user_id',     %s, true),"
            "       set_config('ishmed.member_role', %s, true)",
            (
                clinic_id or "",
                str(user_id) if user_id is not None else "",
                member_role or "",
            ),
        )
        yield conn


async def fetch_one(sql: str, params: tuple | dict | None = None) -> dict | None:
    async with connection() as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchone()


async def fetch_all(sql: str, params: tuple | dict | None = None) -> list[dict]:
    async with connection() as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall()


async def execute(sql: str, params: tuple | dict | None = None) -> None:
    async with connection() as conn:
        await conn.execute(sql, params)


async def healthcheck() -> dict:
    """Что показать в логе при старте и по /health."""
    row = await fetch_one(
        """
        SELECT current_user                                   AS role,
               current_setting('server_version')              AS pg_version,
               inet_server_addr()::text                       AS server_addr,
               (SELECT count(*) FROM product.users)           AS users
        """
    )
    return row or {}
