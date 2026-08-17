"""Обучение в кабинете менеджера: курсы и кто их прошёл.

Только чтение. Курсы на пилоте заводятся сидом (`make seed-course`), потому что
редактор курса с вопросами и правильными ответами — отдельный экран, а ответ на
вопрос «кто ещё не прошёл инфекционную безопасность» нужен раньше него.

Прохождение сотрудником живёт в `app/web/api/portal.py`: там другая роль и
другие права, и смешивать их в одном роутере значит однажды забыть проверку.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, status

from app.services import courses as svc
from app.web.deps import Manager, ManagerConn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/courses", tags=["courses"])


@router.get("")
async def listing(conn: ManagerConn, _: Manager):
    return {
        "courses": await svc.listing(conn),
        "summary": await svc.summary(conn),
    }


@router.get("/results")
async def results(
    conn: ManagerConn,
    _: Manager,
    course_id: str | None = Query(default=None),
):
    """Прохождение курсов сотрудниками: один плоский список.

    Специально не «средний балл по клинике»: обучение нужно, чтобы найти
    человека, который не прошёл, и того, кто провалил — а не чтобы отчитаться
    красивой цифрой.
    """
    return {"assignments": await svc.assignments(conn, course_id=course_id)}


@router.get("/{course_id}")
async def detail(course_id: str, conn: ManagerConn, _: Manager):
    course = await svc.course(conn, course_id)
    if course is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Курс не найден")
    return {
        **course,
        "lessons": await svc.lessons(conn, course_id),
        # Правильные ответы отдаёт product.course_answer_key, и она сама
        # проверяет, что спрашивает менеджер: колонка is_correct прикладной
        # роли недоступна вовсе.
        "questions": await svc.questions_with_key(conn, course_id),
        "assignments": await svc.assignments(conn, course_id=course_id),
    }
