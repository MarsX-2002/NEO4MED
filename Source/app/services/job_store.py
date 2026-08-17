"""Чтение и запись вакансий и плана интервью в кабинете клиники.

Всё под контекстом RLS: соединение приходит из `ManagerConn`, поэтому запросы
без clinic_id вернут пустоту, а не чужие данные. Публикация идёт через
`product.publish_job`, чтобы правило «нет одобренного плана — нет публикации»
жило в одном месте.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_FIELDS = (
    "title", "role_category", "specialty", "experience_min_months",
    "required_skills", "required_languages", "city", "districts", "schedule",
    "salary_min_uzs", "salary_max_uzs", "credential_requirements",
    "source_text", "interview_intro",
)


async def listing(conn, *, status: str | None = None) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        SELECT j.id, j.title, j.role_category, j.specialty, s.name_ru AS specialty_name,
               j.city, j.schedule, j.salary_min_uzs, j.salary_max_uzs,
               j.status, j.public_code, j.published_at, j.closed_at,
               j.interview_plan_status,
               (SELECT count(*) FROM product.applications a WHERE a.job_id = j.id)
                   AS applications_count,
               (SELECT count(*) FROM product.job_questions q WHERE q.job_id = j.id)
                   AS questions_count,
               (SELECT count(*) FROM product.applications a
                 JOIN product.interviews i ON i.application_id = a.id
                WHERE a.job_id = j.id AND i.status = 'completed')
                   AS interviews_done,
               j.created_at, j.updated_at
          FROM product.jobs j
          LEFT JOIN product.specialties s ON s.code = j.specialty
         WHERE (%(status)s::text IS NULL OR j.status::text = %(status)s::text)
         ORDER BY j.published_at DESC NULLS FIRST, j.created_at DESC
        """,
        {"status": status},
    )
    return await cur.fetchall()


async def summary(conn) -> dict[str, Any]:
    cur = await conn.execute(
        """
        SELECT count(*) FILTER (WHERE status = 'draft')  AS drafts,
               count(*) FILTER (WHERE status = 'active') AS active,
               count(*) FILTER (WHERE status = 'closed') AS closed,
               (SELECT count(*) FROM product.applications a
                 JOIN product.jobs jj ON jj.id = a.job_id)  AS applications,
               (SELECT count(*) FROM product.applications a
                 JOIN product.jobs jj ON jj.id = a.job_id
                 JOIN product.interviews i ON i.application_id = a.id
                WHERE i.status = 'completed')            AS interviews_done
          FROM product.jobs
        """
    )
    return await cur.fetchone() or {}


async def get(conn, job_id: str) -> dict[str, Any] | None:
    cur = await conn.execute(
        """
        SELECT j.*, s.name_ru AS specialty_name,
               (SELECT count(*) FROM product.job_questions q WHERE q.job_id = j.id)
                   AS questions_count,
               (SELECT count(*) FROM product.applications a WHERE a.job_id = j.id)
                   AS applications_count
          FROM product.jobs j
          LEFT JOIN product.specialties s ON s.code = j.specialty
         WHERE j.id = %s
        """,
        (job_id,),
    )
    return await cur.fetchone()


async def create(conn, *, created_by: int, **fields: Any) -> dict[str, Any]:
    values = {k: fields.get(k) for k in _FIELDS}
    values["created_by"] = created_by
    cur = await conn.execute(
        """
        INSERT INTO product.jobs
            (clinic_id, title, role_category, specialty, experience_min_months,
             required_skills, required_languages, city, districts, schedule,
             salary_min_uzs, salary_max_uzs, credential_requirements,
             source_text, interview_intro, created_by, status)
        VALUES (product.current_clinic_id(), %(title)s, %(role_category)s, %(specialty)s,
                %(experience_min_months)s,
                coalesce(%(required_skills)s::text[], '{}'), coalesce(%(required_languages)s::text[], '{}'),
                coalesce(%(city)s::text, 'tashkent'), coalesce(%(districts)s::text[], '{}'),
                coalesce(%(schedule)s::text[], '{}'),
                %(salary_min_uzs)s, %(salary_max_uzs)s,
                coalesce(%(credential_requirements)s::text[], '{}'),
                %(source_text)s, %(interview_intro)s, %(created_by)s, 'draft')
        RETURNING id, title, public_code, status, interview_plan_status
        """,
        values,
    )
    return await cur.fetchone()


async def update(conn, job_id: str, **fields: Any) -> dict[str, Any] | None:
    """Частичное обновление. Правка полей сбрасывает одобрение плана.

    Так задумано: если менеджер поменял требования, старые вопросы могли
    перестать соответствовать вакансии, и одобрять их надо заново. Молча
    оставить approved значило бы публиковать вакансию с планом не про неё.
    """
    given = {k: v for k, v in fields.items() if k in _FIELDS and v is not None}
    if not given:
        return await get(conn, job_id)

    # Имена колонок берутся из белого списка _FIELDS, значения идут
    # параметрами: подставить в запрос произвольное поле нельзя.
    sets = ", ".join(f"{k} = %({k})s" for k in given)
    params = dict(given, job_id=job_id)
    cur = await conn.execute(
        f"UPDATE product.jobs SET {sets}, "  # noqa: S608 — поля из белого списка
        "interview_plan_status = CASE WHEN interview_plan_status = 'approved' "
        "THEN 'draft' ELSE interview_plan_status END "
        "WHERE id = %(job_id)s RETURNING id, interview_plan_status",
        params,
    )
    return await cur.fetchone()


async def publish(conn, job_id: str) -> dict[str, Any]:
    cur = await conn.execute("SELECT * FROM product.publish_job(%s)", (job_id,))
    return await cur.fetchone()


async def close(conn, job_id: str) -> None:
    await conn.execute("SELECT product.close_job(%s)", (job_id,))


# ── План интервью ─────────────────────────────────────────────────────────────

async def questions(conn, job_id: str) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT id, ord, question, intent, origin, edited "
        "FROM product.job_questions WHERE job_id = %s ORDER BY ord",
        (job_id,),
    )
    return await cur.fetchall()


async def replace_questions(
    conn, job_id: str, items: list[dict[str, Any]], *, origin: str = "ai"
) -> list[dict[str, Any]]:
    """Заменяет план целиком и снимает одобрение.

    Целиком, а не пообъектно: порядок вопросов — часть плана, и точечные
    правки с перестановкой ord дают конфликты по UNIQUE (job_id, ord).
    Сохранённый план всегда переходит в draft — одобряет менеджер отдельным
    действием, иначе «одобрено» ничего не значит.
    """
    await conn.execute("DELETE FROM product.job_questions WHERE job_id = %s", (job_id,))
    for i, item in enumerate(items[:12], start=1):
        text = (item.get("question") or "").strip()
        if not 5 <= len(text) <= 400:
            continue
        await conn.execute(
            "INSERT INTO product.job_questions (job_id, ord, question, intent, origin, edited) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (job_id, i, text, (item.get("intent") or None), origin, bool(item.get("edited"))),
        )
    await conn.execute(
        "UPDATE product.jobs SET interview_plan_status = 'draft' WHERE id = %s", (job_id,)
    )
    return await questions(conn, job_id)


async def approve_plan(conn, job_id: str) -> dict[str, Any] | None:
    """Одобрение плана менеджером. Меньше трёх вопросов не одобряем."""
    cur = await conn.execute(
        """
        UPDATE product.jobs j
           SET interview_plan_status = 'approved'
         WHERE j.id = %s
           AND (SELECT count(*) FROM product.job_questions q WHERE q.job_id = j.id) >= 3
        RETURNING j.id, j.interview_plan_status
        """,
        (job_id,),
    )
    return await cur.fetchone()


# ── Справочники для разбора текста ────────────────────────────────────────────

async def dictionaries(conn) -> dict[str, Any]:
    cur = await conn.execute("SELECT code FROM product.role_categories ORDER BY code")
    roles = [r["code"] for r in await cur.fetchall()]
    cur = await conn.execute(
        "SELECT code, name_ru, role_category FROM product.specialties ORDER BY role_category, code"
    )
    specialties = await cur.fetchall()
    return {"roles": roles, "specialties": specialties}


# ── Отклики и интервью ────────────────────────────────────────────────────────

async def applications(conn, job_id: str | None = None) -> list[dict[str, Any]]:
    """Отклики с итогом интервью. Контакт кандидата здесь НЕ отдаётся.

    Он открывается отдельным действием после принятия отклика, через
    `product.reveal_application_contact` — и это фиксируется в журнале согласий.
    """
    cur = await conn.execute(
        """
        SELECT a.id AS application_id, a.status, a.applied_at, a.message,
               j.id AS job_id, j.title AS job_title,
               i.id AS interview_id, i.status AS interview_status,
               i.summary, i.gaps, i.follow_ups, i.extraction,
               i.started_at, i.finished_at, i.voice_answers,
               (SELECT count(*) FROM product.interview_turns t
                 WHERE t.interview_id = i.id
                   AND t.kind = 'question' AND t.answered_at IS NOT NULL) AS answered,
               (SELECT count(*) FROM product.job_questions q WHERE q.job_id = j.id) AS total,
               (SELECT count(*) FROM product.interview_turns t
                 WHERE t.interview_id = i.id AND t.kind = 'follow_up') AS follow_ups_asked,
               c.role_category, c.specialty, c.experience_months, c.skills, c.languages,
               c.salary_min_uzs AS salary_expectation_uzs,
               -- Имя — не контакт. Позвонить по нему нельзя, а «Кандидат
               -- 3f2a91c4» в списке откликов не читается как человек.
               -- product.users без RLS сознательно, поэтому join безопасен:
               -- сам профиль сюда попадает только по политике candidate_profiles.
               cu.full_name AS candidate_name
          FROM product.applications a
          JOIN product.jobs j ON j.id = a.job_id
          LEFT JOIN product.interviews i ON i.application_id = a.id
          LEFT JOIN product.candidate_profiles c ON c.id = a.candidate_id
          LEFT JOIN product.users cu ON cu.id = c.user_id
         WHERE (%(job_id)s::uuid IS NULL OR a.job_id = %(job_id)s::uuid)
         ORDER BY a.applied_at DESC
        """,
        {"job_id": job_id},
    )
    return await cur.fetchall()


async def transcript(conn, interview_id: str) -> list[dict[str, Any]]:
    """Полный ход разговора. Клиника видит и саммари, и каждую реплику."""
    cur = await conn.execute(
        """
        SELECT ord, kind, question_text, answer_kind, answer_text,
               voice_seconds, asked_at, answered_at
          FROM product.interview_turns
         WHERE interview_id = %s
         ORDER BY ord
        """,
        (interview_id,),
    )
    return await cur.fetchall()


async def set_application_status(conn, application_id: str, status: str) -> dict[str, Any] | None:
    cur = await conn.execute(
        """
        UPDATE product.applications
           SET status = %s::product.application_status,
               responded_at = now(),
               viewed_at = coalesce(viewed_at, now())
         WHERE id = %s
        RETURNING id, status
        """,
        (status, application_id),
    )
    return await cur.fetchone()
