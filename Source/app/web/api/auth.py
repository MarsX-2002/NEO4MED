"""Роуты входа и текущего пользователя."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from app import db
from app.config import settings
from app.services import auth as auth_service
from app.web.deps import SESSION_COOKIE, CurrentUser, client_ip

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class MeOut(BaseModel):
    email: str
    full_name: str | None
    locale: str
    clinic_id: str
    clinic_name: str | None = None
    # Роль в клинике: owner, recruiter или employee. Интерфейс по ней решает,
    # что показывать — кабинет или портал обучения. Это не защита: разделы уже
    # закрыты политиками RLS и зависимостью Manager. Но сотруднику незачем
    # видеть меню, где каждый пункт отвечает 403.
    member_role: str = "employee"


def _set_session_cookie(response: Response, token: str) -> None:
    s = settings()
    # secure включаем, только если сайт реально на https: иначе cookie не
    # доедет при локальной разработке по http и вход будет «молча не работать».
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=s.session_ttl_hours * 3600,
        httponly=True,
        secure=s.web_base_url.startswith("https://"),
        samesite="lax",
        path="/",
    )


@router.post("/login", response_model=MeOut)
async def login(payload: LoginIn, request: Request, response: Response) -> MeOut:
    try:
        ctx = await auth_service.authenticate(payload.email, payload.password)
    except auth_service.AuthError as e:
        headers = {"Retry-After": str(e.retry_after)} if e.retry_after else None
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=e.message, headers=headers
        ) from None

    token = await auth_service.create_session(
        ctx["user_id"],
        ctx["clinic_id"],
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _set_session_cookie(response, token)
    log.info("вход выполнен: user_id=%s clinic=%s", ctx["user_id"], ctx["clinic_id"])

    return MeOut(
        email=ctx["email"],
        full_name=ctx["full_name"],
        locale=ctx["locale"],
        clinic_id=ctx["clinic_id"],
        clinic_name=ctx["clinic_name"],
        # Нет роли — считаем сотрудником, как и в Principal: неизвестность
        # должна уменьшать права, а не расширять.
        member_role=ctx.get("member_role") or "employee",
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response) -> Response:
    await auth_service.revoke_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class LocaleIn(BaseModel):
    # Ровно те же два языка, что в enum product.locale: чужое значение база
    # не примет, и лучше отказать понятной ошибкой валидации.
    locale: str = Field(pattern="^(ru|uz)$")


@router.post("/locale", status_code=status.HTTP_204_NO_CONTENT)
async def set_locale(payload: LocaleIn, principal: CurrentUser) -> Response:
    """Язык интерфейса кабинета.

    Кладём в `product.users.locale` — туда же, где язык медика в боте: язык
    принадлежит человеку, а не поверхности. Интерфейс переключается сразу и не
    ждёт этого запроса, поэтому тело ответа не нужно.
    """
    await auth_service.set_user_locale(principal.user_id, payload.locale)
    log.info("язык кабинета: user_id=%s → %s", principal.user_id, payload.locale)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=MeOut)
async def me(principal: CurrentUser) -> MeOut:
    """Кто я. Название клиники читается внутри контекста тенанта."""
    async with db.scoped(
        clinic_id=principal.clinic_id,
        user_id=principal.user_id,
        member_role=principal.member_role,
    ) as conn:
        cur = await conn.execute(
            "SELECT name FROM product.clinics WHERE id = %s", (principal.clinic_id,)
        )
        clinic = await cur.fetchone()

    return MeOut(
        email=principal.email,
        full_name=principal.full_name,
        locale=principal.locale,
        clinic_id=principal.clinic_id,
        clinic_name=(clinic or {}).get("name"),
        member_role=principal.member_role,
    )
