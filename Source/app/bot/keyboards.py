"""Клавиатуры бота.

Кнопки — не только удобство. Агент недетерминирован, а демо должно пройти
три раза подряд, поэтому у каждого критичного шага есть кнопка: если модель
уедет в сторону, сценарий доводится нажатиями.
"""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

from app.i18n import t


def _suffix(job_id: str | None) -> str:
    """Хвост callback_data с вакансией, к которой надо вернуться.

    Человек приходит по ссылке на конкретную вакансию, и если по пути его
    остановил гейт согласия, после согласия он должен увидеть ту же вакансию,
    а не общее приветствие. Несём код вакансии в самой кнопке: uuid — 36
    символов, вместе с префиксом укладывается в лимит 64 байта, и никакого
    состояния в памяти бота держать не нужно.
    """
    return f":{job_id}" if job_id else ""


def language(job_id: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Русский", callback_data=f"lang:ru{_suffix(job_id)}"),
            InlineKeyboardButton(text="O‘zbekcha", callback_data=f"lang:uz{_suffix(job_id)}"),
        ]]
    )


def consent(locale: str, job_id: str | None = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("consent_accept", locale),
                                  callback_data=f"consent:accept{_suffix(job_id)}")],
            [InlineKeyboardButton(text=t("consent_details", locale), callback_data="consent:details")],
            [InlineKeyboardButton(text=t("i_am_clinic", locale), callback_data="role:clinic")],
        ]
    )


def main_menu(locale: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t("menu_vacancies", locale))],
            [
                KeyboardButton(text=t("menu_profile", locale)),
                KeyboardButton(text=t("menu_invitations", locale)),
            ],
            [KeyboardButton(text=t("menu_help", locale))],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def share_contact(locale: str) -> ReplyKeyboardMarkup:
    """Клавиатура для передачи телефона.

    `request_contact` отдаёт номер, подтверждённый самим Telegram, — его нельзя
    ошибиться при вводе и нельзя выдать чужой. Поэтому ручной ввод не предлагаем.
    Клавиатура одноразовая: висеть поверх следующего разговора ей незачем.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t("btn_share_contact", locale), request_contact=True)],
            [KeyboardButton(text=t("btn_contact_later", locale))],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def hide() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def forget_confirm(locale: str) -> InlineKeyboardMarkup:
    """Удаление профиля — только с подтверждением.

    Кнопка «удалить» без вопроса на репетиции демо срабатывает случайно, а
    forget_candidate необратим.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("btn_forget_yes", locale), callback_data="forget:yes")],
            [InlineKeyboardButton(text=t("btn_forget_no", locale), callback_data="forget:no")],
        ]
    )


# ── Профиль медика: форма и действия ──────────────────────────────────────────
# Вся форма собрана на кнопках, свободного ввода в ней нет ни на одном шаге.
# Причина не в удобстве: роутер профиля подключается первым и ловит текст раньше
# обработчика ответов интервью. Любой шаг «напишите ответ текстом» означал бы,
# что бот однажды запишет ответ на вопрос собеседования в поле профиля.
#
# Второе следствие: коды словарей попадают в базу как коды. Свободный ввод дал
# бы фразы вместо кодов, а по ним матчинг сравнивать не умеет.


def profile_cta(locale: str, *, has_card: bool, in_pool: bool) -> InlineKeyboardMarkup:
    """Кнопки под карточкой профиля.

    Три разных состояния, потому что человеку в них нужно разное: у одного
    карточки нет вовсе, второй заполнил её и не выведен в поиск, третий уже
    ищет работу и хочет перестать.
    """
    rows: list[list[InlineKeyboardButton]] = []
    if not has_card:
        rows.append([InlineKeyboardButton(text=t("btn_create_profile", locale),
                                          callback_data="pf:start")])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    if in_pool:
        rows.append([InlineKeyboardButton(text=t("btn_pool_leave", locale),
                                          callback_data="pf:hide")])
    else:
        rows.append([InlineKeyboardButton(text=t("btn_pool_join", locale),
                                          callback_data="pf:publish")])
    rows.append([InlineKeyboardButton(text=t("btn_profile_edit", locale),
                                      callback_data="pf:edit")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def form_choices(
    items: list[tuple[str, str]], prefix: str, *, columns: int = 2
) -> InlineKeyboardMarkup:
    """Одиночный выбор: категория роли, специальность, опыт, зарплата.

    `items` — пары (код, подпись). Два столбца, потому что подписи вроде
    «Мирзо-Улугбекский район» в один столбец дают простыню на весь экран.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(items), columns):
        rows.append([
            InlineKeyboardButton(text=label, callback_data=f"{prefix}:{code}")
            for code, label in items[i:i + columns]
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def form_multi(
    items: list[tuple[str, str]],
    chosen: set[str],
    prefix: str,
    locale: str,
    *,
    columns: int = 2,
) -> InlineKeyboardMarkup:
    """Множественный выбор с галочками: районы и график.

    Отмеченное видно прямо в кнопке, а не отдельной строкой над клавиатурой:
    выбирая шестой район, человек не должен вспоминать первые пять. Состояние
    берётся из базы на каждую перерисовку, поэтому галочки не разъезжаются с
    тем, что сохранено.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(items), columns):
        rows.append([
            InlineKeyboardButton(
                text=("✓ " + label) if code in chosen else label,
                callback_data=f"{prefix}:{code}",
            )
            for code, label in items[i:i + columns]
        ])
    rows.append([InlineKeyboardButton(text=t("btn_form_done", locale),
                                      callback_data=f"{prefix}_done:x")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def invitation_decision(locale: str, invitation_id: str) -> InlineKeyboardMarkup:
    """Принять или отказаться. Кнопка отказа обязательна.

    Приглашение — единственное сообщение, которое человек получает не по своей
    инициативе. Без возможности сказать «нет» одним нажатием это спам, а не
    приглашение.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("btn_invite_accept", locale),
                                  callback_data=f"inv:yes:{invitation_id}")],
            [InlineKeyboardButton(text=t("btn_invite_decline", locale),
                                  callback_data=f"inv:no:{invitation_id}")],
        ]
    )
