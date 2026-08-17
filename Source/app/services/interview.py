"""Движок авто-интервью.

Состояние интервью живёт в базе, а не в памяти бота и не в отдельном
чекпоинтере: `product.interviews` и `product.interview_turns` уже знают, какой
вопрос задан, на какой отвечено и сколько ходов осталось. Один источник правды
означает, что бота можно перезапустить посреди разговора — человек продолжит
с того же места.

Порядок вопросов задаёт одобренный менеджером план, и выбирает их база
(`next_interview_question`). Модель занята ровно двумя решениями:
  1. ответ по делу или стоит задать одно уточнение;
  2. итоговое саммари и структура для клиники.
Оба решения не критичны: если Azure недоступен, интервью идёт дальше по плану,
а саммари можно собрать позже. Разговор не должен обрываться из-за модели.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app import db
from app.config import settings
from app.i18n import DEFAULT_LOCALE
from app.services import jobs as ai_jobs
from app.services.llm import LLMError, chat_json, speak

log = logging.getLogger(__name__)

# Одно уточнение на вопрос плана. Больше — и разговор превращается в допрос,
# а модель начинает ходить по кругу вокруг одного и того же.
MAX_FOLLOW_UPS_PER_QUESTION = 1

# Короткие ответы вида «да» на открытый вопрос почти всегда означают, что
# человек не понял вопроса. Порог грубый и работает до обращения к модели.
SHORT_ANSWER_CHARS = 12

_JUDGE_PROMPT = """\
Ты помогаешь вести собеседование. Даны вопрос и ответ кандидата.
Верни JSON: {{"sufficient": true|false, "follow_up": "текст" или null}}

sufficient=false ставь только если ответ действительно не отвечает на вопрос:
пустой, не по теме, или в нём нет ни одного факта по существу вопроса.
Короткий, но содержательный ответ — это достаточный ответ.

follow_up — один короткий вопрос на «вы», до 200 символов, уточняющий именно
то, чего не хватило. Если sufficient=true, ставь null.
{language_rule}

Не оценивай кандидата и не давай советов. Только уточнение по существу.
"""

# Вопрос задан на языке клиники, а отвечает кандидат на своём. Уточнение —
# единственная реплика интервью, которую сочиняет модель, поэтому только её
# и можно выдать на языке кандидата, не трогая одобренный менеджером план.
_JUDGE_LANGUAGE = {
    "ru": "Пиши follow_up по-русски.",
    "uz": (
        "Пиши follow_up на узбекском языке латиницей (o‘zbekcha, lotin yozuvi), "
        "даже если вопрос и ответ на русском."
    ),
}


class NoActiveInterview(Exception):
    """У человека нет незакрытого интервью."""


# ── Витрина вакансий ──────────────────────────────────────────────────────────

async def published_jobs(*, limit: int = 10, offset: int = 0) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT * FROM product.list_published_jobs(NULL, NULL, %s, %s)", (limit, offset)
    )


async def job_by_code(code: str) -> dict[str, Any] | None:
    return await db.fetch_one("SELECT * FROM product.get_published_job(%s)", (code,))


async def job_by_id(job_id: str) -> dict[str, Any] | None:
    return await db.fetch_one("SELECT * FROM product.get_published_job_by_id(%s)", (job_id,))


async def my_applications(user_id: int) -> list[dict[str, Any]]:
    return await db.fetch_all("SELECT * FROM product.my_applications(%s)", (user_id,))


# ── Ход интервью ──────────────────────────────────────────────────────────────

async def open_for(user_id: int, job_id: str, message: str | None = None) -> dict[str, Any]:
    """Отклик и открытие интервью. Повторный вызов возвращает то же интервью."""
    row = await db.fetch_one(
        "SELECT * FROM product.open_interview(%s, %s, %s)", (user_id, job_id, message)
    )
    if row is None:
        raise NoActiveInterview("не удалось открыть интервью")
    return row


async def active(user_id: int) -> dict[str, Any] | None:
    return await db.fetch_one("SELECT * FROM product.my_active_interview(%s)", (user_id,))


async def next_question(interview_id: str) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM product.next_interview_question(%s)", (interview_id,)
    )


async def ask(
    interview_id: str,
    *,
    question_id: str | None,
    text: str,
    kind: str = "question",
) -> dict[str, Any]:
    row = await db.fetch_one(
        "SELECT * FROM product.ask_interview_turn(%s, %s, %s, %s)",
        (interview_id, question_id, text, kind),
    )
    if row is None:
        raise NoActiveInterview("интервью закрыто")
    return row


async def record_answer(
    interview_id: str,
    *,
    kind: str,
    text: str | None,
    voice_file_id: str | None = None,
    voice_seconds: int | None = None,
) -> None:
    await db.fetch_one(
        "SELECT * FROM product.record_interview_answer(%s, %s, %s, %s, %s)",
        (interview_id, kind, text, voice_file_id, voice_seconds),
    )


async def judge_answer(
    question: str, answer: str, *, locale: str = DEFAULT_LOCALE
) -> tuple[bool, str | None]:
    """Достаточен ли ответ и какое уточнение задать.

    `locale` — язык кандидата, на нём будет задано уточнение.

    При любой неудаче считаем ответ достаточным: пропустить уточнение не
    страшно, а застрять на вопросе из-за недоступной модели — страшно.
    """
    clean = (answer or "").strip()
    if not clean:
        return False, None
    if len(clean) >= 200:
        # Развёрнутый ответ уточнять незачем, и это экономит вызов модели.
        return True, None

    prompt = _JUDGE_PROMPT.format(
        language_rule=_JUDGE_LANGUAGE.get(locale, _JUDGE_LANGUAGE[DEFAULT_LOCALE])
    )
    try:
        data = await chat_json(
            [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"вопрос": question, "ответ": clean}, ensure_ascii=False
                    ),
                },
            ],
            # Явно диалоговая модель. deployment=None подставлял бы
            # extract_deployment, то есть gpt-5: журнал ai.model_calls показал
            # 156 вызовов оценки на старшей модели вместо mini. Задача тут
            # простая — «ответил человек по делу или нет».
            deployment=settings().dialog_deployment,
            max_tokens=600,
            purpose="interview_judge",
        )
    except LLMError as e:
        log.warning("оценка ответа не удалась, идём дальше по плану: %s", e)
        return True, None

    if not isinstance(data, dict):
        return True, None
    sufficient = bool(data.get("sufficient", True))
    follow_up = (data.get("follow_up") or "").strip() or None
    if sufficient or not follow_up:
        return True, None
    return False, follow_up[:400]


async def follow_ups_used(interview_id: str) -> int:
    row = await db.fetch_one(
        "SELECT product.follow_ups_after_last_question(%s) AS n", (interview_id,)
    )
    return int((row or {}).get("n") or 0)


async def voice_for(text: str) -> bytes | None:
    """Озвучка вопроса. None означает «продолжаем текстом»."""
    return await speak(text)


async def remember_question_voice(question_id: str, user_id: int, file_id: str) -> None:
    """Запоминает file_id озвученного вопроса, чтобы больше его не синтезировать.

    Квота tts — три запроса в минуту на всю подписку, поднять нельзя. Вопросы
    плана при этом фиксированы, и запись у них одна для всех кандидатов: первый
    нажавший «послушать» оплачивает синтез, остальные получают то же аудио
    мгновенно и без обращения к Azure.
    """
    try:
        await db.execute(
            "SELECT product.save_question_voice(%s, %s, %s)",
            (question_id, user_id, file_id),
        )
    except Exception as e:  # кэш не обязателен, разговор важнее
        log.warning("не удалось запомнить озвучку вопроса: %s", e)


async def finish(interview_id: str, user_id: int) -> dict[str, Any]:
    """Закрывает интервью и собирает итог для клиники.

    Если модель не ответила, интервью всё равно закрывается: клиника увидит
    транскрипт, а саммари можно будет пересобрать. Оставить разговор в
    состоянии «идёт» хуже — кандидат не поймёт, закончил он или нет.
    """
    turns = await db.fetch_all(
        "SELECT * FROM product.candidate_transcript(%s, %s)", (interview_id, user_id)
    )
    job = await db.fetch_one("SELECT * FROM product.job_of_interview(%s)", (interview_id,))

    result: dict[str, Any] = {"summary": None, "extraction": {}, "gaps": [], "follow_ups": []}
    answered = [t for t in turns if t.get("answer_text")]
    if job is not None and answered:
        try:
            result = await ai_jobs.summarize_interview(job, answered)
        except LLMError as e:
            log.warning("саммари интервью не собрано: %s", e)

    await db.execute(
        "SELECT product.complete_interview(%s, %s, %s::jsonb, %s, %s)",
        (
            interview_id,
            result["summary"],
            json.dumps(result["extraction"], ensure_ascii=False),
            result["gaps"],
            result["follow_ups"],
        ),
    )
    # Разобранное переносим в профиль сразу здесь, а не отдельной задачей.
    # Иначе получается то, что и получалось: extraction лежит в интервью,
    # профиль пустой, менеджер видит пять прочерков. Отдельный try — потому что
    # интервью уже закрыто, и падать на пост-обработке нельзя.
    try:
        await db.execute(
            "SELECT product.apply_interview_extraction(%s)", (interview_id,)
        )
    except Exception as e:
        log.warning("не удалось перенести extraction в профиль: %s", e)

    log.info(
        "интервью %s закрыто: ответов %s, саммари %s",
        interview_id, len(answered), "есть" if result["summary"] else "нет",
    )
    return {**result, "answered": len(answered), "total": len(turns)}


async def abandon(interview_id: str) -> None:
    await db.execute("SELECT product.abandon_interview(%s)", (interview_id,))
