"""Вход в бота: язык, согласие, переход к сбору профиля.

Порядок жёсткий и намеренный: identity → язык → согласие → только потом
приём персональных данных. Критерий A2 требует, чтобы согласие было
зафиксировано ДО того, как мы что-то о человеке узнали.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot.handlers import interview as iv
from app.bot.handlers import profile_form as pf
from app.config import settings
from app.i18n import DEFAULT_LOCALE, t
from app.services import interview as iv_svc
from app.services import users

log = logging.getLogger(__name__)
router = Router(name="start")


async def _locale_of(telegram_user_id: int) -> str:
    user = await users.get_by_telegram_id(telegram_user_id)
    return (user or {}).get("locale") or DEFAULT_LOCALE


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    tg = message.from_user
    if tg is None:
        return

    user = await users.ensure_medic(tg.id, full_name=tg.full_name)
    log.info("start от telegram_id=%s, согласие=%s", tg.id, bool(user["consent_at"]))

    # Язык уже выбран и согласие есть — не гоняем человека по кругу.
    if user["locale"] and users.has_consent(user):
        await message.answer(
            t("medic_welcome", user["locale"]),
            reply_markup=kb.main_menu(user["locale"]),
        )
        await pf.offer_profile(message, int(user["id"]), user["locale"])
        return

    await message.answer(t("choose_language"), reply_markup=kb.language())


def _payload(data: str, prefix_parts: int) -> str | None:
    """Хвост callback_data после служебных сегментов — код вакансии или None.

    Человек мог прийти по ссылке на конкретную вакансию и застрять на согласии.
    Вакансию несём в самой кнопке, чтобы вернуть его туда, куда он шёл, а не в
    общее приветствие: состояние в памяти бота для этого держать не нужно.
    """
    parts = data.split(":")
    return parts[prefix_parts] if len(parts) > prefix_parts else None


async def _after_gate(call: CallbackQuery, locale: str, job_id: str | None) -> None:
    """Куда вести человека, когда язык и согласие получены."""
    user = await users.get_by_telegram_id(call.from_user.id)
    if job_id and user is not None:
        job = await iv_svc.job_by_id(job_id)
        if job is not None:
            await iv.show_job_card(
                call.message, job, int(user["id"]), locale  # type: ignore[arg-type]
            )
            return
    await call.message.answer(  # type: ignore[union-attr]
        t("medic_welcome", locale), reply_markup=kb.main_menu(locale)
    )
    # Человек пришёл без вакансии — значит просто ищет работу. Карточка это
    # ровно то, что ему нужно, и предложить её надо здесь, а не ждать, пока он
    # сам найдёт «Мой профиль».
    if user is not None:
        await pf.offer_profile(call.message, int(user["id"]), locale)  # type: ignore[arg-type]


@router.callback_query(F.data.startswith("lang:"))
async def on_language(call: CallbackQuery) -> None:
    assert call.data is not None
    parts = call.data.split(":")
    locale = parts[1] if len(parts) > 1 else ""
    if locale not in ("ru", "uz"):
        await call.answer()
        return
    job_id = _payload(call.data, 2)

    await users.set_locale(call.from_user.id, locale)
    await call.answer(t("language_set", locale))

    user = await users.get_by_telegram_id(call.from_user.id)
    if users.has_consent(user):
        await _after_gate(call, locale, job_id)
        return

    await call.message.edit_text(  # type: ignore[union-attr]
        t("consent_ask", locale), reply_markup=kb.consent(locale, job_id)
    )


@router.callback_query(F.data == "consent:details")
async def on_consent_details(call: CallbackQuery) -> None:
    locale = await _locale_of(call.from_user.id)
    await call.answer()
    await call.message.answer(t("consent_details_text", locale))  # type: ignore[union-attr]


@router.callback_query(F.data.startswith("consent:accept"))
async def on_consent_accept(call: CallbackQuery) -> None:
    locale = await _locale_of(call.from_user.id)
    job_id = _payload(call.data or "", 2)
    await users.record_consent(call.from_user.id)
    log.info("согласие записано для telegram_id=%s", call.from_user.id)

    await call.answer(t("consent_saved", locale))
    await call.message.edit_text(t("consent_saved", locale))  # type: ignore[union-attr]
    await _after_gate(call, locale, job_id)


@router.callback_query(F.data == "role:clinic")
async def on_role_clinic(call: CallbackQuery) -> None:
    """Клиника в боте не обслуживается — отправляем в веб-кабинет.

    Держать её здесь бессмысленно: весь клинический сценарий живёт на платформе.
    """
    locale = await _locale_of(call.from_user.id)
    await call.answer()
    await call.message.answer(  # type: ignore[union-attr]
        t("clinic_redirect", locale, url=settings().web_base_url)
    )


@router.message(Command("language"))
async def cmd_language(message: Message) -> None:
    await message.answer(t("choose_language"), reply_markup=kb.language())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    locale = await _locale_of(message.from_user.id)  # type: ignore[union-attr]
    await message.answer(t("help_text", locale))


# ── Всё остальное ─────────────────────────────────────────────────────────────
# Эти два обработчика подключаются последними и намеренно ловят всё, что не
# разобрали предыдущие. До них бот на любое неожидаемое сообщение отвечал
# тишиной, и человек не мог отличить «не понял» от «сломался».

@router.message(F.photo | F.document | F.video | F.audio | F.sticker)
async def on_file(message: Message) -> None:
    """Файл, фото или стикер.

    Резюме файлом мы не читаем. Раньше приветствие это обещало, а код молчал:
    обещание убрано из текста, а сам файл теперь получает честный ответ вместо
    тишины.
    """
    locale = await _locale_of(message.from_user.id)  # type: ignore[union-attr]
    await message.answer(t("file_not_supported", locale), reply_markup=kb.main_menu(locale))


@router.message()
async def on_anything_else(message: Message) -> None:
    locale = await _locale_of(message.from_user.id)  # type: ignore[union-attr]
    await message.answer(t("unknown_message", locale), reply_markup=kb.main_menu(locale))
