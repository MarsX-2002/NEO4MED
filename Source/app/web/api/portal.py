"""Портал сотрудника: свои курсы и отзывы пациентов о себе.

Это единственная часть кабинета, доступная роли `employee`. Всё остальное для
неё закрыто дважды: политикой RLS (`product.is_manager()`) и зависимостью
`Manager` в роутах. Здесь наоборот — менеджерских данных нет вообще:

  * `employee_id` берётся из `product.my_employee_card()`, а не из запроса.
    Клиент не может назвать чужой идентификатор, потому что его вообще не
    передаёт;
  * отзывы приходят из `product.my_reviews()` — без телефона пациента и без
    вложений: обратный звонок и фотографии адресованы руководству;
  * попытка теста начинается и проверяется функциями БД, и обе отказывают на
    чужой попытке (миграция 037).

Отказы функций разбираем по ERRCODE, а не по тексту: сообщение может меняться,
код — часть контракта.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from psycopg import errors as pg_errors
from pydantic import BaseModel, Field

from app.services import courses as svc
from app.services import reviews as reviews_svc
from app.web.deps import CurrentUser, TenantConn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/portal", tags=["portal"])

NO_CARD = "Ваша учётная запись не привязана к сотруднику клиники"


class AnswersIn(BaseModel):
    # {"<question_id>": "<option_id>"}. Пропущенный вопрос — просто отсутствующий
    # ключ: он считается неверным, и это честнее, чем не давать закончить тест.
    answers: dict[str, str] = Field(default_factory=dict, max_length=200)


async def _card(conn) -> dict:
    card = await svc.my_card(conn)
    if card is None:
        # Так выглядит менеджер, зашедший в портал: он не сотрудник в штате.
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_CARD)
    return card


@router.get("/me")
async def me(conn: TenantConn, _: CurrentUser):
    """Своя карточка: имя, подразделение, клиника. Больше о штате — ничего."""
    return await _card(conn)


@router.get("/courses")
async def my_courses(conn: TenantConn, _: CurrentUser):
    card = await _card(conn)
    return {"employee": card, "courses": await svc.my_courses(conn, card["employee_id"])}


@router.get("/courses/{course_id}")
async def my_course(course_id: str, conn: TenantConn, _: CurrentUser):
    card = await _card(conn)
    course = await svc.my_course(conn, card["employee_id"], course_id)
    if course is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Этот курс вам не назначен")
    return course


@router.post("/courses/{course_id}/attempt")
async def start_attempt(course_id: str, conn: TenantConn, user: CurrentUser):
    """Начать тест. Повторный вызов возвращает ту же незавершённую попытку."""
    await _card(conn)
    try:
        attempt_id = await svc.start_attempt(conn, course_id)
    except pg_errors.InsufficientPrivilege:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Этот курс вам не назначен") from None
    except pg_errors.CheckViolation:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "В курсе пока нет вопросов — тест сдавать нечем"
        ) from None

    questions = await svc.attempt_questions(conn, attempt_id)
    log.info("тест начат: user=%s курс=%s попытка=%s", user.user_id, course_id, attempt_id)
    return {"attempt_id": attempt_id, "questions": questions}


@router.get("/attempts/{attempt_id}/questions")
async def attempt_questions(attempt_id: str, conn: TenantConn, _: CurrentUser):
    questions = await svc.attempt_questions(conn, attempt_id)
    if not questions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Попытка не найдена")
    return {"attempt_id": attempt_id, "questions": questions}


@router.post("/attempts/{attempt_id}/submit")
async def submit(attempt_id: str, payload: AnswersIn, conn: TenantConn, user: CurrentUser):
    try:
        result = await svc.grade(conn, attempt_id, payload.answers)
    except pg_errors.NoDataFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Попытка не найдена") from None
    except pg_errors.InsufficientPrivilege:
        # Одна ошибка на два случая — чужая попытка и уже завершённая. Разделять
        # их наружу незачем: в обоих сдавать нечего.
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Эта попытка уже завершена или принадлежит не вам"
        ) from None
    except pg_errors.CheckViolation:
        raise HTTPException(status.HTTP_409_CONFLICT, "В курсе нет вопросов") from None

    log.info(
        "тест сдан: user=%s попытка=%s балл=%s%%",
        user.user_id, attempt_id, (result or {}).get("score"),
    )
    return {**(result or {}), "review": await svc.attempt_review(conn, attempt_id)}


@router.get("/attempts/{attempt_id}/review")
async def attempt_review(attempt_id: str, conn: TenantConn, _: CurrentUser):
    """Разбор завершённой попытки: что было верно и почему."""
    rows = await svc.attempt_review(conn, attempt_id)
    if not rows:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Разбор недоступен: попытка не найдена или не завершена"
        )
    return {"attempt_id": attempt_id, "review": rows}


@router.get("/reviews")
async def my_reviews(conn: TenantConn, _: CurrentUser):
    """Отзывы пациентов о себе.

    Смысл раздела для сотрудника — обратная связь, а не рейтинг: поэтому
    показываем оценки и тексты, но не даём ни телефона пациента, ни кнопки
    «обработано». Разбирает обращения менеджер.
    """
    await _card(conn)
    return {
        "reviews": await reviews_svc.my_listing(conn),
        "summary": await reviews_svc.my_summary(conn),
        "tags": await reviews_svc.tag_dictionary(conn),
    }
