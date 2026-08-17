#!/usr/bin/env python
"""Тестовый курс обучения и прохождения сотрудниками.

Что создаёт в демо-клинике:
  * один опубликованный курс с материалом из четырёх уроков и тестом из шести
    вопросов. Каждый вопрос отвечается по материалу — курс внутренний, и
    источник правды здесь именно урок, а не общая эрудиция;
  * назначение всем работающим сотрудникам;
  * прохождения: сдано, сдано со второй попытки, провалено, начато и не
    закончено, не начато. Одинаковые «все прошли» ничего не показывают —
    раздел нужен ровно затем, чтобы найти того, кто не прошёл;
  * назначение тестового сотрудника (у которого есть вход в портал) остаётся
    НЕ начатым: на демо тест проходят живьём.

Почему попытки вставляются напрямую, а не через product.grade_attempt.
Функция после миграции 037 требует, чтобы сдающий был сам сотрудник:
`employees.user_id = current_user_id()`. У девяти демо-сотрудников учётной
записи нет и быть не должно — вход в портал выдаётся не всем. Поэтому балл
здесь считается тем же правилом (совпадение ответа с верным вариантом), но
своим запросом, под владельцем базы. Это демо-данные; любая настоящая
пересдача пересчитает их той самой функцией.

    ./.venv/bin/python db/seed_course.py [--reset]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

COURSE_TITLE = "Инфекционная безопасность и обработка инструментов"
COURSE_SUMMARY = (
    "Как мы моем руки, обрабатываем инструменты и что делаем при укол-порезе. "
    "Читается за 10 минут, тест из шести вопросов, порог 80%."
)
PASS_SCORE = 80

# Сотрудник с входом в портал: его назначение оставляем не начатым, чтобы тест
# можно было пройти живьём. Имя то же, что в db/seed_employee_portal.py.
PORTAL_EMPLOYEE = "Ахмедова Азиза Рустамовна"

# ── Материал ──────────────────────────────────────────────────────────────────
# Тексты намеренно длинные: раздел называется «Курсы», и на демо должно быть
# видно, что человек действительно читает, а не смотрит на заголовок.
LESSONS: list[tuple[str, str]] = [
    (
        "Зачем этот курс и что он меняет в вашей смене",
        """Инфекционная безопасность — это не журнал для проверяющих, а способ
не унести домой то, с чем вы работали, и не передать одному пациенту то, что
принёс другой. В стоматологии контакт с кровью и слюной есть почти на каждом
приёме, а аэрозоль от наконечника разлетается по кабинету на полтора-два метра.

Курс собран из нашего внутреннего регламента. Все цифры и сроки в тесте — наши,
а не «как принято вообще»: если в другой клинике вам говорили другое, здесь
правильным считается то, что написано в этих уроках.

Что вы будете уметь после курса:
— мыть и обрабатывать руки в те пять моментов, когда это действительно важно;
— провести инструмент по всем этапам от кабинета до стерильного хранения;
— разложить отходы так, чтобы никто не порезался после вас;
— действовать в первые минуты после укола иглой, а не вспоминать, к кому идти.

Порог сдачи — 80%: из шести вопросов можно ошибиться в одном. Пересдавать можно,
попытки не ограничены, но каждая видна руководителю — не как наказание, а чтобы
понять, какой урок написан плохо, если на нём спотыкаются все.""",
    ),
    (
        "Гигиена рук: пять моментов",
        """Всемирная организация здравоохранения описывает пять моментов гигиены
рук. Их пять, а не «всегда» — потому что «всегда» на практике превращается
в «когда вспомнил».

1. Перед контактом с пациентом.
2. Перед чистой или инвазивной процедурой.
3. После контакта с биологическими жидкостями — сразу, не дожидаясь конца приёма.
4. После контакта с пациентом.
5. После контакта с окружением пациента: кресло, светильник, столик, монитор.

Чем обрабатывать. Руки без видимого загрязнения — спиртовым антисептиком,
30 секунд, до полного высыхания, не вытирая. Руки с видимым загрязнением —
сначала вода с жидким мылом 40–60 секунд, затем антисептик.

Перчатки не заменяют обработку рук. Под перчаткой тепло и влажно, микрофлора
там размножается быстрее, а микропроколы в латексе есть у каждой третьей
перчатки к концу приёма. Поэтому антисептик — и до перчаток, и после их снятия.

Что мешает: длинные и наращённые ногти, гель-лак, кольца и браслеты. Под ними
остаётся то, что не смывается. На приёме их не носим.""",
    ),
    (
        "Обработка инструментов: пять этапов по порядку",
        """Порядок этапов важнее старательности на каждом из них: пропущенный
шаг нельзя компенсировать более долгим следующим.

Этап 1. Дезинфекция. Инструмент погружается в раствор сразу после приёма,
полностью, в разобранном виде, в закрытой ёмкости. Не «складываем в лоток
до конца смены»: подсохшая кровь и слюна снимаются потом только механически.

Этап 2. Предстерилизационная очистка. Ультразвуковая мойка или ручная щётка,
затем промывание и сушка. Задача — убрать белковые загрязнения. Стерилизация
не работает через слой органики: пар просто не доходит до металла.

Этап 3. Контроль качества очистки. Азопирамовая проба выборочно: 1% от партии,
но не меньше трёх единиц. Проба положительная — вся партия возвращается на
второй этап.

Этап 4. Упаковка и стерилизация. Крафт-пакет запечатывается, на нём пишется
дата и смена. Автоклав: 134 °C, 2,0 атм, 5 минут для металла; 121 °C, 1,1 атм,
20 минут для того, что не выдерживает высокой температуры. В каждую загрузку
кладётся химический индикатор, снаружи — индикаторная лента.

Этап 5. Хранение. По нашему регламенту простерилизованный инструмент
в запечатанном крафт-пакете годен 3 суток. Пакет вскрыт, надорван или намок —
инструмент считается нестерильным независимо от даты. Срок вышел — партия
возвращается на упаковку и стерилизацию, а не «дорабатывает до конца дня».""",
    ),
    (
        "Отходы и аварийная ситуация",
        """Отходы. Всё, что контактировало с кровью или слюной, — класс Б,
жёлтый пакет с маркировкой. Иглы, карпулы, скальпели, боры и матрицы — только
непрокалываемый контейнер, и только он. Игла в пакете — это порез санитарки,
который случится через час после того, как вы ушли домой.

Иглу не сгибаем, не ломаем и не надеваем колпачок обратно двумя руками:
большинство уколов происходит именно в этот момент. Контейнер заполняется
не более чем на три четверти и закрывается окончательно.

Авария: укол, порез, попадание крови на слизистую. Порядок первых минут:
1. Снять перчатки, не втирая кровь и не выдавливая её из ранки специально.
2. Промыть место водой с мылом.
3. Обработать: кожу — 70% спиртом, затем 5% йодом; глаза — водой; слизистую
   носа и рта — водой, затем 70% спиртом.
4. В ТОТ ЖЕ ДЕНЬ сообщить руководителю и внести запись в журнал аварийных
   ситуаций.
5. Обследование и, если нужно, постконтактная профилактика — в первые 2 часа,
   не позже 72 часов.

Ключевой пункт — четвёртый. Незарегистрированная авария лишает вас и защиты,
и профилактики: доказать связь заражения с работой потом невозможно.
Скрытый укол — единственная ошибка в этом курсе, которую нельзя исправить
задним числом. За саму аварию у нас не наказывают, за молчание — да.""",
    ),
]

# ── Тест ──────────────────────────────────────────────────────────────────────
# Формат: (вопрос, пояснение, [(вариант, верный?)]).
QUESTIONS: list[tuple[str, str, list[tuple[str, bool]]]] = [
    (
        "Сколько моментов гигиены рук описывает ВОЗ и нужна ли обработка после снятия перчаток?",
        "Пять моментов. Перчатки не заменяют антисептик: под ними тепло и влажно, "
        "а микропроколы к концу приёма есть у каждой третьей перчатки.",
        [
            ("Пять моментов, обработка нужна и до перчаток, и после их снятия", True),
            ("Пять моментов, но после перчаток обработка не требуется", False),
            ("Три момента, обработка только перед приёмом", False),
            ("Отдельных моментов нет: достаточно мыть руки в начале и конце смены", False),
        ],
    ),
    (
        "Инструмент только что использован на приёме. Что с ним делают в первую очередь?",
        "Первый этап — дезинфекция погружением сразу после приёма. Подсохшую "
        "органику потом снимают только механически, и это удлиняет всю цепочку.",
        [
            ("Полностью погружают в дезинфицирующий раствор в разобранном виде", True),
            ("Сразу отправляют в автоклав, дезинфекция после стерилизации", False),
            ("Складывают в лоток и обрабатывают всё вместе в конце смены", False),
            ("Протирают салфеткой и убирают в крафт-пакет", False),
        ],
    ),
    (
        "Почему стерилизация не спасает, если пропустить предстерилизационную очистку?",
        "Пар не проходит через слой белковых загрязнений. Пропущенный этап нельзя "
        "компенсировать более долгим следующим.",
        [
            ("Пар не проникает через слой органики и не доходит до металла", True),
            ("Очистка нужна только для внешнего вида инструмента", False),
            ("Без очистки инструмент быстрее тупится, на стерильность это не влияет", False),
            ("Влияет только на срок хранения после стерилизации", False),
        ],
    ),
    (
        "Сколько по нашему регламенту годен инструмент в запечатанном крафт-пакете?",
        "Трое суток. И отдельно: надорванный или намокший пакет делает инструмент "
        "нестерильным независимо от даты на нём.",
        [
            ("3 суток, а вскрытый или намокший пакет — сразу нестерилен", True),
            ("3 суток, дата важнее целостности упаковки", False),
            ("20 суток при закрытом шкафе", False),
            ("До конца рабочего дня, потом обязательно повторно", False),
        ],
    ),
    (
        "Куда сбрасывают использованную иглу от карпульного шприца?",
        "Только в непрокалываемый контейнер, заполненный не более чем на три "
        "четверти. Игла в мягком пакете — это порез того, кто выносит отходы.",
        [
            ("В непрокалываемый контейнер для острого, отходы класса Б", True),
            ("В жёлтый пакет класса Б вместе с перчатками и салфетками", False),
            ("В общий мусор после дезинфекции колпачка", False),
            ("В контейнер для стекла, предварительно сняв иглу с карпулы", False),
        ],
    ),
    (
        "Вы укололись иглой после приёма. Что обязательно сделать в тот же день?",
        "Промыть, обработать — и сообщить руководителю с записью в журнале "
        "аварийных ситуаций. Незарегистрированная авария лишает и профилактики, "
        "и защиты: связь с работой потом не доказать.",
        [
            ("Обработать ранку и сообщить руководителю с записью в журнал аварийных ситуаций", True),
            ("Обработать ранку и продолжить работу: при малой ранке журнал не нужен", False),
            ("Выдавить кровь, залить йодом и сообщить, если появятся симптомы", False),
            ("Дождаться результатов анализов и только потом сообщать", False),
        ],
    ),
]

# Сценарий прохождения: сколько верных ответов даёт сотрудник в каждой попытке.
# Порядок соответствует порядку сотрудников после исключения портального.
#   None — назначено и не начато;
#   []   — начал и не закончил (попытка открыта, ответов нет);
#   [3, 5] — провалил и пересдал.
SCENARIOS: list[list[int] | None] = [
    [6],        # сдал с первого раза, 100%
    [5],        # сдал, 83% — одна ошибка допустима при пороге 80
    [3, 5],     # провалил и пересдал
    [6],
    [4],        # 67% — провалил, пересдачи пока не было
    [],         # начал и бросил на середине
    None,       # не начал
    [2],        # 33% — курс явно не читал
    None,
]


def clinic_id(cur) -> str:
    cur.execute("SELECT id FROM product.clinics WHERE is_demo ORDER BY created_at LIMIT 1")
    row = cur.fetchone()
    if row is None:
        raise SystemExit("демо-клиники нет — сначала ./.venv/bin/python db/seed_demo.py")
    return row[0]


def reset(cur, cid: str) -> None:
    """Сносит только обучение и только в демо-клинике.

    Каскад от courses уносит уроки, вопросы, варианты, назначения и попытки.
    """
    cur.execute("DELETE FROM product.courses WHERE clinic_id = %s", (cid,))
    print("  курсы, назначения и попытки демо-клиники удалены")


def manager_id(cur, cid: str) -> int | None:
    """Кто «создал» курс. Без него created_by останется пустым, и в кабинете
    будет непонятно, чей это материал."""
    cur.execute(
        """
        SELECT cm.user_id FROM product.clinic_members cm
         WHERE cm.clinic_id = %s AND cm.role = 'owner'
         ORDER BY cm.created_at LIMIT 1
        """,
        (cid,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def build_course(cur, cid: str, author: int | None) -> str:
    cur.execute(
        """
        INSERT INTO product.courses
            (clinic_id, title, summary, pass_score, status, created_by)
        VALUES (%s, %s, %s, %s, 'published', %s)
        RETURNING id
        """,
        (cid, COURSE_TITLE, COURSE_SUMMARY, PASS_SCORE, author),
    )
    course_id = cur.fetchone()[0]

    for i, (title, content) in enumerate(LESSONS, start=1):
        cur.execute(
            """
            INSERT INTO product.course_lessons (course_id, clinic_id, position, title, content)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (course_id, cid, i, title, content.strip()),
        )

    for i, (text, explanation, options) in enumerate(QUESTIONS, start=1):
        cur.execute(
            """
            INSERT INTO product.course_questions
                (course_id, clinic_id, position, text, explanation)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (course_id, cid, i, text, explanation),
        )
        question_id = cur.fetchone()[0]
        for j, (option_text, is_correct) in enumerate(options, start=1):
            cur.execute(
                """
                INSERT INTO product.course_options
                    (question_id, clinic_id, position, text, is_correct)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (question_id, cid, j, option_text, is_correct),
            )

    print(f"  курс: {COURSE_TITLE}")
    print(f"    уроков {len(LESSONS)}, вопросов {len(QUESTIONS)}, порог {PASS_SCORE}%")
    return course_id


def assign(cur, cid: str, course_id: str, author: int | None) -> list[tuple]:
    """Назначает курс всем работающим. Портальный сотрудник идёт первым в
    возврате, чтобы его назначение осталось не начатым."""
    cur.execute(
        """
        SELECT id, full_name FROM product.employees
         WHERE clinic_id = %s AND status <> 'dismissed'
         ORDER BY (full_name = %s) DESC, full_name
        """,
        (cid, PORTAL_EMPLOYEE),
    )
    staff = cur.fetchall()
    if not staff:
        raise SystemExit("в демо-клинике нет сотрудников — сначала db/seed_dental.py")

    for employee_id, _ in staff:
        cur.execute(
            """
            INSERT INTO product.course_assignments
                (clinic_id, course_id, employee_id, due_at, assigned_by)
            VALUES (%s, %s, %s, current_date + 14, %s)
            ON CONFLICT (course_id, employee_id) DO NOTHING
            """,
            (cid, course_id, employee_id, author),
        )
    print(f"  назначено сотрудникам: {len(staff)}")
    return staff


def answer_key(cur, course_id: str) -> list[tuple[str, list[str]]]:
    """Вопрос → [верный вариант, остальные]. Читаем под владельцем: прикладной
    роли колонка is_correct недоступна, и это ровно то, что нужно защитить."""
    cur.execute(
        """
        SELECT q.id, o.id, o.is_correct
        FROM product.course_questions q
        JOIN product.course_options o ON o.question_id = q.id
        WHERE q.course_id = %s
        ORDER BY q.position, o.position
        """,
        (course_id,),
    )
    by_question: dict[str, dict[str, list[str]]] = {}
    order: list[str] = []
    for question_id, option_id, is_correct in cur.fetchall():
        if question_id not in by_question:
            by_question[question_id] = {"right": [], "wrong": []}
            order.append(question_id)
        by_question[question_id]["right" if is_correct else "wrong"].append(option_id)
    return [(q, by_question[q]["right"] + by_question[q]["wrong"]) for q in order]


def play(cur, cid: str, course_id: str, staff: list[tuple], rng: random.Random) -> dict:
    """Разыгрывает прохождения по сценарию SCENARIOS."""
    key = answer_key(cur, course_id)
    total = len(key)
    stats = {"passed": 0, "failed": 0, "in_progress": 0, "not_started": 0, "attempts": 0}

    # Портальный сотрудник — первый в списке, его пропускаем: на демо тест
    # проходят живьём под его логином.
    for (employee_id, full_name), scenario in zip(staff[1:], SCENARIOS, strict=False):
        cur.execute(
            "SELECT id FROM product.course_assignments "
            "WHERE course_id = %s AND employee_id = %s",
            (course_id, employee_id),
        )
        assignment_id = cur.fetchone()[0]

        if scenario is None:
            stats["not_started"] += 1
            continue

        if not scenario:
            # Открытая попытка без ответов: человек начал и не дошёл до конца.
            # Именно такие и нужны менеджеру в первую очередь.
            cur.execute(
                """
                INSERT INTO product.course_attempts
                    (clinic_id, assignment_id, employee_id, started_at)
                VALUES (%s, %s, %s, now() - interval '3 days')
                """,
                (cid, assignment_id, employee_id),
            )
            cur.execute(
                "UPDATE product.course_assignments SET status = 'in_progress' WHERE id = %s",
                (assignment_id,),
            )
            stats["in_progress"] += 1
            print(f"    {full_name}: начал и не закончил")
            continue

        best = 0
        passed_any = False
        # Попытки идут по времени вперёд: пересдача должна быть позже провала.
        days_ago = rng.randint(8, 14)
        for correct_count in scenario:
            days_ago = max(1, days_ago - rng.randint(2, 4))
            # Ответы: correct_count верных, остальные — случайный неверный.
            answers: dict[str, str] = {}
            picks = list(range(total))
            rng.shuffle(picks)
            right_ones = set(picks[:correct_count])
            for idx, (question_id, options) in enumerate(key):
                if idx in right_ones:
                    answers[str(question_id)] = str(options[0])
                else:
                    answers[str(question_id)] = str(rng.choice(options[1:]))

            score = round(correct_count * 100 / total)
            passed = score >= PASS_SCORE
            best = max(best, score)
            passed_any = passed_any or passed
            stats["attempts"] += 1

            cur.execute(
                """
                INSERT INTO product.course_attempts
                    (clinic_id, assignment_id, employee_id, answers, score,
                     correct_count, total_count, passed, started_at, finished_at)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s,
                        now() - (%s || ' days')::interval,
                        now() - (%s || ' days')::interval + interval '9 minutes')
                """,
                (
                    cid, assignment_id, employee_id,
                    json.dumps(answers, ensure_ascii=False),
                    score, correct_count, total, passed,
                    days_ago, days_ago,
                ),
            )

        status = "passed" if passed_any else "failed"
        cur.execute(
            """
            UPDATE product.course_assignments
               SET status = %s, best_score = %s,
                   completed_at = CASE WHEN %s THEN now() - interval '1 day' ELSE NULL END
             WHERE id = %s
            """,
            (status, best, passed_any, assignment_id),
        )
        stats["passed" if passed_any else "failed"] += 1
        print(f"    {full_name}: {status}, лучший балл {best}%, попыток {len(scenario)}")

    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="удалить прежние курсы демо-клиники")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("нужен DATABASE_URL владельца")
        return 1

    # Фиксированное зерно: демо должно выглядеть одинаково при каждом пересеве,
    # иначе скриншоты в отчёте не совпадают с тем, что на экране.
    rng = random.Random(2026)  # noqa: S311 — раскладка демо-ответов, не криптография

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cid = clinic_id(cur)
        cur.execute("SELECT set_config('ishmed.clinic_id', %s, false)", (str(cid),))

        if args.reset:
            reset(cur, cid)
        else:
            cur.execute(
                "SELECT id FROM product.courses WHERE clinic_id = %s AND title = %s",
                (cid, COURSE_TITLE),
            )
            if cur.fetchone():
                print("  курс уже есть — пересев с --reset")
                return 0

        author = manager_id(cur, cid)
        course_id = build_course(cur, cid, author)
        staff = assign(cur, cid, course_id, author)
        stats = play(cur, cid, course_id, staff, rng)
        conn.commit()

    print(
        "  прохождения: сдали {passed}, провалили {failed}, в процессе "
        "{in_progress}, не начали {not_started}, попыток всего {attempts}".format(**stats)
    )
    print(f"  {PORTAL_EMPLOYEE}: назначено и не начато — проходить живьём в портале")
    return 0


if __name__ == "__main__":
    sys.exit(main())
