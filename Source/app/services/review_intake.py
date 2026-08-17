"""Приём отзыва из бота: создание, дополнение, вложения, расшифровка голоса.

Бот работает без контекста тенанта — пациент никуда не входил. Поэтому запись
идёт исключительно через SECURITY DEFINER функции, которые сами выводят
clinic_id из цели опроса. RLS при этом остаётся в силе для всего остального.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any, Literal

from aiogram import Bot

from app.config import settings

log = logging.getLogger(__name__)

# Дольше пациент и не запишет: если ему есть что сказать на пять минут,
# это разговор с администратором, а не отзыв.
MAX_VOICE_SECONDS = 300


class AlreadySubmitted(Exception):
    """Отзыв с этого аккаунта на эту цель уже принят в течение суток."""


async def resolve_target(conn, slug: str) -> dict[str, Any] | None:
    if not slug:
        return None
    cur = await conn.execute("SELECT * FROM product.public_review_target(%s)", (slug,))
    return await cur.fetchone()


async def create_review(
    conn, *, slug: str, telegram_user_id: int, rating: int, locale: str = "ru"
) -> str:
    try:
        cur = await conn.execute(
            """
            SELECT product.submit_review_telegram(
                %s, %s, %s::smallint, '{}', '{}', NULL, NULL, false, %s
            ) AS id
            """,
            (slug, telegram_user_id, rating, locale if locale in ("ru", "uz") else "ru"),
        )
        row = await cur.fetchone()
        return str(row["id"])
    except Exception as e:
        if "уже принят" in str(e):
            raise AlreadySubmitted from None
        raise


async def append_comment(conn, review_id: str, text: str) -> None:
    """Дописывает текст к отзыву.

    Пациент может прислать две реплики подряд — склеиваем, а не перезаписываем:
    потерять первую половину жалобы хуже, чем показать её двумя абзацами.
    """
    clean = (text or "").strip()
    if not clean:
        return
    await conn.execute(
        """
        UPDATE product.reviews
           SET comment = left(
                   CASE WHEN comment IS NULL OR comment = '' THEN %s
                        ELSE comment || E'\n' || %s END, 2000)
         WHERE id = %s
        """,
        (clean, clean, review_id),
    )


async def attach(
    conn,
    *,
    review_id: str,
    kind: Literal["photo", "voice", "document"],
    file_id: str,
    file_unique_id: str | None = None,
    mime_type: str | None = None,
    file_size: int | None = None,
    duration: int | None = None,
    transcript: str | None = None,
) -> str:
    cur = await conn.execute(
        """
        SELECT product.attach_to_review(
            %s, %s::product.attachment_kind, %s, %s, %s, %s, %s, %s
        ) AS id
        """,
        (review_id, kind, file_id, file_unique_id, mime_type, file_size, duration, transcript),
    )
    row = await cur.fetchone()
    return str(row["id"])


# ── Расшифровка голосового ────────────────────────────────────────────────────

async def fetch_file(file_id: str) -> tuple[bytes, str] | None:
    """Забирает файл вложения из Telegram по file_id.

    Нужна кабинету: фотографии пациентов мы у себя не храним — в базе лежит
    только file_id, а сам файл отдаёт Telegram. Кабинет проксирует его, чтобы
    токен бота не уезжал в браузер.

    Отдельный Bot на один запрос — не расточительство: веб-процесс ботом не
    владеет, а держать долгоживущую сессию ради редкого просмотра фото
    незачем. Возвращает (байты, mime).
    """
    s = settings()
    if s.review_bot_token is None:
        return None

    bot = Bot(token=s.review_bot_token.get_secret_value())
    try:
        tg_file = await bot.get_file(file_id)
        if tg_file.file_path is None:
            return None
        buf = await bot.download_file(tg_file.file_path)
        if buf is None:
            return None
        data = buf.read()
    except Exception as e:
        log.warning("не удалось забрать файл %s из Telegram: %s", file_id[:12], e)
        return None
    finally:
        await bot.session.close()

    path = (tg_file.file_path or "").lower()
    if path.endswith(".png"):
        mime = "image/png"
    elif path.endswith((".oga", ".ogg")):
        mime = "audio/ogg"
    else:
        mime = "image/jpeg"
    return data, mime


async def transcribe_voice(bot: Bot, file_id: str) -> str | None:
    """Скачивает голосовое из Telegram и расшифровывает через Azure.

    Telegram отдаёт ogg/opus, и Azure принимает его напрямую — проверено, так
    что конвертация через ffmpeg не нужна. Тратить на неё процесс и время
    ответа было бы зря.
    """
    tg_file = await bot.get_file(file_id)
    if tg_file.file_path is None:
        return None

    buf = await bot.download_file(tg_file.file_path)
    if buf is None:
        return None
    audio = buf.read()

    s = settings()
    url = (
        f"{s.azure_openai_endpoint}/openai/deployments/{s.stt_deployment}"
        f"/audio/transcriptions?api-version={s.azure_openai_api_version}"
    )

    boundary = "----ishmed-voice-boundary"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="file"; filename="voice.ogg"\r\n',
        b"Content-Type: audio/ogg\r\n\r\n",
        audio,
        f"\r\n--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="response_format"\r\n\r\njson\r\n',
        f"--{boundary}--\r\n".encode(),
    ])

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "api-key": s.azure_openai_api_key.get_secret_value(),
        },
    )
    # urllib блокирующий, поэтому вызов уводим в поток: иначе он застопорит
    # весь polling бота, и остальные пациенты будут ждать чужую расшифровку.
    import asyncio
    import time

    from app.services.llm import log_model_call

    def _call() -> tuple[str | None, dict]:
        with urllib.request.urlopen(req, timeout=90) as resp:
            data = json.load(resp)
        return (data.get("text") or "").strip() or None, (data.get("usage") or {})

    started = time.monotonic()
    text, usage = await asyncio.to_thread(_call)
    elapsed = time.monotonic() - started

    # Пишем в журнал даже пустую расшифровку: обращение к Azure состоялось,
    # и оно оплачено.
    await log_model_call(
        s.stt_deployment,
        "voice_transcribe",
        elapsed,
        {
            "prompt_tokens": usage.get("input_tokens") or usage.get("prompt_tokens"),
            "completion_tokens": usage.get("output_tokens") or usage.get("completion_tokens"),
        },
    )
    if text:
        log.info("голосовое расшифровано за %.1f c, %s символов", elapsed, len(text))
    return text
