"""Слой Telegram-хендлеров: гейт согласия, подписи кнопок, карточка медика.

Этот слой был единственным непокрытым, и все дыры, найденные аудитом, жили
именно здесь: согласие обходилось через ссылку на вакансию, нажатие «Помощь»
записывалось в транскрипт как ответ кандидата, а «Мой профиль» уходил в тишину.

Живой Telegram здесь не нужен. Проверяемые функции трогают только объект
сообщения, поэтому подставляем заглушку и смотрим, что именно бот ответил и
какие кнопки показал. Это ровно те решения, в которых были ошибки.
"""
from __future__ import annotations

from typing import Any

import pytest
from aiogram.dispatcher.event.bases import SkipHandler

from app.bot import keyboards as kb
from app.bot.handlers import interview as iv
from app.bot.handlers import profile as pf
from app.bot.handlers import profile_form as pform
from app.i18n import labels, t

pytestmark = pytest.mark.asyncio


class FakeMessage:
    """Минимальный двойник Message: копит ответы вместо отправки в Telegram."""

    def __init__(self, text: str | None = None, user_id: int = -900_555):
        self.text = text
        self.answers: list[dict[str, Any]] = []
        self.from_user = type("U", (), {"id": user_id, "full_name": "TEST", "username": None})()
        self.chat = type("C", (), {"id": user_id})()

    async def answer(self, text: str, **kwargs) -> FakeMessage:
        self.answers.append({"text": text, **kwargs})
        return self

    @property
    def texts(self) -> str:
        return "\n".join(a["text"] for a in self.answers)

    @property
    def buttons(self) -> list[str]:
        out: list[str] = []
        for a in self.answers:
            markup = a.get("reply_markup")
            for row in getattr(markup, "inline_keyboard", []) or []:
                out += [b.callback_data or b.text for b in row]
            for row in getattr(markup, "keyboard", []) or []:
                out += [b.text for b in row]
        return out


# ─────────────────────────── гейт согласия ───────────────────────────

async def test_consent_gate_stops_person_without_consent():
    """Ссылка на вакансию ведёт мимо /start — гейт обязан остановить."""
    msg = FakeMessage()
    stopped = await iv.gate_consent(msg, {"locale": "ru", "consent_at": None}, "job-1")

    assert stopped is True
    assert t("consent_before_apply", "ru") in msg.texts
    assert "consent:accept:job-1" in msg.buttons, "вакансия должна пережить гейт"


async def test_consent_gate_asks_language_first_when_unknown():
    """Языка нет — сначала язык, иначе согласие покажут на чужом языке.

    Именно так и получалось: двое узбекоязычных прошли собеседование на
    русском, потому что языка их никто не спросил.
    """
    msg = FakeMessage()
    stopped = await iv.gate_consent(msg, {"locale": None, "consent_at": None}, "job-7")

    assert stopped is True
    assert "lang:uz:job-7" in msg.buttons
    assert "lang:ru:job-7" in msg.buttons


async def test_consent_gate_lets_through_with_consent():
    msg = FakeMessage()
    stopped = await iv.gate_consent(
        msg, {"locale": "uz", "consent_at": "2026-08-01T00:00:00+00:00"}, "job-1"
    )
    assert stopped is False
    assert msg.answers == []


async def test_gate_without_job_keeps_flow_generic():
    """Человек пришёл не по ссылке — несём пустой хвост, а не строку 'None'."""
    msg = FakeMessage()
    await iv.gate_consent(msg, {"locale": "ru", "consent_at": None}, None)
    assert "consent:accept" in msg.buttons
    assert not any(b.endswith(":None") for b in msg.buttons)


# ─────────────────────────── подписи кнопок ───────────────────────────

async def test_menu_labels_cover_both_locales():
    """Человек мог сменить язык, а клавиатура в чате осталась старая."""
    assert t("menu_help", "ru") in iv.MENU_LABELS
    assert t("menu_help", "uz") in iv.MENU_LABELS
    assert t("menu_profile", "uz") in iv.MENU_LABELS


async def test_menu_label_is_not_recorded_as_interview_answer():
    """«Помощь», нажатая на вопросе, не должна уехать клинике в транскрипт."""
    msg = FakeMessage(text=t("menu_help", "ru"))
    with pytest.raises(SkipHandler):
        await iv.on_text_answer(msg, bot=None)  # type: ignore[arg-type]
    assert msg.answers == [], "ответа быть не должно: сообщение уходит дальше по цепочке"


async def test_text_without_active_interview_goes_further():
    """Свободный текст вне интервью не проглатывается: его ждёт fallback."""
    msg = FakeMessage(text="здравствуйте")
    with pytest.raises(SkipHandler):
        await iv.on_text_answer(msg, bot=None)  # type: ignore[arg-type]


async def test_every_menu_button_has_a_handler():
    """Кнопка без обработчика — это бот, который молчит на нажатие.

    Проверка механическая: подписи из клавиатуры сверяются с наборами, по
    которым фильтруют хендлеры. Добавить кнопку и забыть обработчик теперь
    нельзя — тест упадёт.
    """
    from app.bot import keyboards as kb

    handled = pf.MENU_PROFILE | pf.MENU_INVITATIONS | pf.MENU_HELP | pf.MENU_VACANCIES
    for locale in ("ru", "uz"):
        for row in kb.main_menu(locale).keyboard:
            for button in row:
                assert button.text in handled, f"нет обработчика для кнопки {button.text!r}"


async def test_contact_later_button_is_handled():
    from app.bot import keyboards as kb

    for locale in ("ru", "uz"):
        rows = kb.share_contact(locale).keyboard
        plain = [b.text for row in rows for b in row if not b.request_contact]
        assert plain and all(text in pf.CONTACT_LATER for text in plain)


# ─────────────────────────── карточка медика ───────────────────────────

async def test_card_text_shows_data_and_never_a_phone():
    """Карточка показывает то, что увидит клиника, и никогда — телефон.

    Рендер живёт в `profile_form`: там же собирается итог анкеты, и два похожих
    формата однажды разошлись бы. Названия ролей и районов берутся из словарей
    базы, поэтому функция асинхронная — в обмен карточка не может показать код
    вместо человеческого названия.
    """
    card = {
        "profile_status": "active",
        "role_category": "doctor",
        "specialty": "dentist_therapist",
        "experience_months": 60,
        "skills": ["Микроскоп"],
        "languages": ["Русский"],
        "districts": ["chilanzar"],
        "schedule": ["shift"],
        "salary_min_uzs": 5000000,
        "credential_claims": [],
        "has_contact": True,
        "in_pool": True,
        "self_filled": True,
    }
    text = await pform.card_text(card, "ru")
    assert "Врач" in text
    assert "Стоматолог-терапевт" in text
    assert "5 лет" in text
    assert "Микроскоп" in text
    assert "5 000 000" in text
    assert "Чиланзарский район" in text
    assert t("profile_contact_yes", "ru") in text
    # Самой цифры телефона в карточке нет: функция базы её и не отдаёт.
    assert "998" not in text


async def test_card_text_admits_missing_experience():
    """Прочерк лучше выдуманного нуля: «опыт 0» читается как «без опыта»."""
    text = await pform.card_text(
        {"profile_status": "draft", "experience_months": None, "has_contact": False}, "ru"
    )
    assert t("profile_experience_unset", "ru") in text
    assert t("profile_contact_no", "ru") in text


async def test_card_text_states_whether_person_is_in_the_pool():
    """Человек должен видеть, ищут его клиники или нет.

    Это не украшение: заполненная карточка вне поиска — самая обидная ситуация в
    продукте. Человек считает, что откликнулся на всё, а его просто не видно.
    """
    hidden = await pform.card_text(
        {"profile_status": "draft", "experience_months": 12,
         "has_contact": False, "in_pool": False}, "ru"
    )
    assert t("profile_pool_no", "ru") in hidden

    shown = await pform.card_text(
        {"profile_status": "active", "experience_months": 12,
         "has_contact": True, "in_pool": True}, "ru"
    )
    assert t("profile_pool_yes", "ru") in shown


async def test_experience_under_a_year_is_not_rounded_to_zero():
    """Для медсестры с полугодом опыта «0 лет» — потеря смысла."""
    text = await pform.card_text(
        {"profile_status": "active", "experience_months": 6, "has_contact": False}, "ru"
    )
    assert t("exp_less_year", "ru") in text


async def test_form_next_step_walks_empty_fields_in_order():
    """Состояние анкеты — сама строка в базе, а не FSM в памяти процесса.

    Поэтому «следующий шаг» это функция от данных. Бота можно перезапустить
    посреди анкеты, человек продолжит там же.
    """
    from app.services import candidate as cand

    assert cand.next_step(None) == "role_category"
    assert cand.next_step({"role_category": "nurse"}) == "specialty"
    assert cand.next_step({
        "role_category": "nurse", "specialty": "procedural_nurse",
        "experience_months": 48, "districts": [], "schedule": [],
    }) == "districts"
    assert cand.next_step({
        "role_category": "nurse", "specialty": "procedural_nurse",
        "experience_months": 48, "districts": ["chilanzar"], "schedule": ["shift"],
    }) is None


async def test_invitation_buttons_always_offer_refusal():
    """Приглашение — единственное сообщение, которое человек получает не по
    своей инициативе. Без кнопки «нет» это спам, а не приглашение."""
    for locale in ("ru", "uz"):
        rows = kb.invitation_decision(locale, "00000000-0000-0000-0000-000000000000").inline_keyboard
        actions = {b.callback_data.split(":")[1] for row in rows for b in row}
        assert actions == {"yes", "no"}


# ─────────────────────────── тексты ───────────────────────────

async def test_promise_about_resume_file_is_gone():
    """Приветствие обещало «пришлите резюме файлом», а кода для этого не было.

    Обещание убрано, а на присланный файл теперь есть честный ответ. Тест
    держит оба конца, чтобы обещание не вернулось само.
    """
    for locale in ("ru", "uz"):
        welcome = t("medic_welcome", locale).lower()
        assert "резюме" not in welcome
        assert "rezyume" not in welcome
        assert t("file_not_supported", locale) != f"[{'file_not_supported'}]"


async def test_unknown_message_is_used_and_translated():
    for locale in ("ru", "uz"):
        assert t("unknown_message", locale) not in ("", "[unknown_message]")
    assert labels("unknown_message"), "ключ должен существовать в обеих локалях"
