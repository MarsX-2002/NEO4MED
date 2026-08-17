"""Форма профиля медика: «Создать мой профиль».

Второй вход в тот же профиль. Первый — отклик на вакансию: `open_interview`
создаёт черновик, который видит только та клиника. Здесь человек заводит
карточку сам, ни на кого не откликаясь, и после публикации его находят все
клиники через раздел «Подбор».

Профиль ОДИН на человека (`candidate_profiles.user_id UNIQUE`), поэтому второй
вход ничего не создаёт заново: если карточка уже есть, форма показывает её и
спрашивает только то, чего в ней нет. Пришёл по ссылке на вакансию, прошёл
собеседование, потом решил показаться всем — дозаполняет три шага, а не
заполняет семь.

Состояние формы — сама строка в базе. Никакого FSM: следующий шаг выводится из
того, какие поля пусты (`candidate.next_step`). Бот перезапускается посреди
анкеты, человек продолжает там же. Ровно то же решение, что в интервью, и по той
же причине — второй источник состояния однажды разойдётся с первым.

Свободного ввода в форме нет ни на одном шаге. Роутер профиля подключается
ПЕРВЫМ и ловит текст раньше обработчика ответов интервью: шаг «напишите
текстом» означал бы, что однажды ответ на вопрос собеседования уедет в поле
профиля. Плюс словари — в базе лежат коды, а свободный ввод дал бы фразы, по
которым матчинг сравнивать не умеет.
"""
from __future__ import annotations

import contextlib
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from app.bot import keyboards as kb
from app.i18n import plural, t
from app.services import candidate as cand
from app.services import users

log = logging.getLogger(__name__)
router = Router(name="profile_form")

# Подпись шага -> ключ вопроса и ключ названия поля. Одна таблица вместо
# ветвлений в трёх местах.
_STEP_KEYS = {
    "role_category":     ("form_ask_role", "field_role"),
    "specialty":         ("form_ask_specialty", "field_specialty"),
    "experience_months": ("form_ask_experience", "field_experience"),
    "districts":         ("form_ask_districts", "field_districts"),
    "schedule":          ("form_ask_schedule", "field_schedule"),
    "salary_min_uzs":    ("form_ask_salary", "field_salary"),
}


def money(value, locale: str) -> str:
    """Сумма с разделителями. Дубль из handlers.interview намеренно не делаем —
    импортируем оттуда, чтобы формат сумм был один на весь бот."""
    from app.bot.handlers.interview import money as _money

    return _money(value, locale)


def _experience_label(months: int, locale: str) -> str:
    """Опыт словами. До года — «меньше года», а не «0 лет».

    Порог именно 12, а не 0: кнопки формы дают только круглые значения, но
    профиль, заполненный из интервью, содержит любое число месяцев. Полгода,
    показанные как «0 лет», читаются как «без опыта» — для медсестры это
    существенная разница, и она не в нашу пользу.
    """
    if months < 12:
        return t("exp_less_year", locale)
    years = months // 12
    return f"{years} {plural(years, 'years', locale)}"


# ── Карточка ──────────────────────────────────────────────────────────────────

async def card_text(card: dict | None, locale: str) -> str:
    """Полная карточка человека: то, что увидит клиника, плюс состояние поиска.

    Телефона здесь нет и быть не может: `my_profile_form` отдаёт только факт
    `has_contact`, у прикладной роли прав на таблицу контактов нет вовсе.
    """
    if card is None:
        return t("profile_empty", locale)

    lines = [t("profile_title", locale), ""]

    roles = await cand.names_for("roles", [card["role_category"]] if card.get("role_category") else None, locale)
    lines.append(t("profile_role", locale, value=roles[0]) if roles
                 else t("profile_role_unset", locale))

    specs = await cand.names_for("specialties", [card["specialty"]] if card.get("specialty") else None, locale)
    if specs:
        lines.append(t("profile_specialty", locale, value=specs[0]))

    months = card.get("experience_months")
    lines.append(t("profile_experience", locale, value=_experience_label(int(months), locale))
                 if months is not None else t("profile_experience_unset", locale))

    districts = await cand.names_for("districts", card.get("districts"), locale)
    if districts:
        lines.append(t("profile_districts", locale, value=", ".join(districts)))

    schedules = await cand.names_for("schedules", card.get("schedule"), locale)
    if schedules:
        lines.append(t("profile_schedule", locale, value=", ".join(schedules)))

    if card.get("salary_min_uzs"):
        lines.append(t("profile_salary", locale, value=money(card["salary_min_uzs"], locale)))
    if card.get("skills"):
        lines.append(t("profile_skills", locale, value=", ".join(card["skills"][:6])))
    if card.get("languages"):
        lines.append(t("profile_languages", locale, value=", ".join(card["languages"][:4])))

    lines.append("")
    lines.append(t("profile_contact_yes" if card.get("has_contact") else "profile_contact_no", locale))
    lines.append(t("profile_pool_yes" if card.get("in_pool") else "profile_pool_no", locale))
    lines.append("")
    lines.append(t("profile_source_note", locale))
    return "\n".join(lines)


async def show_card(message: Message, user_id: int, locale: str) -> None:
    """Карточка с кнопками действий. Публичная: её же показывает «Мой профиль»."""
    card = await cand.form(user_id)
    await message.answer(
        await card_text(card, locale),
        reply_markup=kb.profile_cta(
            locale,
            has_card=card is not None,
            in_pool=bool(card and card.get("in_pool")),
        ),
    )


async def offer_profile(message: Message, user_id: int, locale: str) -> None:
    """Предложение завести карточку — для тех, кто зашёл просто так.

    Показываем только если карточки нет или она неполная: человеку, который уже
    в поиске, это сообщение читается как «мы про тебя забыли».
    """
    card = await cand.form(user_id)
    if card is not None and cand.next_step(card) is None:
        return
    await message.answer(
        t("profile_offer", locale),
        reply_markup=kb.profile_cta(locale, has_card=False, in_pool=False),
    )


# ── Шаги формы ────────────────────────────────────────────────────────────────

async def _ask(message: Message, user_id: int, locale: str, step: str) -> None:
    """Задаёт один шаг. Все варианты приходят из словарей базы."""
    dicts = await cand.dictionaries()
    card = await cand.form(user_id)
    question_key, _ = _STEP_KEYS[step]

    if step == "role_category":
        items = [(r["code"], cand.dict_name(r, locale)) for r in dicts["roles"]]
        markup = kb.form_choices(items, "pf:role", columns=2)

    elif step == "specialty":
        role = (card or {}).get("role_category")
        items = [
            (s["code"], cand.dict_name(s, locale))
            for s in dicts["specialties"] if s["role_category"] == role
        ]
        if not items:
            # Категория без специальностей в словаре: не тупик, идём дальше.
            await _advance(message, user_id, locale)
            return
        markup = kb.form_choices(items, "pf:spec", columns=1)

    elif step == "experience_months":
        items = [(str(m), _experience_label(m, locale)) for m in cand.EXPERIENCE_CHOICES]
        markup = kb.form_choices(items, "pf:exp", columns=3)

    elif step == "districts":
        items = [(d["code"], cand.dict_name(d, locale)) for d in dicts["districts"]]
        markup = kb.form_multi(items, set((card or {}).get("districts") or []),
                               "pf:dis", locale, columns=2)

    elif step == "schedule":
        items = [(s["code"], cand.dict_name(s, locale)) for s in dicts["schedules"]]
        markup = kb.form_multi(items, set((card or {}).get("schedule") or []),
                               "pf:sch", locale, columns=2)

    else:  # salary_min_uzs
        items = [(str(v), money(v, locale)) for v in cand.SALARY_CHOICES]
        items.append(("skip", t("btn_salary_skip", locale)))
        markup = kb.form_choices(items, "pf:sal", columns=2)

    await message.answer(t(question_key, locale), reply_markup=markup)


async def _advance(message: Message, user_id: int, locale: str) -> None:
    """Ведёт к следующему незаполненному шагу либо к итогу.

    Зарплата не входит в обязательные шаги: карточка без ожиданий публикуется, а
    в подборе это станет пробелом «не назвал сумму». Но спросить один раз стоит —
    без неё клиника не понимает, о чём разговор.
    """
    card = await cand.form(user_id)
    step = cand.next_step(card)
    if step is not None:
        await _ask(message, user_id, locale, step)
        return
    if card is not None and card.get("salary_min_uzs") is None:
        await _ask(message, user_id, locale, "salary_min_uzs")
        return
    await _finish(message, user_id, locale)


async def _finish(message: Message, user_id: int, locale: str) -> None:
    """Итог формы: карточка, просьба о телефоне и кнопка выхода в поиск.

    Телефон просим здесь, а не на шаге формы: до этого момента человек не знает,
    зачем он нужен. `request_contact` отдаёт номер, подтверждённый самим
    Telegram, — подставить чужой нельзя.
    """
    card = await cand.form(user_id)
    await message.answer(await card_text(card, locale))
    if card is not None and not card.get("has_contact"):
        await message.answer(t("contact_ask", locale), reply_markup=kb.share_contact(locale))
    await message.answer(
        t("form_ready", locale),
        reply_markup=kb.profile_cta(
            locale, has_card=True, in_pool=bool(card and card.get("in_pool"))
        ),
    )


# ── Вход ──────────────────────────────────────────────────────────────────────

@router.message(Command("myprofile"))
async def cmd_form(message: Message) -> None:
    uid, locale = await users.id_and_locale(message.from_user.id)  # type: ignore[union-attr]
    if uid is None:
        await message.answer(t("profile_needs_start", locale))
        return
    await _advance(message, uid, locale)


@router.callback_query(F.data == "pf:start")
async def on_start(call: CallbackQuery) -> None:
    user = await users.ensure_medic(call.from_user.id, full_name=call.from_user.full_name)
    locale = user.get("locale") or "ru"
    await call.answer()

    # Тот же гейт согласия, что и перед откликом: форма записывает о человеке
    # данные, а согласие должно быть раньше любой записи (критерий A2).
    from app.bot.handlers.interview import gate_consent

    if await gate_consent(call.message, user, None):  # type: ignore[arg-type]
        return
    await call.message.answer(t("form_intro", locale))  # type: ignore[union-attr]
    await _advance(call.message, int(user["id"]), locale)  # type: ignore[arg-type]


@router.callback_query(F.data == "pf:edit")
async def on_edit(call: CallbackQuery) -> None:
    """Что именно менять. Переспрашивать всю анкету ради одного поля — верный
    способ, чтобы её больше никто не открыл."""
    uid, locale = await users.id_and_locale(call.from_user.id)
    await call.answer()
    if uid is None:
        return
    items = [(step, t(name_key, locale)) for step, (_, name_key) in _STEP_KEYS.items()]
    await call.message.answer(  # type: ignore[union-attr]
        t("form_pick_field", locale),
        reply_markup=kb.form_choices(items, "pf:go", columns=2),
    )


@router.callback_query(F.data.startswith("pf:go:"))
async def on_go(call: CallbackQuery) -> None:
    step = call.data.split(":", 2)[2]  # type: ignore[union-attr]
    if step not in _STEP_KEYS:
        await call.answer()
        return
    uid, locale = await users.id_and_locale(call.from_user.id)
    await call.answer()
    if uid is None:
        return
    await _ask(call.message, uid, locale, step)  # type: ignore[arg-type]


# ── Ответы на шаги ────────────────────────────────────────────────────────────

async def _who(call: CallbackQuery) -> tuple[int | None, str]:
    return await users.id_and_locale(call.from_user.id)


@router.callback_query(F.data.startswith("pf:role:"))
async def on_role(call: CallbackQuery) -> None:
    code = call.data.split(":", 2)[2]  # type: ignore[union-attr]
    uid, locale = await _who(call)
    if uid is None:
        await call.answer()
        return
    await cand.save(uid, role_category=code)
    await call.answer(t("form_saved", locale))
    # Смена категории обнуляет специальность (это делает save_my_profile),
    # поэтому следующий шаг сам окажется «специальность».
    await _advance(call.message, uid, locale)  # type: ignore[arg-type]


@router.callback_query(F.data.startswith("pf:spec:"))
async def on_specialty(call: CallbackQuery) -> None:
    code = call.data.split(":", 2)[2]  # type: ignore[union-attr]
    uid, locale = await _who(call)
    if uid is None:
        await call.answer()
        return
    await cand.save(uid, specialty=code)
    await call.answer(t("form_saved", locale))
    await _advance(call.message, uid, locale)  # type: ignore[arg-type]


@router.callback_query(F.data.startswith("pf:exp:"))
async def on_experience(call: CallbackQuery) -> None:
    raw = call.data.split(":", 2)[2]  # type: ignore[union-attr]
    uid, locale = await _who(call)
    if uid is None or not raw.isdigit():
        await call.answer()
        return
    await cand.save(uid, experience_months=int(raw))
    await call.answer(t("form_saved", locale))
    await _advance(call.message, uid, locale)  # type: ignore[arg-type]


async def _toggle(call: CallbackQuery, field: str, prefix: str, dict_kind: str) -> None:
    """Переключает один пункт множественного выбора и перерисовывает клавиатуру.

    Перерисовываем по данным из базы, а не по локальной копии: два быстрых
    нажатия иначе разъедутся с сохранённым.
    """
    code = call.data.split(":", 2)[2]  # type: ignore[union-attr]
    uid, locale = await _who(call)
    if uid is None:
        await call.answer()
        return

    card = await cand.form(uid)
    current = list((card or {}).get(field) or [])
    if code in current:
        current.remove(code)
    else:
        current.append(code)

    try:
        await cand.save(uid, **{field: current})
    except Exception as e:
        log.warning("не удалось сохранить %s: %s", field, e)
        await call.answer(t("form_save_failed", locale), show_alert=True)
        return

    await call.answer()
    dicts = await cand.dictionaries()
    items = [(r["code"], cand.dict_name(r, locale)) for r in dicts[dict_kind]]
    # Telegram отказывается менять сообщение на идентичное. Для нас это не
    # ошибка: галочка уже стоит там, где надо.
    with contextlib.suppress(Exception):
        await call.message.edit_reply_markup(  # type: ignore[union-attr]
            reply_markup=kb.form_multi(items, set(current), prefix, locale, columns=2)
        )


@router.callback_query(F.data.startswith("pf:dis:"))
async def on_district(call: CallbackQuery) -> None:
    await _toggle(call, "districts", "pf:dis", "districts")


@router.callback_query(F.data.startswith("pf:sch:"))
async def on_schedule(call: CallbackQuery) -> None:
    await _toggle(call, "schedule", "pf:sch", "schedules")


@router.callback_query(F.data.startswith("pf:dis_done:") | F.data.startswith("pf:sch_done:"))
async def on_multi_done(call: CallbackQuery) -> None:
    uid, locale = await _who(call)
    if uid is None:
        await call.answer()
        return
    card = await cand.form(uid)
    field = "districts" if call.data.startswith("pf:dis_done") else "schedule"  # type: ignore[union-attr]
    if not (card or {}).get(field):
        # Ни одного пункта: дальше пускать нельзя, иначе карточка не опубликуется,
        # и человек не поймёт, почему.
        await call.answer(t("form_pick_at_least_one", locale), show_alert=True)
        return
    await call.answer()
    await _advance(call.message, uid, locale)  # type: ignore[arg-type]


@router.callback_query(F.data.startswith("pf:sal:"))
async def on_salary(call: CallbackQuery) -> None:
    raw = call.data.split(":", 2)[2]  # type: ignore[union-attr]
    uid, locale = await _who(call)
    if uid is None:
        await call.answer()
        return
    if raw == "skip":
        await call.answer()
        # Прямо к итогу, а не через _advance: иначе пустая зарплата снова
        # привела бы к этому же вопросу, и кнопка «не указывать» не работала бы.
        await _finish(call.message, uid, locale)  # type: ignore[arg-type]
        return
    if not raw.isdigit():
        await call.answer()
        return
    await cand.save(uid, salary_min_uzs=int(raw))
    await call.answer(t("form_saved", locale))
    await _finish(call.message, uid, locale)  # type: ignore[arg-type]


# ── Вход в поиск и выход из него ──────────────────────────────────────────────

@router.callback_query(F.data == "pf:publish")
async def on_publish(call: CallbackQuery) -> None:
    uid, locale = await _who(call)
    if uid is None:
        await call.answer()
        return
    try:
        result = await cand.publish(uid)
    except Exception as e:
        log.warning("публикация профиля не удалась: %s", e)
        await call.answer(t("form_save_failed", locale), show_alert=True)
        return

    if not result["published"]:
        # Говорим, чего именно не хватает. Общий отказ человек читает как
        # поломку и уходит.
        missing = ", ".join(
            t(_STEP_KEYS[m][1], locale) for m in result["missing"] if m in _STEP_KEYS
        )
        await call.answer()
        await call.message.answer(t("pool_incomplete", locale, fields=missing))  # type: ignore[union-attr]
        await _advance(call.message, uid, locale)  # type: ignore[arg-type]
        return

    await call.answer(t("pool_joined_toast", locale))
    await call.message.answer(t("pool_joined", locale))  # type: ignore[union-attr]
    if not result["has_contact"]:
        # Без телефона клиника не сможет позвонить даже после accept. Просим
        # здесь, где просьба уже осмысленна.
        await call.message.answer(  # type: ignore[union-attr]
            t("pool_needs_contact", locale), reply_markup=kb.share_contact(locale)
        )
    log.info("профиль выведен в общий поиск, user_id=%s", uid)


@router.callback_query(F.data == "pf:hide")
async def on_hide(call: CallbackQuery) -> None:
    uid, locale = await _who(call)
    if uid is None:
        await call.answer()
        return
    await cand.hide(uid)
    await call.answer(t("pool_left_toast", locale))
    await call.message.answer(  # type: ignore[union-attr]
        t("pool_left", locale),
        reply_markup=kb.profile_cta(locale, has_card=True, in_pool=False),
    )
    log.info("профиль убран из общего поиска, user_id=%s", uid)
