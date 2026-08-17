"""Подбор: общий поиск медиков, совпадения под вакансию, приглашения.

Отдельный роутер от вакансий и откликов, потому что это другой сценарий.
Вакансия — «я жду, кто придёт». Отклик — «пришёл, разбираемся». Подбор — «иду
искать сам», и в нём клиника видит людей, которые к ней не обращались.

Всё анонимно до accept. В списке откликов имя показано — человек пришёл к этой
клинике сам. В подборе он никого не выбирал, поэтому карточка без имени и без
контакта, а телефон открывается через `product.reveal_contact` только после
того, как человек принял приглашение. Каждое открытие пишется в журнал согласий.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.services import match_store as store
from app.services import notify
from app.web.deps import Manager, ManagerConn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/matching", tags=["matching"])


class InviteIn(BaseModel):
    job_id: str
    candidate_id: str
    # Короткое личное сообщение. Не обязательное: пустое приглашение всё равно
    # понятно из карточки вакансии, а заставлять менеджера писать текст ради
    # текста — верный способ получить «Здравствуйте, приглашаем вас».
    message: str | None = Field(default=None, max_length=600)


@router.get("/jobs")
async def jobs_for_matching(conn: ManagerConn, _: Manager):
    """Вакансии, под которые можно подбирать, и справочники для фильтров."""
    return {
        "jobs": await store.matchable_jobs(conn),
        "dictionaries": await store.dictionaries(conn),
    }


@router.get("/pool")
async def pool(
    conn: ManagerConn,
    _: Manager,
    role_category: str | None = Query(default=None),
    specialty: str | None = Query(default=None),
    district: str | None = Query(default=None),
    schedule: str | None = Query(default=None),
    experience_min: int | None = Query(default=None, ge=0, le=720),
    salary_max: int | None = Query(default=None, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Ручной поиск по базе медиков.

    Матчи не пишет и балл не считает: здесь клиника ищет своими глазами, а не
    спрашивает алгоритм. Скоринг — отдельная кнопка «Подобрать» под вакансию.
    """
    rows = await store.pool(
        conn,
        role_category=role_category,
        specialty=specialty,
        district=district,
        schedule=schedule,
        experience_min=experience_min,
        salary_max=salary_max,
        limit=limit,
        offset=offset,
    )
    # total_count приходит оконной функцией в каждой строке — на пустом ответе
    # его негде взять, и это честный ноль.
    total = int(rows[0]["total_count"]) if rows else 0
    return {"candidates": rows, "total": total, "limit": limit, "offset": offset}


@router.post("/jobs/{job_id}/recompute")
async def recompute(job_id: str, conn: ManagerConn, _: Manager):
    """Пересчитывает подбор под вакансию.

    Отдаёт и совпадения, и сводку по отсеянным. Второе не менее важно: «пусто»
    без объяснения читается как поломка, а «отсеяно 34, из них 20 по роли»
    подсказывает, что не так с требованиями.
    """
    ranking = await store.recompute(conn, job_id)
    if ranking is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Вакансия не найдена"
        )
    return {
        "matches": await store.matches(conn, job_id),
        "excluded": ranking.excluded,
        "excluded_total": ranking.excluded_total,
        "strong": sum(1 for m in ranking.matches if m.level == "strong"),
        "wants_more_money": sum(1 for m in ranking.matches if m.wants_more_money),
    }


@router.get("/jobs/{job_id}/matches")
async def stored_matches(job_id: str, conn: ManagerConn, _: Manager):
    """Ранее посчитанный подбор. Без пересчёта: он трогает базу, а открытие
    страницы не должно её менять."""
    return {"matches": await store.matches(conn, job_id)}


@router.get("/invitations")
async def invitations(
    conn: ManagerConn, _: Manager, job_id: str | None = Query(default=None)
):
    return {"invitations": await store.invitations(conn, job_id)}


@router.post("/invitations", status_code=status.HTTP_201_CREATED)
async def invite(payload: InviteIn, conn: ManagerConn, user: Manager):
    try:
        row = await store.invite(
            conn,
            job_id=payload.job_id,
            candidate_id=payload.candidate_id,
            actor_user_id=user.user_id,
            message=payload.message,
        )
    except Exception as e:
        # Тексты сверены с тем, что действительно поднимает product.send_invitation.
        text = str(e)
        if "не опубликована или план интервью не одобрен" in text:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Сначала опубликуйте вакансию с одобренным планом интервью — "
                       "приглашение ведёт человека на собеседование",
            ) from None
        if "не выводил карточку" in text:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Человек убрал карточку из поиска, пока вы смотрели список",
            ) from None
        if "нет права приглашать" in text:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Приглашать можно только по вакансиям своей клиники",
            ) from None
        if "не найден" in text:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Вакансия или кандидат не найдены"
            ) from None
        raise

    notified = False
    if row["is_new"]:
        notified = await notify.invitation_sent(conn, str(row["invitation_id"]))
    else:
        log.info(
            "приглашение по (%s, %s) уже существует в статусе %s",
            payload.job_id, payload.candidate_id, row["invitation_status"],
        )
    return {**row, "candidate_notified": notified}


@router.post("/invitations/{invitation_id}/contact")
async def reveal_contact(invitation_id: str, conn: ManagerConn, user: Manager):
    """Контакт приглашённого. Только после того, как человек принял приглашение."""
    try:
        row = await store.reveal_invited_contact(conn, invitation_id, user.user_id)
    except Exception as e:
        text = str(e)
        if "контакт закрыт" in text:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Человек ещё не принял приглашение — до этого контакт закрыт",
            ) from None
        if "не оставил контакт" in text:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Человек принял приглашение, но телефон не оставил. Бот попросил "
                       "у него номер — он появится здесь, когда человек его отправит",
            ) from None
        if "не участник" in text:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Это приглашение отправляла другая клиника",
            ) from None
        raise
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Контакт недоступен"
        )
    log.info("контакт по приглашению %s открыт менеджером %s", invitation_id, user.user_id)
    return row
