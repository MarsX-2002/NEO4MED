#!/usr/bin/env python
"""Сквозной прогон авто-интервью без Telegram.

Зачем отдельный скрипт, а не тест: здесь настоящие обращения к Azure —
оценка ответов и сборка саммари. В pytest такое место делает набор медленным
и капризным, а нам нужно проверять именно живой путь.

Прогон идёт тем же кодом, что бот: `app.services.interview` под прикладной
ролью. Не воспроизводится только транспорт Telegram.

Требование «демо должно пройти три раза подряд» проверяется буквально:
скрипт по умолчанию гоняет сценарий трижды разными кандидатами и печатает
итог по каждому. Любое падение — это красный прогон.

    ./.venv/bin/python tools/demo_interview.py --runs 3
    ./.venv/bin/python tools/demo_interview.py --job-code 7ffe8xxga5 --keep
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

import psycopg
from psycopg.rows import dict_row

from app import db
from app.config import settings
from app.services import interview as svc

log = logging.getLogger("demo")

# Тестовые кандидаты получают отрицательные telegram_id: живые всегда
# положительные, столкнуться с настоящим человеком нельзя.
TG_BASE = -800_100

# Ответы подобраны так, чтобы проверить разные ветки: развёрнутый ответ,
# короткий но содержательный, и намеренно расплывчатый — на нём должно
# сработать уточнение.
# Ответ выбирается по номеру вопроса плана, а не по порядку выдачи: если
# модель вставит уточнение, ответы не должны съехать на один. На уточнение
# идёт отдельная реплика — так ведёт себя человек, которого переспросили.
CANDIDATES = [
    {
        "name": "Азиза Каримова",
        "follow_up_reply": "Уточняю: восемь лет стажа, из них три года с микроскопом.",
        "answers": [
            "Работаю стоматологом-терапевтом восемь лет, с 2018 года в частной клинике.",
            "Да, три года лечу каналы под микроскопом, прошла курс по эндодонтии в Ташкенте.",
            "Смены удобны, по субботам выходить готова, кроме первой субботы месяца.",
            "Рассчитываю на двенадцать миллионов сум.",
            "Да, сертификат действует до 2028 года.",
            "С пациентами говорю свободно и на узбекском, и на русском.",
        ],
    },
    {
        "name": "Дилшод Рахимов",
        "follow_up_reply": "Если конкретнее — пять лет в терапии, сертификат до 2027 года.",
        "answers": [
            "Пять лет.",
            "Под микроскопом не работал, но каналы лечу давно, обычными инструментами.",
            "Готов на смены.",
            "От десяти миллионов.",
            "Сертификат есть.",
            "Узбекский родной, русский хорошо.",
        ],
    },
    {
        "name": "Нилуфар Юсупова",
        "follow_up_reply": "Точнее сказать не готова.",
        "answers": [
            # Намеренно расплывчато: ждём уточняющий вопрос от модели.
            "Ну как-то работаю, нормально всё.",
            "Микроскоп видела, интересно было бы попробовать.",
            "Смены нормально, суббота тоже.",
            "Сколько предложите, договоримся.",
            "Да, есть действующий.",
            "Оба языка знаю хорошо.",
        ],
    },
]


async def _admin(sql: str, params: tuple | None = None) -> dict | None:
    admin = settings().admin_database_url
    assert admin is not None, "нужен DATABASE_URL владельца"
    async with await psycopg.AsyncConnection.connect(
        admin.get_secret_value(), autocommit=True, row_factory=dict_row
    ) as conn:
        cur = await conn.execute(sql, params)
        if cur.description is None:
            return None
        return await cur.fetchone()


async def _pick_job(code: str | None) -> dict:
    if code:
        job = await svc.job_by_code(code)
        if job is None:
            raise SystemExit(f"вакансия с кодом {code} не опубликована")
        return job
    jobs = await svc.published_jobs(limit=1)
    if not jobs:
        raise SystemExit(
            "нет опубликованных вакансий. Создайте вакансию в кабинете, "
            "одобрите план вопросов и опубликуйте."
        )
    full = await svc.job_by_id(str(jobs[0]["job_id"]))
    assert full is not None
    return full


async def run_once(job: dict, candidate: dict, tg_id: int) -> dict:
    """Один полный проход интервью. Возвращает замеры."""
    # Функции интервью принимают product.users.id, а не telegram_user_id.
    row = await _admin(
        """
        INSERT INTO product.users (role, telegram_user_id, full_name, locale,
                                   consent_at, consent_version)
        VALUES ('medic', %s, %s, 'ru', now(), 'demo')
        ON CONFLICT (telegram_user_id) DO UPDATE SET full_name = excluded.full_name
        RETURNING id
        """,
        (tg_id, candidate["name"]),
    )
    assert row is not None
    uid = int(row["id"])

    started = time.monotonic()
    opened = await svc.open_for(uid, str(job["job_id"]))
    interview_id = str(opened["interview_id"])
    if not opened["is_new"]:
        raise SystemExit(
            f"интервью для {candidate['name']} уже существует — прогон не чистый. "
            "Запустите без --keep или смените кандидата."
        )

    asked = answered = follow_ups = 0
    answers = candidate["answers"]
    # Номер вопроса плана, на который сейчас отвечаем. Уточнение номер не меняет.
    plan_ord = 0
    answering_follow_up = False

    while True:
        state = await svc.active(uid)
        if state is None:
            break

        pending = state.get("pending_question")
        if pending is None:
            nxt = await svc.next_question(interview_id)
            if nxt is None:
                break
            await svc.ask(
                interview_id, question_id=str(nxt["question_id"]), text=nxt["question"]
            )
            asked += 1
            plan_ord = int(nxt["ord"])
            answering_follow_up = False
            print(f"    ? {nxt['question']}")
            continue

        if answering_follow_up:
            answer = candidate["follow_up_reply"]
        else:
            answer = answers[plan_ord - 1] if 1 <= plan_ord <= len(answers) else "Не готов ответить."
        await svc.record_answer(interview_id, kind="text", text=answer)
        answered += 1
        print(f"    > {answer}")

        used = await svc.follow_ups_used(interview_id)
        if used < svc.MAX_FOLLOW_UPS_PER_QUESTION:
            sufficient, follow_up = await svc.judge_answer(pending, answer)
            if not sufficient and follow_up:
                await svc.ask(
                    interview_id, question_id=None, text=follow_up, kind="follow_up"
                )
                asked += 1
                follow_ups += 1
                answering_follow_up = True
                print(f"    ?? уточнение: {follow_up}")
                continue
        answering_follow_up = False

    result = await svc.finish(interview_id, uid)
    elapsed = time.monotonic() - started

    return {
        "candidate": candidate["name"],
        "interview_id": interview_id,
        "asked": asked,
        "answered": answered,
        "follow_ups": follow_ups,
        "summary": result.get("summary"),
        "gaps": result.get("gaps") or [],
        "next_steps": result.get("follow_ups") or [],
        "seconds": elapsed,
    }


async def _cleanup() -> None:
    await _admin(
        "DELETE FROM product.users WHERE telegram_user_id <= %s AND telegram_user_id > %s",
        (TG_BASE, TG_BASE - 1000),
    )


async def main() -> int:
    ap = argparse.ArgumentParser(description="Сквозной прогон авто-интервью")
    ap.add_argument("--runs", type=int, default=3, help="сколько раз подряд (по умолчанию 3)")
    ap.add_argument("--job-code", default=None, help="код вакансии, иначе первая опубликованная")
    ap.add_argument("--keep", action="store_true", help="не удалять тестовых кандидатов")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="  %(levelname)s %(name)s: %(message)s")

    await _cleanup()
    job = await _pick_job(args.job_code)
    print(f"\nВакансия: {job['title']} — {job['clinic_name']}")
    print(f"Вопросов в плане: {job['questions_count']}, код {job['public_code']}\n")

    results = []
    failures = 0
    for i in range(args.runs):
        candidate = CANDIDATES[i % len(CANDIDATES)]
        print(f"  ── Прогон {i + 1} из {args.runs}: {candidate['name']} ──")
        try:
            results.append(await run_once(job, candidate, TG_BASE - i))
        except Exception as e:  # прогон обязан продолжиться: важно, сколько упало
            failures += 1
            print(f"    ПРОГОН УПАЛ: {type(e).__name__}: {e}")
        print()

    print("═" * 70)
    for r in results:
        print(f"\n{r['candidate']}  ({r['seconds']:.1f} c)")
        print(f"  задано {r['asked']}, отвечено {r['answered']}, уточнений {r['follow_ups']}")
        print(f"  саммари: {r['summary'] or 'НЕ СОБРАНО'}")
        if r["gaps"]:
            print("  пробелы: " + "; ".join(r["gaps"]))
        if r["next_steps"]:
            print("  спросить лично: " + "; ".join(r["next_steps"]))

    print("\n" + "═" * 70)
    ok = len(results)
    print(f"Успешных прогонов: {ok} из {args.runs}, упало: {failures}")
    if results:
        print(f"Среднее время интервью: {sum(r['seconds'] for r in results) / ok:.1f} c")
        no_summary = [r["candidate"] for r in results if not r["summary"]]
        if no_summary:
            print(f"Без саммари: {', '.join(no_summary)}")

    if not args.keep:
        await _cleanup()
        print("Тестовые кандидаты убраны.")

    await db.close_pool()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
