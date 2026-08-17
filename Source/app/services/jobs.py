"""Вакансии клиники: разбор текста, план интервью, публикация.

Смысл публикации — авто-интервью. Поэтому вакансия проходит три состояния:
черновик, план вопросов одобрен, опубликована. Публиковать без одобренного
плана база не даёт (`product.publish_job`), и это правильно: кандидат
откликнулся бы, начал интервью, а спрашивать было бы нечего.

Модель здесь только помогает: разбирает свободный текст на поля и предлагает
вопросы. Последнее слово за менеджером — он правит и одобряет.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from decimal import Decimal
from typing import Any

from app.services.llm import LLMError, chat_json

log = logging.getLogger(__name__)


def _jsonable(value: Any) -> Any:
    """Приводит типы из Postgres к тому, что переживёт json.dumps.

    numeric приходит как Decimal, а зарплата у нас numeric(12,2): без этого
    сборка промпта падает на попытке сериализовать сумму.
    """
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    raise TypeError(f"нечего делать с типом {type(value).__name__}")


def _dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=_jsonable)

# Меньше пяти — интервью не даёт картины, больше семи — кандидат бросает
# на середине. Число выбрано под голосовые ответы: семь вопросов это уже
# около пяти минут разговора.
QUESTIONS_MIN = 5
QUESTIONS_MAX = 7

_EXTRACT_PROMPT = """\
Ты помогаешь клинике в Узбекистане оформить вакансию. На входе — свободный
текст, который написал или надиктовал менеджер. Верни JSON без пояснений.

Поля:
  title            — краткое название должности, как её называют в клинике
  role_category    — одно из: {roles}
  specialty        — один код из справочника или null: {specialties}
  experience_min_months — целое число месяцев или null
  required_skills  — массив коротких строк на русском, до 8 штук
  required_languages — массив из: uz, ru, en
  city             — латиницей, по умолчанию tashkent
  districts        — массив районов латиницей или []
  schedule         — массив из: full_time, part_time, shift, night, weekend
  salary_min_uzs   — целое число или null
  salary_max_uzs   — целое число или null
  credential_requirements — массив требований к документам или []

Правила:
  * не придумывай того, чего в тексте нет — ставь null или пустой массив;
  * зарплату переводи в сумы числом, без пробелов и слова «сум»;
  * если указан диапазон опыта, бери нижнюю границу;
  * specialty выбирай только из справочника, иначе null.
"""

_QUESTIONS_PROMPT = """\
Ты составляешь план собеседования для клиники в Узбекистане. Вопросы задаст
бот в Telegram, отвечать кандидат будет текстом или голосом.

Верни JSON: массив из {q_min}–{q_max} объектов с полями question и intent.
intent — одно слово из: опыт, навыки, график, оплата, документы, мотивация.

Правила, важные для нас:
  * вопросы по существу вакансии, а не общие «расскажите о себе»;
  * по одному вопросу на одну тему, без «и» внутри вопроса — иначе кандидат
    ответит на половину;
  * первый вопрос простой, чтобы человек начал говорить;
  * обязательно спроси про опыт, график и ожидания по оплате;
  * НЕ спрашивай о здоровье, семье, возрасте, гражданстве, религии и
    беременности — это недопустимо и нам не нужно;
  * формулируй на «вы», коротко, до 200 символов;
  * язык — русский.
"""

_SUMMARY_PROMPT = """\
Ты готовишь для клиники итог собеседования. На входе — вакансия и запись
разговора с кандидатом. Верни JSON без пояснений.
{language_rule}

Поля:
  summary     — 3–5 предложений: что кандидат рассказал о себе по существу
                вакансии. Только факты из разговора.
  extraction  — объект с тем, что удалось установить: experience_months
                (целое или null), skills (массив), schedule (массив),
                salary_expectation_uzs (целое или null), languages (массив)
  gaps        — массив, не больше 4 строк: о чём СПРАШИВАЛИ, но ответа не
                получили. Только это.
  follow_ups  — массив, не больше 4 строк: что стоит уточнить лично

Правила:
  * НЕ делай вывода «подходит» или «не подходит» и не ставь оценок:
    решение принимает человек, ты только структурируешь сказанное;
  * не додумывай за кандидата — если он не назвал цифру, ставь null;
  * в gaps попадает только то, о чём был задан вопрос и ответа не последовало.
    НЕ придумывай дополнительных требований к кандидату и не перечисляй
    подробности, которых вакансия не спрашивала: «не указал модель
    микроскопа» — это не пробел, а придирка. Если кандидат ответил на все
    вопросы, gaps должен быть пустым;
  * follow_ups — про то, что решается в разговоре с человеком: условия,
    расписание, проверка документов. Не дублируй gaps.
"""

# Итог читает клиника, поэтому язык здесь — язык кабинета, а не кандидата.
# Кандидат мог отвечать на узбекском русскоязычному менеджеру и наоборот:
# пересказ всё равно должен лечь на язык того, кто принимает решение.
_SUMMARY_LANGUAGE = {
    "ru": "Пиши по-русски, независимо от языка ответов кандидата.",
    "uz": (
        "Пиши на узбекском языке латиницей (o‘zbekcha, lotin yozuvi), "
        "независимо от языка ответов кандидата."
    ),
}


async def extract_fields(
    source_text: str, *, roles: list[str], specialties: list[dict[str, str]]
) -> dict[str, Any]:
    """Разбирает свободный текст вакансии на поля.

    Справочники передаём в промпт: specialty и role_category — внешние ключи,
    и придуманное моделью значение база просто не примет.
    """
    spec_list = ", ".join(f"{s['code']} ({s['name_ru']})" for s in specialties)
    prompt = _EXTRACT_PROMPT.format(roles=", ".join(roles), specialties=spec_list)

    data = await chat_json(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": source_text[:4000]},
        ],
        purpose="job_extract",
    )
    if not isinstance(data, dict):
        raise LLMError("ожидался объект с полями вакансии")

    valid_specialties = {s["code"] for s in specialties}
    if data.get("specialty") not in valid_specialties:
        data["specialty"] = None
    if data.get("role_category") not in set(roles):
        # Категорию не угадали — пусть менеджер выберет сам, чем сохранить
        # неверную и сломать матчинг.
        data["role_category"] = None
    return data


async def suggest_questions(job: dict[str, Any]) -> list[dict[str, str]]:
    """Предлагает план вопросов по вакансии. Менеджер потом правит и одобряет."""
    described = _dumps(
        {
            "title": job.get("title"),
            "specialty": job.get("specialty"),
            "experience_min_months": job.get("experience_min_months"),
            "required_skills": job.get("required_skills"),
            "required_languages": job.get("required_languages"),
            "schedule": job.get("schedule"),
            "salary_min_uzs": job.get("salary_min_uzs"),
            "salary_max_uzs": job.get("salary_max_uzs"),
            "credential_requirements": job.get("credential_requirements"),
            "описание": (job.get("source_text") or "")[:2000],
        }
    )

    data = await chat_json(
        [
            {
                "role": "system",
                "content": _QUESTIONS_PROMPT.format(q_min=QUESTIONS_MIN, q_max=QUESTIONS_MAX),
            },
            {"role": "user", "content": described},
        ],
        purpose="interview_plan",
    )

    items = data.get("questions") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        raise LLMError("модель не вернула список вопросов")

    out: list[dict[str, str]] = []
    for item in items[:QUESTIONS_MAX]:
        if isinstance(item, str):
            question, intent = item, None
        elif isinstance(item, dict):
            question, intent = item.get("question"), item.get("intent")
        else:
            continue
        text = (question or "").strip()
        # Ограничение в базе — от 5 до 400 символов. Отсекаем здесь, чтобы
        # менеджер увидел пригодный список, а не ошибку вставки.
        if 5 <= len(text) <= 400:
            out.append({"question": text[:400], "intent": (intent or "").strip()[:40] or None})

    if len(out) < 3:
        raise LLMError("модель вернула слишком мало пригодных вопросов")
    return out


async def summarize_interview(
    job: dict[str, Any], turns: list[dict[str, Any]]
) -> dict[str, Any]:
    """Собирает саммари и структуру по расшифрованному разговору.

    Язык итога берём из `job["clinic_locale"]` — это язык менеджера, который
    завёл вакансию (миграция 033). Заметьте: решения здесь нет. Модель
    пересказывает и раскладывает по полям, а «подходит или нет» считают
    правила и решает человек.
    """
    locale = str(job.get("clinic_locale") or "ru")
    prompt = _SUMMARY_PROMPT.format(
        language_rule=_SUMMARY_LANGUAGE.get(locale, _SUMMARY_LANGUAGE["ru"])
    )
    transcript = "\n".join(
        f"Вопрос: {t['question_text']}\nОтвет: {t.get('answer_text') or '(нет ответа)'}"
        for t in turns
    )
    payload = _dumps(
        {
            "вакансия": {
                "title": job.get("title"),
                "specialty": job.get("specialty"),
                "experience_min_months": job.get("experience_min_months"),
                "required_skills": job.get("required_skills"),
            },
            "разговор": transcript[:6000],
        }
    )

    # Одна повторная попытка. Сборка итога — единственный вызов в конце
    # разговора, и промах здесь означает, что клиника увидит транскрипт без
    # саммари. Наблюдал перемежающийся пустой ответ на упоре в лимит токенов,
    # поэтому лимит поднят и добавлен повтор: восемь секунд дешевле, чем
    # объяснять на демо, почему у одного кандидата итога нет.
    data = None
    last: LLMError | None = None
    for attempt in (1, 2):
        try:
            data = await chat_json(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": payload},
                ],
                max_tokens=3000,
                purpose="interview_summary",
            )
            break
        except LLMError as e:
            last = e
            log.warning("попытка %s собрать итог интервью не удалась: %s", attempt, e)
    if data is None:
        raise last or LLMError("итог интервью не собран")
    if not isinstance(data, dict):
        raise LLMError("ожидался объект с итогом интервью")

    return {
        "summary": (data.get("summary") or "").strip()[:2000],
        "extraction": data.get("extraction") if isinstance(data.get("extraction"), dict) else {},
        "gaps": [str(x)[:200] for x in (data.get("gaps") or []) if x][:8],
        "follow_ups": [str(x)[:200] for x in (data.get("follow_ups") or []) if x][:8],
    }
