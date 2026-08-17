"""Точка входа бота @ishmedbot.

Режим — long polling, не webhook. Причина не в простоте: зона ishmed.ezgupro.uz
до сих пор рассинхронизирована между авторитативными NS, и половина резолверов
её не видит. Завязывать доставку апдейтов на резолвинг домена нельзя.
Polling работает одинаково с ноутбука и с сервера, входящий порт не нужен.

Запуск:  ./.venv/bin/python -m app.bot.main
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app import db
from app.bot.handlers import interview as interview_handlers
from app.bot.handlers import profile as profile_handlers
from app.bot.handlers import profile_form as profile_form_handlers
from app.bot.handlers import start as start_handlers
from app.config import settings

log = logging.getLogger("ishmed.bot")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    # aiogram на INFO шумит опросом апдейтов
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


async def main() -> None:
    setup_logging()
    s = settings()

    health = await db.healthcheck()
    log.info(
        "база: %s, роль %s, PostgreSQL %s, пользователей %s",
        health.get("server_addr"),
        health.get("role"),
        health.get("pg_version"),
        health.get("users"),
    )

    bot = Bot(
        token=s.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    me = await bot.get_me()
    log.info("бот @%s (id=%s) запускается в режиме polling", me.username, me.id)

    dp = Dispatcher()
    # Порядок важен, и каждый шаг здесь оплачен отладкой.
    #
    # profile первым: reply-клавиатура присылает подписи кнопок обычным текстом,
    # а обработчик ответов интервью ловит любой текст. Пока этот роутер стоял
    # после, «Помощь», нажатая во время собеседования, записывалась как ответ
    # кандидата и уезжала клинике в транскрипт.
    #
    # interview раньше start, потому что его CommandStart сужен до полезной
    # нагрузки job_*: голый CommandStart() в start перехватил бы deep link на
    # вакансию, и человек попадал бы в общее приветствие вместо вакансии.
    #
    # start последним: там же живут обработчики «всего остального». Текст и
    # голос вне интервью доходят до них, потому что обработчики интервью
    # поднимают SkipHandler, а не молча возвращаются.
    #
    # profile_form сразу за profile: он весь на callback-кнопках с префиксом
    # pf:, с текстом не конфликтует, а рядом с профилем ему место по смыслу.
    dp.include_router(profile_handlers.router)
    dp.include_router(profile_form_handlers.router)
    dp.include_router(interview_handlers.router)
    dp.include_router(start_handlers.router)

    # Сбрасываем возможный webhook и накопленные апдейты: иначе после
    # переключения режимов бот молча не получает сообщения.
    await bot.delete_webhook(drop_pending_updates=True)

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await db.close_pool()
        log.info("бот остановлен")


if __name__ == "__main__":
    # Ctrl+C и systemd stop — это нормальное завершение, а не ошибка.
    with contextlib.suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(main())
