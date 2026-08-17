"""Вакансии и авто-интервью в боте медика.

Кнопки есть на каждом шаге сознательно: модель недетерминирована, а сценарий
должен проходиться три раза подряд. Если ответ модели уедет в сторону, разговор
доводится нажатиями — «Ответить голосом», «Пропустить вопрос», «Закончить».

Голос работает в обе стороны: вопрос можно послушать (Azure tts, ~2 c, ogg/opus
уходит в Telegram как голосовое без перекодирования), ответить можно голосом
(gpt-4o-transcribe принимает ogg напрямую).
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app.bot import keyboards as kb
from app.i18n import DEFAULT_LOCALE, labels, plural, t
from app.services import interview as svc
from app.services import review_intake, users

log = logging.getLogger(__name__)
router = Router(name="interview")

# Длиннее пяти минут — это не ответ на вопрос собеседования, а монолог.
MAX_VOICE_SECONDS = 300
JOBS_PAGE = 5

# Подписи кнопок на всех языках: их нельзя принимать за ответ кандидата.
MENU_LABELS = labels(
    "menu_vacancies", "menu_profile", "menu_invitations", "menu_help",
    "btn_contact_later", "btn_share_contact",
)


async def _who(telegram_user_id: int) -> tuple[int | None, str]:
    """product.users.id и язык человека. Язык — здесь же, чтобы не ходить в базу
    второй раз на каждое нажатие кнопки."""
    return await users.id_and_locale(telegram_user_id)


def money(value, locale: str) -> str:
    if value is None:
        return t("salary_unset", locale)
    return f"{int(value):,}".replace(",", " ") + " " + t("currency", locale)


def salary_phrase(job: dict, locale: str) -> str:
    lo, hi = job.get("salary_min_uzs"), job.get("salary_max_uzs")
    if lo and hi:
        return f"{money(lo, locale)} — {money(hi, locale)}"
    if lo:
        return t("salary_from", locale, value=money(lo, locale))
    if hi:
        return t("salary_to", locale, value=money(hi, locale))
    return t("salary_unset", locale)


def experience_phrase(months: int, locale: str) -> str:
    """«3 года» / «6 месяцев» / «3 yil».

    До года считаем месяцами: «от 0.5 года» звучит как ошибка ввода, а для
    медсестры с полугодом опыта это существенная разница.
    """
    if months < 12:
        return f"{months} {plural(months, 'months_from', locale)}"
    years = months // 12
    return f"{years} {plural(years, 'years_from', locale)}"


def schedule_phrase(codes: list[str], locale: str) -> str:
    return ", ".join(t(f"schedule_{c}", locale) for c in codes)


def _job_card(job: dict, locale: str) -> str:
    count = job.get("questions_count") or 0
    lines = [
        f"<b>{job['title']}</b>",
        t("job_clinic", locale, name=job["clinic_name"]),
    ]
    if job.get("specialty_name"):
        lines.append(t("job_specialty", locale, name=job["specialty_name"]))
    if job.get("experience_min_months"):
        lines.append(
            t("job_experience", locale,
              value=experience_phrase(int(job["experience_min_months"]), locale))
        )
    lines.append(t("job_salary", locale, value=salary_phrase(job, locale)))
    if job.get("schedule"):
        lines.append(t("job_schedule", locale, value=schedule_phrase(job["schedule"], locale)))
    if job.get("required_skills"):
        lines.append(t("job_skills", locale, value=", ".join(job["required_skills"][:5])))
    lines.append("")
    lines.append(
        t("job_interview_note", locale,
          count=count, questions_word=plural(count, "questions", locale))
    )
    return "\n".join(lines)


# ── Клавиатуры ────────────────────────────────────────────────────────────────

def _kb_job(job_id: str, locale: str, *, applied: bool) -> dict:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    if applied:
        rows.append([
            InlineKeyboardButton(text=t("btn_continue", locale),
                                 callback_data=f"iv:go:{job_id}")
        ])
    else:
        rows.append([
            InlineKeyboardButton(text=t("btn_apply", locale),
                                 callback_data=f"iv:apply:{job_id}")
        ])
    rows.append([
        InlineKeyboardButton(text=t("btn_jobs_list", locale), callback_data="iv:list:0")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_list(jobs: list[dict], offset: int, locale: str) -> dict:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows = [
        [InlineKeyboardButton(text=f"{j['title']} — {j['clinic_name']}",
                              callback_data=f"iv:job:{j['job_id']}")]
        for j in jobs
    ]
    nav = []
    if offset:
        nav.append(InlineKeyboardButton(
            text=t("btn_prev", locale),
            callback_data=f"iv:list:{max(offset - JOBS_PAGE, 0)}"))
    if len(jobs) == JOBS_PAGE:
        nav.append(InlineKeyboardButton(
            text=t("btn_more", locale), callback_data=f"iv:list:{offset + JOBS_PAGE}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_question(locale: str, *, with_voice: bool = True) -> dict:
    """Кнопки под каждым вопросом.

    «Послушать вопрос» нужна не для красоты: часть медсестёр читает по-русски
    хуже, чем понимает на слух. Для узбекоязычного кандидата это тем более так —
    вопрос он услышит на языке клиники, а подписи кнопок на своём.
    """
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    if with_voice:
        rows.append([
            InlineKeyboardButton(text=t("btn_listen", locale), callback_data="iv:say")
        ])
    rows.append([
        InlineKeyboardButton(text=t("btn_skip", locale), callback_data="iv:skip"),
        InlineKeyboardButton(text=t("btn_stop", locale), callback_data="iv:stop"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_after_finish(locale: str) -> dict:
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t("btn_other_jobs", locale),
                                  callback_data="iv:list:0")],
            [InlineKeyboardButton(text=t("btn_my_applications", locale),
                                  callback_data="iv:mine")],
        ]
    )


# ── Вход по deep link ─────────────────────────────────────────────────────────

async def show_job_card(message: Message, job: dict, user_id: int, locale: str) -> None:
    """Карточка вакансии с кнопкой отклика. Публичная: сюда же возвращается
    человек после того, как дал согласие."""
    job_id = str(job["job_id"])
    apps = {str(a["job_id"]) for a in await svc.my_applications(user_id)}
    await message.answer(
        _job_card(job, locale),
        reply_markup=_kb_job(job_id, locale, applied=job_id in apps),
    )


async def gate_consent(message: Message, user: dict, job_id: str | None) -> bool:
    """Не пускает дальше без согласия. True означает «остановлены».

    Ссылка на вакансию ведёт мимо `/start`, поэтому проверка согласия только
    там не работает: двое живых людей прошли собеседование, не дав согласия и
    не выбрав язык. Гейт стоит перед КАЖДЫМ действием, которое что-то о человеке
    записывает, а не в одном месте входа.

    Вакансию несём в callback_data кнопок, чтобы после согласия вернуть человека
    к тому, ради чего он пришёл.
    """
    if users.has_consent(user):
        return False

    locale = user.get("locale")
    if not locale:
        # Язык ещё не выбран: сначала он, согласие — следующим шагом.
        await message.answer(t("choose_language"), reply_markup=kb.language(job_id))
        return True

    await message.answer(t("consent_before_apply", locale))
    await message.answer(t("consent_ask", locale), reply_markup=kb.consent(locale, job_id))
    return True


@router.message(CommandStart(deep_link=True, magic=F.args.startswith("job_")))
async def deep_link_job(message: Message) -> None:
    """t.me/ishmedbot?start=job_<код> — сразу карточка вакансии."""
    tg = message.from_user
    if tg is None:
        return
    code = (message.text or "").split("job_", 1)[-1].strip()
    user = await users.ensure_medic(tg.id, full_name=tg.full_name)
    locale = user.get("locale") or DEFAULT_LOCALE

    job = await svc.job_by_code(code)
    if job is None:
        await message.answer(t("job_gone", locale))
        return

    log.info("deep link на вакансию %s от telegram_id=%s", code, tg.id)
    if await gate_consent(message, user, str(job["job_id"])):
        return
    await show_job_card(message, job, int(user["id"]), locale)


@router.message(Command("vacancies"))
async def cmd_vacancies(message: Message) -> None:
    _, locale = await _who(message.from_user.id)  # type: ignore[union-attr]
    await show_jobs(message, 0, locale)


@router.callback_query(F.data.startswith("iv:list:"))
async def on_list(call: CallbackQuery) -> None:
    offset = int(call.data.rsplit(":", 1)[1])  # type: ignore[union-attr]
    _, locale = await _who(call.from_user.id)
    await call.answer()
    await show_jobs(call.message, offset, locale)  # type: ignore[arg-type]


async def show_jobs(message: Message, offset: int, locale: str) -> None:
    jobs = await svc.published_jobs(limit=JOBS_PAGE, offset=offset)
    if not jobs:
        await message.answer(
            t("jobs_empty" if offset == 0 else "jobs_end", locale)
        )
        return
    await message.answer(
        t("jobs_header", locale, first=offset + 1, last=offset + len(jobs)),
        reply_markup=_kb_list(jobs, offset, locale),
    )


@router.callback_query(F.data.startswith("iv:job:"))
async def on_job(call: CallbackQuery) -> None:
    job_id = call.data.rsplit(":", 1)[1]  # type: ignore[union-attr]
    await call.answer()
    user = await users.ensure_medic(call.from_user.id, full_name=call.from_user.full_name)
    locale = user.get("locale") or DEFAULT_LOCALE
    job = await svc.job_by_id(job_id)
    if job is None:
        await call.message.answer(t("job_closed", locale))  # type: ignore[union-attr]
        return
    if await gate_consent(call.message, user, job_id):  # type: ignore[arg-type]
        return
    await show_job_card(call.message, job, int(user["id"]), locale)  # type: ignore[arg-type]


@router.callback_query(F.data == "iv:mine")
async def on_mine(call: CallbackQuery) -> None:
    await call.answer()
    uid, locale = await _who(call.from_user.id)
    await show_my_applications(call.message, uid, locale)  # type: ignore[arg-type]


async def show_my_applications(message: Message, user_id: int | None, locale: str) -> None:
    """Отклики человека и ход интервью по каждому. Публичная: то же показывает
    карточка профиля."""
    apps = await svc.my_applications(user_id) if user_id else []
    if not apps:
        await message.answer(t("applications_empty", locale))
        return
    lines = [t("applications_title", locale), ""]
    for a in apps:
        iv = a.get("interview_status")
        if iv == "completed":
            progress = t("progress_done", locale)
        elif iv == "in_progress":
            progress = t("progress_running", locale,
                         answered=a["answered_count"], total=a["total_questions"])
        else:
            progress = t("progress_none", locale)
        status = str(a["app_status"])
        lines.append(
            t("application_line", locale,
              title=a["job_title"], clinic=a["clinic_name"],
              status=t(f"app_status_{status}", locale), progress=progress)
        )
    await message.answer("\n".join(lines))


# ── Отклик и начало интервью ──────────────────────────────────────────────────

@router.callback_query(F.data.startswith("iv:apply:"))
async def on_apply(call: CallbackQuery, bot: Bot) -> None:
    job_id = call.data.rsplit(":", 1)[1]  # type: ignore[union-attr]
    user = await users.ensure_medic(call.from_user.id, full_name=call.from_user.full_name)
    uid = int(user["id"])
    locale = user.get("locale") or DEFAULT_LOCALE

    job = await svc.job_by_id(job_id)
    if job is None:
        await call.answer(t("job_closed", locale), show_alert=True)
        return

    # Второй рубеж того же гейта. Кнопка «Откликнуться» может остаться в чате с
    # прошлого раза, и нажать её можно, ни разу не проходя через карточку.
    if not users.has_consent(user):
        await call.answer()
        await gate_consent(call.message, user, job_id)  # type: ignore[arg-type]
        return

    opened = await svc.open_for(uid, job_id)
    await call.answer(t("applied_toast", locale))

    if not opened["is_new"] and opened["status"] == "completed":
        await call.message.answer(  # type: ignore[union-attr]
            t("already_interviewed", locale),
            reply_markup=_kb_after_finish(locale),
        )
        return

    # Приветствие клиники не переводим: его писал менеджер, и это часть того,
    # что он одобрил. Своё показываем только когда клиника ничего не написала.
    count = job.get("questions_count") or 0
    intro = job.get("interview_intro") or t(
        "interview_intro", locale,
        count=count,
        questions_word=plural(count, "questions", locale),
        clinic=job["clinic_name"],
    )
    await call.message.answer(intro)  # type: ignore[union-attr]
    await _ask_next(call.message, bot, str(opened["interview_id"]), uid, locale)  # type: ignore[arg-type]


@router.callback_query(F.data.startswith("iv:go:"))
async def on_continue(call: CallbackQuery, bot: Bot) -> None:
    await call.answer()
    uid, locale = await _who(call.from_user.id)
    state = await svc.active(uid) if uid else None
    if state is None:
        await call.message.answer(  # type: ignore[union-attr]
            t("no_active_interview", locale), reply_markup=_kb_after_finish(locale)
        )
        return
    if state.get("pending_question"):
        # Уточнение не получает своего номера: это добор к текущему вопросу,
        # а не следующий пункт плана.
        head = (
            t("resume_follow_up", locale)
            if state.get("pending_is_follow_up")
            else t("resume_question", locale,
                   ord=state["answered_count"] + 1, total=state["total_questions"])
        )
        await call.message.answer(  # type: ignore[union-attr]
            f"{head}\n\n{state['pending_question']}", reply_markup=_kb_question(locale)
        )
        return
    await _ask_next(
        call.message, bot, str(state["interview_id"]), uid, locale  # type: ignore[arg-type]
    )


# ── Ядро разговора ────────────────────────────────────────────────────────────

async def _ask_next(
    message: Message, bot: Bot, interview_id: str, user_id: int, locale: str
) -> None:
    """Задаёт следующий вопрос из плана либо закрывает интервью.

    Вопрос выбирает база: порядок фиксирован планом, а предел ходов проверяется
    там же. Здесь только отправка. Текст самого вопроса идёт как есть — на языке
    клиники: менеджер его одобрял, и подменять одобренное переводом нельзя.
    """
    nxt = await svc.next_question(interview_id)
    if nxt is None:
        await _finish(message, interview_id, user_id, locale)
        return

    await svc.ask(interview_id, question_id=str(nxt["question_id"]), text=nxt["question"])
    header = t("question_header", locale, ord=nxt["ord"], total=nxt["total"])
    await message.answer(
        f"{header}\n\n{nxt['question']}",
        reply_markup=_kb_question(locale),
    )


async def _finish(message: Message, interview_id: str, user_id: int, locale: str) -> None:
    # Сборка саммари занимает около восьми секунд: предупреждаем, иначе
    # человек решит, что разговор оборвался, и закроет чат.
    await message.answer(t("interview_wrapping", locale))
    result = await svc.finish(interview_id, user_id)
    await message.answer(
        t("interview_done", locale, answered=result["answered"]),
        reply_markup=_kb_after_finish(locale),
    )

    # Телефон спрашиваем здесь и только здесь: до собеседования человек ещё не
    # знает, стоит ли оно того, а после — уже понимает, зачем клинике звонить.
    # Если контакт уже есть, второй раз не просим.
    card = await users.candidate_card(user_id)
    if card is not None and not card.get("has_contact"):
        await message.answer(t("contact_ask", locale), reply_markup=kb.share_contact(locale))


@router.callback_query(F.data == "iv:say")
async def on_say(call: CallbackQuery, bot: Bot) -> None:
    """Озвучивает текущий вопрос, переиспользуя уже записанное.

    Вопросы плана одинаковы для всех кандидатов, поэтому первое нажатие
    синтезирует запись и сохраняет её file_id, а дальше Telegram отдаёт то же
    аудио сам. Это не только быстрее: квота tts — три запроса в минуту на всю
    подписку, и без кэша два одновременных кандидата в неё упрутся.
    """
    uid, locale = await _who(call.from_user.id)
    state = await svc.active(uid) if uid else None
    if state is None or not state.get("pending_question"):
        await call.answer(t("no_pending_question", locale), show_alert=True)
        return

    chat_id = call.message.chat.id  # type: ignore[union-attr]
    cached = state.get("pending_voice_file_id")
    if cached:
        await call.answer()
        await bot.send_voice(chat_id, cached)
        return

    await call.answer(t("voice_recording", locale))
    audio = await svc.voice_for(state["pending_question"])
    if audio is None:
        await call.message.answer(t("voice_failed", locale))  # type: ignore[union-attr]
        return

    sent = await bot.send_voice(
        chat_id, BufferedInputFile(audio, filename="question.ogg")
    )
    # Уточнения не кэшируем: они сочинены под конкретный ответ и не повторятся.
    question_id = state.get("pending_question_id")
    if question_id and sent.voice and uid:
        await svc.remember_question_voice(str(question_id), uid, sent.voice.file_id)


@router.callback_query(F.data == "iv:skip")
async def on_skip(call: CallbackQuery, bot: Bot) -> None:
    uid, locale = await _who(call.from_user.id)
    state = await svc.active(uid) if uid else None
    if state is None:
        await call.answer(t("interview_not_running", locale), show_alert=True)
        return
    if not state.get("pending_question"):
        await call.answer()
        await _ask_next(call.message, bot, str(state["interview_id"]), uid, locale)  # type: ignore[arg-type]
        return
    await call.answer(t("skipped_toast", locale))
    await svc.record_answer(str(state["interview_id"]), kind="skipped", text=None)
    await _ask_next(call.message, bot, str(state["interview_id"]), uid, locale)  # type: ignore[arg-type]


@router.callback_query(F.data == "iv:stop")
async def on_stop(call: CallbackQuery) -> None:
    uid, locale = await _who(call.from_user.id)
    state = await svc.active(uid) if uid else None
    if state is None:
        await call.answer(t("interview_not_running", locale), show_alert=True)
        return
    await call.answer()
    # Закрываем как завершённое, а не брошенное: на вопросы человек ответил,
    # и клинике эти ответы полезны. Сколько именно — видно по счётчику.
    await _finish(call.message, str(state["interview_id"]), uid, locale)  # type: ignore[arg-type]


# ── Приём ответов ─────────────────────────────────────────────────────────────

@router.message(F.voice)
async def on_voice_answer(message: Message, bot: Bot) -> None:
    tg = message.from_user
    if tg is None:
        return
    uid, locale = await _who(tg.id)
    state = await svc.active(uid) if uid else None
    if state is None or not state.get("pending_question"):
        # Не наш случай. SkipHandler, а не return: иначе апдейт считается
        # обработанным, и голосовое вне интервью уходит в тишину вместо
        # понятного ответа из общего обработчика.
        raise SkipHandler

    voice = message.voice
    assert voice is not None
    if (voice.duration or 0) > MAX_VOICE_SECONDS:
        await message.answer(t("voice_too_long", locale))
        return

    note = await message.answer(t("voice_listening", locale))
    transcript = await review_intake.transcribe_voice(bot, voice.file_id)
    if not transcript:
        await note.edit_text(t("voice_not_recognized", locale))
        return
    await note.edit_text(t("voice_transcribed", locale, text=transcript))

    await svc.record_answer(
        str(state["interview_id"]),
        kind="voice",
        text=transcript,
        voice_file_id=voice.file_id,
        voice_seconds=voice.duration,
    )
    await _after_answer(message, bot, state, transcript, uid, locale)


@router.message(F.text & ~F.text.startswith("/"))
async def on_text_answer(message: Message, bot: Bot) -> None:
    tg = message.from_user
    if tg is None:
        return
    answer = (message.text or "").strip()
    # Подписи кнопок меню приходят обычным текстом. Раньше они записывались как
    # ответ на текущий вопрос, и клиника читала в транскрипте «Помощь».
    # Роутер профиля стоит раньше и разбирает их сам, это второй рубеж.
    if answer in MENU_LABELS:
        raise SkipHandler

    uid, locale = await _who(tg.id)
    state = await svc.active(uid) if uid else None
    if state is None or not state.get("pending_question"):
        raise SkipHandler
    await svc.record_answer(str(state["interview_id"]), kind="text", text=answer)
    await _after_answer(message, bot, state, answer, uid, locale)


async def _after_answer(
    message: Message, bot: Bot, state: dict, answer: str, user_id: int, locale: str
) -> None:
    """Решает, уточнить или идти дальше.

    Уточнение допускается одно на вопрос плана, и считает их база. Без этого
    предохранителя модель способна переспрашивать бесконечно, а человек уйдёт.

    Уточнение — единственный вопрос, который сочиняет модель, а не менеджер,
    поэтому оно и единственное, что мы вправе выдать на языке кандидата.

    Оценка ответа занимает около четырёх секунд. Без индикатора набора эта
    пауза читается как «бот повис», и люди начинают дописывать сообщения.
    """
    interview_id = str(state["interview_id"])

    used = await svc.follow_ups_used(interview_id)
    if used < svc.MAX_FOLLOW_UPS_PER_QUESTION:
        await bot.send_chat_action(message.chat.id, "typing")
        sufficient, follow_up = await svc.judge_answer(
            state["pending_question"], answer, locale=locale
        )
        if not sufficient and follow_up:
            await svc.ask(interview_id, question_id=None, text=follow_up, kind="follow_up")
            await message.answer(follow_up, reply_markup=_kb_question(locale))
            return

    await _ask_next(message, bot, interview_id, user_id, locale)
