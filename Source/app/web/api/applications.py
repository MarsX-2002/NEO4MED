"""Отклики кандидатов: список по клинике, решение, открытие контакта.

Отдельный роутер, а не часть вакансий: отклик живёт своей жизнью. Менеджер
заходит сюда каждый день, а вакансию правит один раз.

Контакт кандидата здесь НЕ отдаётся вместе со списком. Он открывается отдельным
действием после принятия отклика, через `product.reveal_application_contact`,
и это обращение попадает в журнал согласий — кандидат может узнать, кто увидел
его телефон.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.services import job_store as store
from app.services import notify
from app.web.deps import Manager, ManagerConn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/applications", tags=["applications"])


class StatusIn(BaseModel):
    status: str = Field(pattern="^(accepted|declined|viewed)$")


@router.get("")
async def listing(
    conn: ManagerConn,
    _: Manager,
    job_id: str | None = Query(default=None),
):
    return {"applications": await store.applications(conn, job_id)}


@router.post("/{application_id}/status")
async def set_status(
    application_id: str, payload: StatusIn, conn: ManagerConn, _: Manager
):
    row = await store.set_application_status(conn, application_id, payload.status)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Отклик не найден")
    log.info("отклик %s переведён в %s", application_id, payload.status)

    # Принятие — единственный статус, о котором человек должен узнать. Отказ в
    # Telegram не отправляем: сообщение «вам отказали» от бота без человека и
    # без причины делает больно и ничего не даёт.
    notified = False
    if payload.status == "accepted":
        notified = await notify.application_accepted(conn, application_id)
    return {**row, "candidate_notified": notified}


@router.post("/{application_id}/contact")
async def reveal_contact(application_id: str, conn: ManagerConn, user: Manager):
    """Открывает контакт кандидата. Только после принятия отклика.

    Ограничение живёт в самой функции базы: она проверяет статус отклика и
    пишет событие в журнал. Здесь мы только переводим отказ в понятный ответ.
    """
    try:
        cur = await conn.execute(
            "SELECT * FROM product.reveal_application_contact(%s, %s)",
            (application_id, user.user_id),
        )
        row = await cur.fetchone()
    except Exception as e:
        # Текст сверяем с тем, что функция действительно поднимает
        # («контакт закрыт: отклик в статусе sent»), а не с тем, как её
        # сообщение выглядело в моей голове: на этом уже попались один раз.
        text = str(e)
        if "контакт закрыт" in text:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Сначала примите отклик — только после этого открывается контакт",
            ) from None
        # Отдельный ответ, а не общее «недоступен»: менеджеру важно понять, что
        # дело не в правах и не в поломке. Раньше функция в этом случае молча
        # возвращала пустоту, и кабинет выглядел сломанным (миграция 035).
        if "не оставил контакт" in text:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Кандидат ещё не оставил телефон. Бот уже сообщил ему о принятом "
                       "отклике и попросил номер — он появится здесь, когда человек его "
                       "отправит",
            ) from None
        raise
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Контакт недоступен"
        )
    log.info("контакт по отклику %s открыт менеджером %s", application_id, user.user_id)
    return row
