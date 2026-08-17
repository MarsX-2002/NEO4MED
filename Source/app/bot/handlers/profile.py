"""Карточка медика, телефон, приглашения и удаление профиля.

Отдельный роутер, и подключается он ПЕРВЫМ. Причина прикладная: reply-клавиатура
присылает подписи кнопок обычным текстом, а обработчик ответов интервью ловит
любой текст. Пока этого роутера не было, «Мой профиль» и «Приглашения» уходили
в тишину вне интервью, а внутри интервью записывались как ответ на вопрос —
клиника читала в транскрипте «Помощь».

Телефон здесь только записывается. Прочитать его нельзя ни отсюда, ни из любого
другого места приложения: у роли `ishmed_app` нет прав на
`product.candidate_contacts`, а `my_candidate_card` отдаёт лишь факт наличия.
"""
from __future__ import annotations

import contextlib
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.bot.handlers import interview as iv
from app.bot.handlers import profile_form as pf
from app.i18n import labels, t
from app.services import candidate as cand
from app.services import interview as iv_svc
from app.services import users

log = logging.getLogger(__name__)
router = Router(name="profile")

MENU_PROFILE = labels("menu_profile")
MENU_INVITATIONS = labels("menu_invitations")
MENU_HELP = labels("menu_help")
MENU_VACANCIES = labels("menu_vacancies")
CONTACT_LATER = labels("btn_contact_later")


async def _show_card(message: Message) -> None:
    """Карточка, кнопки действий, отклики.

    Рендерит `profile_form.card_text`: там та же карточка, что показывается в
    конце анкеты, и держать два похожих формата — верный способ, чтобы они
    разошлись. Он же читает `my_profile_form`, где есть районы, график и
    состояние поиска, чего в `my_candidate_card` нет.
    """
    tg = message.from_user
    if tg is None:
        return
    uid, locale = await users.id_and_locale(tg.id)
    if uid is None:
        await message.answer(t("profile_empty", locale), reply_markup=kb.main_menu(locale))
        return

    card = await cand.form(uid)
    if card is None:
        # Профиля нет вовсе — не тупик: предлагаем завести карточку.
        await message.answer(
            t("profile_empty", locale),
            reply_markup=kb.profile_cta(locale, has_card=False, in_pool=False),
        )
        return

    await pf.show_card(message, uid, locale)
    # Нет телефона — сразу даём его добавить. Без этого «Телефон: не указан»
    # превращается в упрёк без выхода.
    if not card.get("has_contact"):
        await message.answer(t("contact_ask", locale), reply_markup=kb.share_contact(locale))
    await iv.show_my_applications(message, uid, locale)


@router.message(Command("profile"))
async def cmd_profile(message: Message) -> None:
    await _show_card(message)


@router.message(F.text.in_(MENU_PROFILE))
async def btn_profile(message: Message) -> None:
    await _show_card(message)


@router.message(F.text.in_(MENU_VACANCIES))
async def btn_vacancies(message: Message) -> None:
    _, locale = await users.id_and_locale(message.from_user.id)  # type: ignore[union-attr]
    await iv.show_jobs(message, 0, locale)


@router.message(F.text.in_(MENU_HELP))
async def btn_help(message: Message) -> None:
    _, locale = await users.id_and_locale(message.from_user.id)  # type: ignore[union-attr]
    await message.answer(t("help_text", locale))


@router.message(F.text.in_(MENU_INVITATIONS))
async def btn_invitations(message: Message) -> None:
    uid, locale = await users.id_and_locale(message.from_user.id)  # type: ignore[union-attr]
    await show_invitations(message, uid, locale)


async def show_invitations(message: Message, user_id: int | None, locale: str) -> None:
    """Приглашения человека.

    Отклонённые не показываем — их отфильтровывает `my_invitations`. Человек уже
    сказал «нет», и напоминать ему об этом незачем.

    Пусто — не одно сообщение, а два разных. Тому, кого нет в поиске, надо
    сказать про поиск: его не приглашают не потому, что он не нужен, а потому
    что его не видно.
    """
    invites = await cand.invitations(user_id) if user_id else []
    if not invites:
        card = await cand.form(user_id) if user_id else None
        if card is None or not card.get("in_pool"):
            await message.answer(
                t("invitations_empty_hidden", locale),
                reply_markup=kb.profile_cta(
                    locale, has_card=card is not None, in_pool=False
                ),
            )
        else:
            await message.answer(t("invitations_empty", locale))
        return

    await message.answer(t("invitations_title", locale))
    for inv in invites:
        await message.answer(
            _invitation_text(inv, locale),
            reply_markup=(
                kb.invitation_decision(locale, str(inv["invitation_id"]))
                if inv["invitation_status"] == "sent" else None
            ),
        )


def _invitation_text(inv: dict, locale: str) -> str:
    lines = [
        t("invite_card_head", locale, clinic=inv["clinic_name"], job=inv["job_title"]),
    ]
    if inv.get("specialty_name"):
        lines.append(t("job_specialty", locale, name=inv["specialty_name"]))
    if inv.get("experience_min_months"):
        lines.append(t("job_experience", locale,
                       value=iv.experience_phrase(int(inv["experience_min_months"]), locale)))
    lines.append(t("job_salary", locale, value=iv.salary_phrase(inv, locale)))
    if inv.get("schedule"):
        lines.append(t("job_schedule", locale, value=iv.schedule_phrase(inv["schedule"], locale)))
    if inv.get("message"):
        # Слова менеджера показываем как есть и отдельно от наших.
        lines.append("")
        lines.append(f"<i>{inv['message']}</i>")

    lines.append("")
    status = str(inv["invitation_status"])
    if status == "accepted":
        lines.append(
            t("invite_accepted_note", locale) if inv.get("has_application")
            else t("invite_accepted_go", locale)
        )
    elif not inv.get("job_open"):
        lines.append(t("invite_job_closed", locale))
    return "\n".join(lines)


# ── Ответ на приглашение ──────────────────────────────────────────────────────
# Принятие — это согласие открыть клинике контакт (product.reveal_contact
# работает только из статуса accepted). Поэтому текст кнопки и сообщение после
# неё говорят об этом прямо, а не прячут за «спасибо за интерес».

@router.callback_query(F.data.startswith("inv:"))
async def on_invitation_answer(call: CallbackQuery) -> None:
    parts = (call.data or "").split(":", 2)
    if len(parts) < 3 or parts[1] not in ("yes", "no"):
        await call.answer()
        return
    accept, invitation_id = parts[1] == "yes", parts[2]

    uid, locale = await users.id_and_locale(call.from_user.id)
    if uid is None:
        await call.answer()
        return

    try:
        result = await cand.respond_safely(invitation_id, uid, accept=accept)
    except cand.AlreadyAnswered:
        await call.answer(t("invite_already_answered", locale), show_alert=True)
        return
    except Exception:
        log.exception("не удалось ответить на приглашение %s", invitation_id)
        await call.answer(t("invite_failed", locale), show_alert=True)
        return

    await call.answer()
    # Кнопки убираем: решение принято и переигрывать его нельзя.
    with contextlib.suppress(Exception):
        await call.message.edit_reply_markup(reply_markup=None)  # type: ignore[union-attr]

    if not accept:
        await call.message.answer(t("invite_declined", locale))  # type: ignore[union-attr]
        log.info("приглашение %s отклонено", invitation_id)
        return

    log.info("приглашение %s принято", invitation_id)
    await call.message.answer(t("invite_accepted", locale))  # type: ignore[union-attr]

    # Ведём к собеседованию по этой вакансии: приглашение — это «поговорите с
    # нами», а разговор идёт по одобренному плану. Карточку показываем вместо
    # автостарта: человек только что принял решение, второе подряд навязывать
    # незачем.
    job = await iv_svc.job_by_id(str(result["job_id"]))
    if job is None:
        await call.message.answer(t("invite_job_closed", locale))  # type: ignore[union-attr]
        return
    await iv.show_job_card(call.message, job, uid, locale)  # type: ignore[arg-type]


# ── Телефон ───────────────────────────────────────────────────────────────────

@router.message(F.contact)
async def on_contact(message: Message) -> None:
    tg = message.from_user
    if tg is None:
        return
    uid, locale = await users.id_and_locale(tg.id)
    contact = message.contact
    if uid is None or contact is None:
        return

    # Чужой контакт из адресной книги не принимаем: телефон должен принадлежать
    # тому, кто его отправил, иначе клиника позвонит не туда.
    if contact.user_id and contact.user_id != tg.id:
        await message.answer(t("contact_failed", locale), reply_markup=kb.main_menu(locale))
        return

    try:
        await users.save_contact(uid, contact.phone_number, tg.username)
    except users.NoProfile:
        await message.answer(
            t("contact_needs_profile", locale), reply_markup=kb.main_menu(locale)
        )
        return
    except Exception:
        log.exception("не удалось сохранить контакт telegram_id=%s", tg.id)
        await message.answer(t("contact_failed", locale), reply_markup=kb.main_menu(locale))
        return

    log.info("контакт сохранён для telegram_id=%s", tg.id)
    await message.answer(t("contact_saved", locale), reply_markup=kb.main_menu(locale))


@router.message(F.text.in_(CONTACT_LATER))
async def on_contact_later(message: Message) -> None:
    _, locale = await users.id_and_locale(message.from_user.id)  # type: ignore[union-attr]
    await message.answer(t("contact_skipped", locale), reply_markup=kb.main_menu(locale))


# ── Удаление профиля ──────────────────────────────────────────────────────────
# В тексте согласия обещано, что профиль можно удалить в любой момент. Функция
# forget_candidate для этого была с самого начала, кнопки к ней не было —
# обещание держалось на честном слове.

@router.message(Command("forget"))
async def cmd_forget(message: Message) -> None:
    tg = message.from_user
    if tg is None:
        return
    uid, locale = await users.id_and_locale(tg.id)
    card = await users.candidate_card(uid) if uid else None
    if card is None:
        await message.answer(t("forget_nothing", locale))
        return
    await message.answer(t("forget_ask", locale), reply_markup=kb.forget_confirm(locale))


@router.callback_query(F.data == "forget:yes")
async def on_forget_yes(call: CallbackQuery) -> None:
    uid, locale = await users.id_and_locale(call.from_user.id)
    if uid is None:
        await call.answer()
        return
    await users.forget(uid)
    log.info("профиль удалён по требованию telegram_id=%s", call.from_user.id)
    await call.answer()
    await call.message.edit_text(t("forget_done", locale))  # type: ignore[union-attr]


@router.callback_query(F.data == "forget:no")
async def on_forget_no(call: CallbackQuery) -> None:
    _, locale = await users.id_and_locale(call.from_user.id)
    await call.answer()
    await call.message.edit_text(t("forget_cancelled", locale))  # type: ignore[union-attr]
