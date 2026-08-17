"""Бот приёма отзывов @ishmedsifatbot.

Пациент наводит камеру на QR у кабинета, попадает сюда по deep link
t.me/ishmedsifatbot?start=<код> и оставляет отзыв: оценку кнопками, потом при
желании текст, голосовое или фото. Голосовое расшифровываем — менеджер должен
читать, а не слушать.

Почему отдельный бот, а не тот же @ishmedbot: у них разные аудитории и разные
разговоры. Медик ищет работу, пациент оценивает приём. Смешать их в одном
диалоге значит каждый раз выяснять, кто пришёл.

Запуск:  ./.venv/bin/python -m app.bot_review.main
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import sys

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app import db
from app.config import settings
from app.i18n import LOCALES, normalize, t
from app.services import review_intake as intake

log = logging.getLogger("ishmed.review-bot")
router = Router(name="review")


class Flow(StatesGroup):
    rating = State()
    details = State()


# ── Язык ──────────────────────────────────────────────────────────────────────
# Пациент приходит по QR и языка не выбирал — спрашивать его отдельным экраном
# значит поставить лишний шаг перед отзывом, который и так занимает полминуты.
# Поэтому берём язык из Telegram, а под приветствием вешаем переключатель:
# ошиблись — одно нажатие.

async def _locale(state: FSMContext, message_from) -> str:
    data = await state.get_data()
    stored = data.get("locale")
    if stored:
        return normalize(stored)
    return normalize(getattr(message_from, "language_code", None))


def _other_locale(locale: str) -> str:
    return next(code for code in LOCALES if code != locale)


def rating_kb(locale: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=str(n), callback_data=f"rate:{n}") for n in range(1, 6)],
            [InlineKeyboardButton(text=t("review_scale_hint", locale), callback_data="noop")],
            # Подпись кнопки — язык, НА который переключаемся, а не текущий.
            [InlineKeyboardButton(
                text=t("review_switch_language", locale),
                callback_data=f"lang:{_other_locale(locale)}",
            )],
        ]
    )


def details_kb(locale: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text=t("review_btn_done", locale), callback_data="finish")
        ]]
    )


# ── Вход по QR ────────────────────────────────────────────────────────────────

@router.message(CommandStart(deep_link=True))
async def start_with_token(message: Message, command: CommandObject, state: FSMContext) -> None:
    slug = (command.args or "").strip()
    locale = normalize(getattr(message.from_user, "language_code", None))
    async with db.connection() as conn:
        target = await intake.resolve_target(conn, slug)

    if target is None:
        await message.answer(t("review_not_found", locale))
        return
    if not target["is_active"]:
        await message.answer(t("review_closed", locale))
        return

    await state.set_state(Flow.rating)
    await state.update_data(slug=slug, title=target["title"], locale=locale)
    await message.answer(
        t("review_greeting", locale, title=target["title"], clinic=target["clinic_name"]),
        reply_markup=rating_kb(locale),
    )


@router.message(CommandStart(deep_link=False))
async def start_without_token(message: Message, state: FSMContext) -> None:
    """Пришёл сам, без QR. Отправлять его выбирать кабинет из списка нельзя:
    он не знает, где был, а мы получим отзывы, привязанные наугад."""
    locale = normalize(getattr(message.from_user, "language_code", None))
    await state.clear()
    await message.answer(t("review_no_qr", locale))


# ── Оценка ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "noop")
async def noop(call: CallbackQuery) -> None:
    await call.answer()


@router.callback_query(F.data.startswith("lang:"), Flow.rating)
async def on_language(call: CallbackQuery, state: FSMContext) -> None:
    """Переключение языка до выставления оценки.

    Перерисовываем то же приветствие: пациент не должен терять место в
    сценарии из-за смены языка.
    """
    assert call.data is not None
    locale = normalize(call.data.split(":", 1)[1])
    data = await state.get_data()
    await state.update_data(locale=locale)
    await call.answer()

    async with db.connection() as conn:
        target = await intake.resolve_target(conn, data.get("slug", ""))
    if target is None:
        return
    await call.message.edit_text(  # type: ignore[union-attr]
        t("review_greeting", locale, title=target["title"], clinic=target["clinic_name"]),
        reply_markup=rating_kb(locale),
    )


@router.callback_query(F.data.startswith("rate:"), Flow.rating)
async def on_rating(call: CallbackQuery, state: FSMContext) -> None:
    assert call.data is not None
    rating = int(call.data.split(":")[1])
    data = await state.get_data()
    locale = await _locale(state, call.from_user)

    try:
        async with db.connection() as conn:
            review_id = await intake.create_review(
                conn,
                slug=data["slug"],
                telegram_user_id=call.from_user.id,
                rating=rating,
                # Язык, на котором человек реально оставляет отзыв, а не тот,
                # что стоит у него в Telegram: он мог переключить его кнопкой.
                locale=locale,
            )
    except intake.AlreadySubmitted:
        await call.answer()
        await call.message.edit_text(t("review_duplicate", locale))  # type: ignore[union-attr]
        await state.clear()
        return
    except Exception:
        log.exception("не удалось создать отзыв")
        await call.answer()
        await call.message.answer(t("review_save_failed", locale))  # type: ignore[union-attr]
        return

    await state.set_state(Flow.details)
    await state.update_data(review_id=review_id, rating=rating, locale=locale)
    await call.answer()
    await call.message.edit_text(  # type: ignore[union-attr]
        t("review_ask_details", locale, rating=rating), reply_markup=details_kb(locale)
    )


# ── Подробности ───────────────────────────────────────────────────────────────

@router.message(Flow.details, F.text & ~F.text.startswith("/"))
async def on_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    locale = await _locale(state, message.from_user)
    async with db.connection() as conn:
        await intake.append_comment(conn, data["review_id"], message.text or "")
    await message.answer(
        t("review_comment_saved", locale), reply_markup=details_kb(locale)
    )


@router.message(Flow.details, F.voice)
async def on_voice(message: Message, state: FSMContext, bot: Bot) -> None:
    """Голосовое расшифровываем сразу.

    Если распознавание отвалилось, вложение всё равно сохраняем: запись важнее
    расшифровки, и менеджер сможет её послушать.
    """
    data = await state.get_data()
    locale = await _locale(state, message.from_user)
    voice = message.voice
    assert voice is not None

    await message.answer(t("review_voice_listening", locale))
    transcript: str | None = None
    try:
        transcript = await intake.transcribe_voice(bot, voice.file_id)
    except Exception:
        log.exception("не удалось расшифровать голосовое")

    async with db.connection() as conn:
        await intake.attach(
            conn,
            review_id=data["review_id"],
            kind="voice",
            file_id=voice.file_id,
            file_unique_id=voice.file_unique_id,
            mime_type=voice.mime_type,
            file_size=voice.file_size,
            duration=voice.duration,
            transcript=transcript,
        )

    if transcript:
        await message.answer(
            t("review_voice_transcribed", locale, text=transcript),
            reply_markup=details_kb(locale),
        )
    else:
        await message.answer(
            t("review_voice_saved", locale), reply_markup=details_kb(locale)
        )


@router.message(Flow.details, F.photo)
async def on_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    locale = await _locale(state, message.from_user)
    # Telegram присылает несколько размеров; берём самый крупный — мелкий
    # превью на фотографии очереди или счёта ничего не покажет.
    photo = (message.photo or [])[-1]
    async with db.connection() as conn:
        await intake.attach(
            conn,
            review_id=data["review_id"],
            kind="photo",
            file_id=photo.file_id,
            file_unique_id=photo.file_unique_id,
            file_size=photo.file_size,
        )
        if message.caption:
            await intake.append_comment(conn, data["review_id"], message.caption)
    await message.answer(t("review_photo_saved", locale), reply_markup=details_kb(locale))


@router.callback_query(F.data == "finish")
async def on_finish(call: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    locale = await _locale(state, call.from_user)
    await state.clear()
    await call.answer()
    await call.message.edit_text(  # type: ignore[union-attr]
        t("review_finished", locale,
          title=data.get("title") or t("review_finished_fallback", locale))
    )


@router.message()
async def fallback(message: Message, state: FSMContext) -> None:
    """Сообщение вне сценария. Пациент мог вернуться в бота через неделю —
    объясняем, что делать, а не молчим."""
    if await state.get_state() is None:
        await message.answer(
            t("review_no_qr", normalize(getattr(message.from_user, "language_code", None)))
        )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    s = settings()
    if not s.review_bot_token:
        log.error("REVIEW_BOT_TOKEN не задан")
        return

    health = await db.healthcheck()
    log.info("база: роль %s, PostgreSQL %s", health.get("role"), health.get("pg_version"))

    bot = Bot(
        token=s.review_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    me = await bot.get_me()
    log.info("бот отзывов @%s (id=%s) запускается в режиме polling", me.username, me.id)

    dp = Dispatcher()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        await db.close_pool()
        log.info("бот отзывов остановлен")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(main())
