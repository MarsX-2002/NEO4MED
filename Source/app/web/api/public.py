"""Публичная страница отзыва: то, что открывает пациент по QR.

Отдельная серверная страница, а не часть React-кабинета. Причины прикладные:
пациент открывает её на мобильном интернете в коридоре клиники, и грузить туда
280 КБ приложения ради одной формы бессмысленно. Здесь отдаётся один HTML со
встроенным CSS — десяток килобайт, работает и на старом телефоне.

Ни авторизации, ни cookie, ни идентификаторов клиники наружу.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import db
from app.config import settings
from app.services import reviews as svc
from app.web.deps import client_ip

log = logging.getLogger(__name__)
router = APIRouter(tags=["public"])

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

MAX_COMMENT = 2000


@router.get("/r/{slug}", response_class=HTMLResponse)
async def review_page(slug: str, request: Request):
    # Обычное соединение без контекста тенанта: пациент не входил в систему.
    # Данные отдаёт SECURITY DEFINER функция, RLS при этом остаётся в силе.
    async with db.connection() as conn:
        target = await svc.public_target(conn, slug)
        cur = await conn.execute(
            "SELECT code, name_ru, name_uz FROM product.review_tags ORDER BY sort"
        )
        tags = await cur.fetchall()

    if target is None:
        # Не «404 Not Found» техническим языком: страницу открывает пациент,
        # а не разработчик.
        return templates.TemplateResponse(
            request, "review_closed.html",
            {"reason": "Опрос не найден. Возможно, код устарел."},
            status_code=status.HTTP_404_NOT_FOUND,
        )
    if not target["is_active"]:
        return templates.TemplateResponse(
            request, "review_closed.html",
            {"reason": "Этот опрос закрыт. Спасибо за интерес!"},
            status_code=status.HTTP_410_GONE,
        )

    return templates.TemplateResponse(
        request,
        "review_form.html",
        {
            "slug": slug,
            "target": target,
            "tags": tags,
            "max_comment": MAX_COMMENT,
        },
    )


@router.post("/r/{slug}", response_class=HTMLResponse)
async def submit_review(
    slug: str,
    request: Request,
    rating: int = Form(...),
    good: list[str] = Form(default=[]),
    bad: list[str] = Form(default=[]),
    comment: str = Form(default=""),
    phone: str = Form(default=""),
    callback: str = Form(default=""),
    # Honeypot: настоящий человек это поле не увидит и не заполнит.
    # Простейшие боты заполняют всё, что находят.
    website: str = Form(default=""),
):
    if website:
        log.info("отзыв отброшен: заполнена ловушка, slug=%s", slug)
        return templates.TemplateResponse(request, "review_done.html", {"target": None})

    if not 1 <= rating <= 5:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Оценка должна быть от 1 до 5")

    ip_hash = svc.hash_ip(client_ip(request), settings().web_secret_key.get_secret_value())
    locale = "uz" if request.headers.get("accept-language", "").startswith("uz") else "ru"

    async with db.connection() as conn:
        target = await svc.public_target(conn, slug)
        if target is None or not target["is_active"]:
            return templates.TemplateResponse(
                request, "review_closed.html",
                {"reason": "Опрос недоступен."},
                status_code=status.HTTP_410_GONE,
            )
        try:
            await svc.submit(
                conn,
                slug=slug,
                rating=rating,
                good_tags=[t for t in good if t],
                bad_tags=[t for t in bad if t],
                comment=comment[:MAX_COMMENT] or None,
                contact_phone=phone.strip() or None,
                wants_callback=bool(callback) and bool(phone.strip()),
                locale=locale,
                ip_hash=ip_hash,
            )
        except Exception as e:
            text = str(e)
            if "уже принят" in text:
                return templates.TemplateResponse(
                    request, "review_done.html",
                    {"target": target, "already": True},
                )
            log.exception("не удалось принять отзыв, slug=%s", slug)
            return templates.TemplateResponse(
                request, "review_closed.html",
                {"reason": "Не получилось сохранить отзыв. Попробуйте позже."},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    log.info("принят отзыв: slug=%s оценка=%s", slug, rating)
    return templates.TemplateResponse(request, "review_done.html", {"target": target})
