"""Отзывы пациентов в кабинете и генерация QR-картинки."""
from __future__ import annotations

import io
import logging

import segno
from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel

from app.config import settings
from app.services import review_intake
from app.services import reviews as svc
from app.web.deps import Manager, ManagerConn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reviews", tags=["reviews"])


class ActiveIn(BaseModel):
    is_active: bool


def review_url(slug: str) -> str:
    """Адрес в QR — deep link в бота приёма отзывов.

    Пациент навёл камеру, попал в Telegram и сразу может приложить фото,
    голосовое или написать текстом. Веб-форма осталась на /r/{slug} как резерв
    для тех, у кого нет Telegram, но на печать идёт именно бот: там больше
    возможностей и не нужно набирать текст в мобильном браузере.

    Код короткий (12 символов) ещё и поэтому: deep link ограничен 64 символами
    параметра start, а чем меньше данных в QR, тем крупнее модули и надёжнее
    считывание с расстояния.
    """
    return f"https://t.me/{settings().review_bot_username}?start={slug}"


def web_review_url(slug: str) -> str:
    """Резервная веб-форма для пациентов без Telegram."""
    return f"{settings().web_base_url}/r/{slug}"


@router.get("")
async def listing(
    conn: ManagerConn,
    _: Manager,
    target_id: str | None = Query(default=None),
    max_rating: int | None = Query(default=None, ge=1, le=5),
    unhandled: bool = Query(default=False),
    callback: bool = Query(default=False),
):
    return {
        "reviews": await svc.listing(
            conn,
            target_id=target_id,
            max_rating=max_rating,
            only_unhandled=unhandled,
            only_callback=callback,
        ),
        "summary": await svc.summary(conn),
        "tags": await svc.tag_stats(conn),
    }


@router.get("/targets")
async def targets(conn: ManagerConn, _: Manager):
    rows = await svc.targets(conn)
    for r in rows:
        r["url"] = review_url(r["slug"])
        r["web_url"] = web_review_url(r["slug"])
    return rows


@router.patch("/targets/{target_id}")
async def set_active(target_id: str, payload: ActiveIn, conn: ManagerConn, _: Manager):
    updated = await svc.set_target_active(conn, target_id, payload.is_active)
    if updated is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Опрос не найден")
    return updated


@router.post("/{review_id}/handled")
async def mark_handled(review_id: str, conn: ManagerConn, user: Manager):
    result = await svc.mark_handled(conn, review_id, user.user_id)
    if result is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Отзыв не найден или уже отмечен"
        )
    return result


# ── Картинка QR ───────────────────────────────────────────────────────────────
# Генерируется по запросу и нигде не хранится: файл на диске пришлось бы
# инвалидировать и убирать за собой, а рисование занимает миллисекунды.

@router.get("/qr/{slug}.svg")
async def qr_svg(slug: str, conn: ManagerConn, _: Manager, scale: int = Query(default=8, ge=2, le=40)):
    await _assert_own_slug(conn, slug)
    qr = segno.make(review_url(slug), error="m")
    # segno пишет SVG байтами, а не строкой, поэтому буфер именно BytesIO.
    buf = io.BytesIO()
    # Для печати нужен вектор: SVG масштабируется без потери качества,
    # а поле quiet zone обязательно, иначе камера не найдёт границы кода.
    qr.save(buf, kind="svg", scale=scale, border=3, dark="#17191c", light="#ffffff")
    return Response(
        content=buf.getvalue(),
        media_type="image/svg+xml",
        headers={"Content-Disposition": f'inline; filename="ishmed-qr-{slug}.svg"'},
    )


@router.get("/qr/{slug}.png")
async def qr_png(slug: str, conn: ManagerConn, _: Manager, scale: int = Query(default=10, ge=2, le=40)):
    await _assert_own_slug(conn, slug)
    qr = segno.make(review_url(slug), error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=scale, border=3, dark="#17191c", light="#ffffff")
    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="ishmed-qr-{slug}.png"'},
    )


async def _assert_own_slug(conn, slug: str) -> None:
    """Картинку можно получить только для своей цели.

    Сам QR ведёт на публичную страницу, то есть секрета в нём нет. Но выдавать
    по чужому slug картинку с чужим названием клиники — способ узнать, что
    такой опрос существует, и мы этого не даём. Соединение уже в контексте
    тенанта, поэтому чужой slug просто не найдётся.
    """
    cur = await conn.execute(
        "SELECT 1 AS ok FROM product.review_targets WHERE slug = %s", (slug,)
    )
    if await cur.fetchone() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Опрос не найден")


# ── Вложение отзыва ───────────────────────────────────────────────────────────

@router.get("/attachments/{attachment_id}")
async def attachment_file(attachment_id: str, conn: ManagerConn, _: Manager):
    """Фото или голосовое из отзыва.

    Файл живёт в Telegram, у нас только file_id — фотографии пациентов мы не
    храним и ответственности за них не берём. Поэтому кабинет не отдаёт браузеру
    ссылку на Telegram: в ней был бы токен бота. Ходим сами и проксируем.

    Проверка владельца — на RLS: соединение уже в контексте тенанта, и чужая
    строка вложения просто не найдётся.
    """
    cur = await conn.execute(
        """
        SELECT ra.file_id, ra.kind::text AS kind, ra.mime_type
          FROM product.review_attachments ra
         WHERE ra.id = %s
        """,
        (attachment_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Вложение не найдено")

    got = await review_intake.fetch_file(row["file_id"])
    if got is None:
        # Токен бота не настроен или Telegram не отдал файл. Пустой квадрат в
        # интерфейсе объяснить нечем, а 502 в консоли — можно.
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, "Telegram не отдал файл вложения"
        )
    data, mime = got
    return Response(
        content=data,
        media_type=row["mime_type"] or mime,
        # Файл в Telegram неизменяем: file_id указывает на конкретную версию,
        # поэтому кэшировать можно надолго. Приватно — это фото пациента.
        headers={"Cache-Control": "private, max-age=86400"},
    )
