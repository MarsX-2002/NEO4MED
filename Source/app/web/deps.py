"""Зависимости FastAPI: сессия, контекст тенанта, разграничение по роли."""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Cookie, Depends, HTTPException, Request, status

from app import db
from app.services import auth

SESSION_COOKIE = "ishmed_session"

MANAGER_ROLES = ("owner", "recruiter")


@dataclass(frozen=True)
class Principal:
    """Кто выполняет запрос. Иммутабелен: подменить тенанта или роль по ходу
    обработки нельзя."""

    user_id: int
    clinic_id: str
    email: str
    full_name: str | None
    locale: str
    session_id: str
    member_role: str

    @property
    def is_manager(self) -> bool:
        return self.member_role in MANAGER_ROLES


async def current_principal(
    session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> Principal:
    ctx = await auth.resolve_session(session)
    if ctx is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Сессия не найдена или истекла",
        )
    return Principal(
        user_id=ctx["user_id"],
        clinic_id=ctx["clinic_id"],
        email=ctx["email"],
        full_name=ctx["full_name"],
        locale=ctx["locale"] or "ru",
        session_id=str(ctx["session_id"]),
        # Нет роли — считаем сотрудником, а не менеджером: неизвестность должна
        # уменьшать права, а не расширять.
        member_role=ctx.get("member_role") or "employee",
    )


CurrentUser = Annotated[Principal, Depends(current_principal)]


async def manager_principal(principal: CurrentUser) -> Principal:
    """Только для управленческих разделов кабинета.

    Второй рубеж после RLS, а не замена ему: политики в БД уже не отдадут
    сотруднику чужие данные, но внятный 403 лучше пустого списка — иначе
    непонятно, сломалось или не положено.
    """
    if not principal.is_manager:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Раздел доступен только сотрудникам кабинета клиники",
        )
    return principal


Manager = Annotated[Principal, Depends(manager_principal)]


async def tenant_conn(principal: CurrentUser) -> AsyncIterator[Any]:
    """Соединение с выставленным контекстом RLS.

    Каждый роут, читающий данные клиники, обязан брать соединение отсюда.
    Забыли — увидите пустоту вместо чужих данных: RLS трактует отсутствие
    контекста как «ничего не видно».
    """
    async with db.scoped(
        clinic_id=principal.clinic_id,
        user_id=principal.user_id,
        member_role=principal.member_role,
    ) as conn:
        yield conn


TenantConn = Annotated[Any, Depends(tenant_conn)]


async def manager_conn(principal: Manager) -> AsyncIterator[Any]:
    """То же соединение, но роут доступен только менеджеру."""
    async with db.scoped(
        clinic_id=principal.clinic_id,
        user_id=principal.user_id,
        member_role=principal.member_role,
    ) as conn:
        yield conn


ManagerConn = Annotated[Any, Depends(manager_conn)]


def client_ip(request: Request) -> str | None:
    """Реальный адрес за nginx. Доверяем только первому значению X-Forwarded-For,
    потому что uvicorn запущен с --forwarded-allow-ips=127.0.0.1."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip() or None
    return request.client.host if request.client else None
