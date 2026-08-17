"""Обучение: курсы клиники и прохождение сотрудником.

Разделение обязанностей между этим модулем и базой намеренное и жёсткое.
Здесь только запросы и сборка ответа. Всё, что связано с правильными ответами
и с баллом, живёт в SECURITY DEFINER функциях схемы product:

  * прикладной роли отозвано право читать `course_options.is_correct`,
    поэтому проверить тест в питоне физически нельзя — считает
    `product.grade_attempt`;
  * начало попытки идёт через `product.start_course_attempt`: сотруднику
    запись в `course_assignments` закрыта политикой, иначе он поставил бы
    себе `passed` сам;
  * ключ ответов (`product.course_answer_key`) отдаётся только менеджеру —
    проверка в самой функции, а не здесь.

То есть «мы не забудем убрать поле в сериализаторе» тут вообще не обещание:
поля нет.
"""
from __future__ import annotations

import json
from typing import Any

# ── Кабинет менеджера ─────────────────────────────────────────────────────────

async def listing(conn) -> list[dict[str, Any]]:
    """Курсы клиники с прогрессом по каждому.

    Менеджеру нужен не список названий, а ответ «кто ещё не прошёл»: поэтому
    считаем назначения по статусам прямо в выдаче.
    """
    cur = await conn.execute(
        """
        SELECT c.id::text, c.title, c.summary, c.status::text, c.pass_score,
               c.role_category, c.specialty, c.created_at, c.updated_at,
               rc.name_ru AS role_name, s.name_ru AS specialty_name,
               (SELECT count(*) FROM product.course_lessons l WHERE l.course_id = c.id)
                   AS lessons_count,
               (SELECT count(*) FROM product.course_questions q WHERE q.course_id = c.id)
                   AS questions_count,
               count(asg.id)                                            AS assigned,
               count(asg.id) FILTER (WHERE asg.status = 'passed')        AS passed,
               count(asg.id) FILTER (WHERE asg.status = 'failed')       AS failed,
               count(asg.id) FILTER (WHERE asg.status = 'in_progress')  AS in_progress,
               count(asg.id) FILTER (WHERE asg.status = 'assigned')     AS not_started,
               round(avg(asg.best_score) FILTER (WHERE asg.best_score IS NOT NULL), 1)
                   AS avg_score
        FROM product.courses c
        LEFT JOIN product.course_assignments asg ON asg.course_id = c.id
        LEFT JOIN product.role_categories rc ON rc.code = c.role_category
        LEFT JOIN product.specialties s ON s.code = c.specialty
        GROUP BY c.id, rc.name_ru, s.name_ru
        ORDER BY c.status, c.title
        """
    )
    return await cur.fetchall()


async def summary(conn) -> dict[str, Any]:
    cur = await conn.execute(
        """
        SELECT (SELECT count(*) FROM product.courses WHERE status = 'published') AS published,
               (SELECT count(*) FROM product.courses WHERE status = 'draft')     AS drafts,
               count(*)                                          AS assigned,
               count(*) FILTER (WHERE status = 'passed')          AS passed,
               count(*) FILTER (WHERE status = 'failed')          AS failed,
               count(*) FILTER (WHERE status <> 'passed'
                                 AND due_at IS NOT NULL
                                 AND due_at < current_date)       AS overdue
        FROM product.course_assignments
        """
    )
    return await cur.fetchone()


async def course(conn, course_id: str) -> dict[str, Any] | None:
    cur = await conn.execute(
        """
        SELECT c.id::text, c.title, c.summary, c.status::text, c.pass_score,
               c.role_category, c.specialty, c.created_at,
               rc.name_ru AS role_name, s.name_ru AS specialty_name
        FROM product.courses c
        LEFT JOIN product.role_categories rc ON rc.code = c.role_category
        LEFT JOIN product.specialties s ON s.code = c.specialty
        WHERE c.id = %s
        """,
        (course_id,),
    )
    return await cur.fetchone()


async def lessons(conn, course_id: str) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        SELECT l.id::text, l.position AS ord, l.title, l.content
        FROM product.course_lessons l
        WHERE l.course_id = %s
        ORDER BY l.position
        """,
        (course_id,),
    )
    return await cur.fetchall()


async def questions_with_key(conn, course_id: str) -> list[dict[str, Any]]:
    """Вопросы с отметкой правильного варианта — для менеджера.

    Флаг приходит не из таблицы, а из `product.course_answer_key`: колонка
    `is_correct` прикладной роли недоступна, и функция сама проверяет, что
    спрашивает менеджер.
    """
    cur = await conn.execute(
        """
        WITH key AS (SELECT * FROM product.course_answer_key(%(course)s::uuid))
        SELECT q.id::text, q.position AS ord, q.text, q.explanation,
               COALESCE(
                   (SELECT jsonb_agg(jsonb_build_object(
                               'id', o.id::text, 'text', o.text,
                               'is_correct', coalesce(k.is_correct, false))
                            ORDER BY o.position)
                      FROM product.course_options o
                      LEFT JOIN key k ON k.option_id = o.id
                     WHERE o.question_id = q.id),
                   '[]'::jsonb
               ) AS options
        FROM product.course_questions q
        WHERE q.course_id = %(course)s::uuid
        ORDER BY q.position
        """,
        {"course": course_id},
    )
    return await cur.fetchall()


async def assignments(conn, *, course_id: str | None = None) -> list[dict[str, Any]]:
    """Прохождение курсов сотрудниками. Это и есть раздел «Результаты»."""
    cur = await conn.execute(
        """
        SELECT asg.id::text, asg.status::text, asg.due_at, asg.assigned_at,
               asg.completed_at, asg.best_score,
               asg.course_id::text, c.title AS course_title, c.pass_score,
               asg.employee_id::text, e.full_name AS employee_name,
               u.name AS unit_name,
               (SELECT count(*) FROM product.course_attempts at
                 WHERE at.assignment_id = asg.id AND at.finished_at IS NOT NULL)
                   AS attempts,
               (SELECT at.id::text FROM product.course_attempts at
                 WHERE at.assignment_id = asg.id AND at.finished_at IS NOT NULL
                 ORDER BY at.finished_at DESC LIMIT 1) AS last_attempt_id
        FROM product.course_assignments asg
        JOIN product.courses c ON c.id = asg.course_id
        JOIN product.employees e ON e.id = asg.employee_id
        LEFT JOIN product.clinic_units u ON u.id = e.unit_id
        WHERE (%(course)s::uuid IS NULL OR asg.course_id = %(course)s::uuid)
        ORDER BY c.title, (asg.status = 'passed'), e.full_name
        """,
        {"course": course_id},
    )
    return await cur.fetchall()


# ── Портал сотрудника ─────────────────────────────────────────────────────────
# Всё ниже работает в контексте роли employee: product.employees для неё
# закрыта политикой, поэтому своя карточка приходит функцией, а курсы
# фильтруются по своему employee_id, а не по «доверься клиенту».

async def my_card(conn) -> dict[str, Any] | None:
    cur = await conn.execute(
        "SELECT employee_id::text, full_name, unit_name, role_name, clinic_name "
        "FROM product.my_employee_card()"
    )
    return await cur.fetchone()


async def my_courses(conn, employee_id: str) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        SELECT asg.id::text AS assignment_id, asg.status::text, asg.due_at,
               asg.assigned_at, asg.completed_at, asg.best_score,
               c.id::text AS course_id, c.title, c.summary, c.pass_score,
               (SELECT count(*) FROM product.course_lessons l WHERE l.course_id = c.id)
                   AS lessons_count,
               (SELECT count(*) FROM product.course_questions q WHERE q.course_id = c.id)
                   AS questions_count,
               (SELECT count(*) FROM product.course_attempts at
                 WHERE at.assignment_id = asg.id AND at.finished_at IS NOT NULL)
                   AS attempts,
               (SELECT at.id::text FROM product.course_attempts at
                 WHERE at.assignment_id = asg.id AND at.finished_at IS NOT NULL
                 ORDER BY at.finished_at DESC LIMIT 1) AS last_attempt_id
        FROM product.course_assignments asg
        JOIN product.courses c ON c.id = asg.course_id
        WHERE asg.employee_id = %s
        ORDER BY (asg.status = 'passed'), asg.due_at NULLS LAST, c.title
        """,
        (employee_id,),
    )
    return await cur.fetchall()


async def my_course(conn, employee_id: str, course_id: str) -> dict[str, Any] | None:
    """Курс с материалом для прохождения.

    Вопросы здесь не отдаются вообще: они приходят только внутри попытки, из
    `product.attempt_questions`, и уже без правильных ответов.
    """
    cur = await conn.execute(
        """
        SELECT asg.id::text AS assignment_id, asg.status::text, asg.due_at,
               asg.best_score, asg.completed_at,
               c.id::text AS course_id, c.title, c.summary, c.pass_score,
               (SELECT count(*) FROM product.course_questions q WHERE q.course_id = c.id)
                   AS questions_count
        FROM product.course_assignments asg
        JOIN product.courses c ON c.id = asg.course_id
        WHERE asg.employee_id = %s AND asg.course_id = %s
        """,
        (employee_id, course_id),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    row["lessons"] = await lessons(conn, course_id)
    row["attempts"] = await my_attempts(conn, row["assignment_id"])
    return row


async def my_attempts(conn, assignment_id: str) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        SELECT at.id::text, at.score, at.correct_count, at.total_count, at.passed,
               at.started_at, at.finished_at
        FROM product.course_attempts at
        WHERE at.assignment_id = %s
        ORDER BY at.started_at DESC
        """,
        (assignment_id,),
    )
    return await cur.fetchall()


async def start_attempt(conn, course_id: str) -> str:
    """Начинает попытку либо возвращает незавершённую.

    Идемпотентность в функции, а не здесь: перезагрузка страницы посреди теста
    не должна плодить попытки, иначе «сколько раз сдавал» перестаёт что-либо
    значить.
    """
    cur = await conn.execute(
        "SELECT product.start_course_attempt(%s) AS id", (course_id,)
    )
    row = await cur.fetchone()
    return str(row["id"])


async def attempt_questions(conn, attempt_id: str) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT question_id::text, ord, text, options FROM product.attempt_questions(%s)",
        (attempt_id,),
    )
    return await cur.fetchall()


async def grade(conn, attempt_id: str, answers: dict[str, str]) -> dict[str, Any] | None:
    cur = await conn.execute(
        "SELECT score, correct_count, total_count, passed "
        "FROM product.grade_attempt(%s, %s::jsonb)",
        (attempt_id, json.dumps(answers)),
    )
    return await cur.fetchone()


async def attempt_review(conn, attempt_id: str) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        SELECT question_id::text, ord, text, explanation,
               chosen_id::text, correct_id::text, is_right
        FROM product.attempt_review(%s)
        """,
        (attempt_id,),
    )
    return await cur.fetchall()
