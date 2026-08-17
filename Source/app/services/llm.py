"""Обращения к Azure OpenAI: чат с разбором JSON, озвучка, журнал вызовов.

Один общий модуль вместо вызовов из разных мест: у нас есть жёсткое требование
`reasoning_effort=minimal` (без него gpt-5 отвечает десять секунд вместо двух)
и журнал обращений в `ai.model_calls`. Обе вещи легко забыть, если ходить в
Azure откуда попало.

urllib вместо http-клиента библиотеки openai намеренно: зависимость тянет
ещё несколько пакетов, а нам нужны три эндпоинта из всего API.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)

# Модель иногда оборачивает JSON в ```json ... ``` вопреки инструкции.
_FENCES = ("```json", "```JSON", "```")


class LLMError(Exception):
    """Azure не ответил или ответил не тем, что мы просили."""


def _endpoint(deployment: str, path: str) -> str:
    s = settings()
    return (
        f"{s.azure_openai_endpoint}/openai/deployments/{deployment}/{path}"
        f"?api-version={s.azure_openai_api_version}"
    )


def _post(url: str, payload: dict[str, Any], *, timeout: int = 90) -> bytes:
    s = settings()
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "api-key": s.azure_openai_api_key.get_secret_value(),
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:400]
        raise LLMError(f"Azure вернул {e.code}: {detail}") from None
    except OSError as e:
        raise LLMError(f"Azure недоступен: {e}") from None


async def chat(
    messages: list[dict[str, str]],
    *,
    deployment: str | None = None,
    max_tokens: int = 1200,
    purpose: str = "chat",
) -> str:
    """Один заход в чат-модель. Возвращает текст ответа."""
    s = settings()
    target = deployment or s.dialog_deployment
    payload: dict[str, Any] = {
        "messages": messages,
        "max_completion_tokens": max_tokens,
        # Без этого gpt-5 уходит в рассуждение на десять секунд, и разговор
        # в боте начинает выглядеть зависшим.
        "reasoning_effort": s.reasoning_effort,
    }

    started = time.monotonic()
    raw = await asyncio.to_thread(_post, _endpoint(target, "chat/completions"), payload)
    elapsed = time.monotonic() - started

    try:
        data = json.loads(raw)
        choice = data["choices"][0]
        text = choice["message"]["content"] or ""
        finish = choice.get("finish_reason")
        usage = data.get("usage") or {}
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise LLMError(f"неожиданный ответ Azure: {e}") from None

    log.info(
        "%s: %s за %.1f c, токенов %s, finish=%s",
        purpose, target, elapsed, usage.get("total_tokens", "?"), finish,
    )
    await _log_call(target, purpose, elapsed, usage)

    if not text.strip():
        # Пустой ответ почти всегда означает, что упёрлись в
        # max_completion_tokens: рассуждение модели тоже расходует этот лимит.
        # Без finish_reason в сообщении такой сбой выглядит как «модель
        # ответила не JSON», и искать причину приходится наугад.
        raise LLMError(
            f"модель вернула пустой ответ, finish_reason={finish}, "
            f"израсходовано токенов {usage.get('completion_tokens', '?')} "
            f"из {max_tokens}"
        )
    return text.strip()


async def chat_json(
    messages: list[dict[str, str]],
    *,
    deployment: str | None = None,
    max_tokens: int = 1600,
    purpose: str = "extract",
) -> Any:
    """Чат с разбором JSON из ответа.

    Ответ модели не доверяем: пробуем разобрать, снимаем ограждение из
    тройных кавычек, и если всё равно не JSON — падаем понятной ошибкой,
    а не тащим мусор дальше в базу.
    """
    text = await chat(
        messages,
        deployment=deployment or settings().extract_deployment,
        max_tokens=max_tokens,
        purpose=purpose,
    )
    cleaned = text.strip()
    for fence in _FENCES:
        if cleaned.startswith(fence):
            cleaned = cleaned[len(fence):]
            break
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Иногда модель добавляет пояснение до или после объекта.
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            start, end = cleaned.find("["), cleaned.rfind("]")
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise LLMError(f"модель ответила не JSON: {cleaned[:200]}") from None


async def speak(text: str) -> bytes | None:
    """Озвучивает текст. Возвращает ogg/opus, готовый для Telegram sendVoice.

    Ошибку не поднимаем: голос — приятное дополнение, и если Azure недоступен,
    интервью обязано продолжиться текстом, а не оборваться.
    """
    clean = (text or "").strip()
    if not clean:
        return None
    s = settings()
    payload = {
        "model": s.tts_deployment,
        "input": clean[:1000],
        "voice": s.tts_voice,
        "response_format": "opus",
    }
    started = time.monotonic()
    try:
        audio = await asyncio.to_thread(
            _post, _endpoint(s.tts_deployment, "audio/speech"), payload, timeout=60
        )
    except LLMError as e:
        log.warning("озвучка не удалась, продолжаем текстом: %s", e)
        return None

    elapsed = time.monotonic() - started
    log.info("озвучено %s символов за %.1f c, %s байт", len(clean), elapsed, len(audio))
    await _log_call(s.tts_deployment, "tts", elapsed, {"characters": len(clean)})
    return audio


async def log_model_call(
    deployment: str, purpose: str, elapsed: float, usage: dict[str, Any] | None = None
) -> None:
    """Публичная обёртка для вызовов, которые идут не через chat().

    Нужна расшифровке голоса: она обращается к Azure своим multipart-запросом,
    и без записи в журнал стоимость самой дорогой операции продукта пришлось бы
    оценивать на глаз.
    """
    await _log_call(deployment, purpose, elapsed, usage or {})


async def _log_call(
    deployment: str, purpose: str, elapsed: float, usage: dict[str, Any]
) -> None:
    """Пишет обращение в ai.model_calls.

    Журнал нужен, чтобы к концу пилота знать реальную стоимость разговора,
    а не гадать. Сбой записи не должен ломать сам вызов, поэтому ошибку
    только логируем.
    """
    from app import db

    try:
        await db.execute(
            """
            INSERT INTO ai.model_calls (deployment, purpose, latency_ms,
                                        prompt_tokens, completion_tokens, http_status)
            VALUES (%s, %s, %s, %s, %s, 200)
            """,
            (
                deployment,
                purpose,
                int(elapsed * 1000),
                usage.get("prompt_tokens"),
                usage.get("completion_tokens"),
            ),
        )
    except Exception as e:  # журнал не должен ломать сам вызов модели
        log.debug("не удалось записать вызов модели: %s", e)
