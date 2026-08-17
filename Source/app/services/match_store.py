"""Подбор в кабинете клиники: пул, пересчёт матчей, приглашения.

Соединение приходит из `ManagerConn`, то есть с выставленным контекстом RLS.
Запросов с `WHERE clinic_id` здесь нет и быть не должно: изоляцию держат
политики, а дублирующее условие в коде создаёт ложное чувство, что оно и
защищает.

Разделение с `matching.py` строгое: там решают, кто подходит и почему, здесь —
только читают и пишут. Алгоритм не должен уметь ходить в базу, иначе его нельзя
прогнать на придуманных случаях.
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.matching import ALGORITHM_VERSION, Ranking, rank

log = logging.getLogger(__name__)

# Больше двухсот карточек за один подбор не берём. Ограничение то же, что в
# product.pool_candidates: на P0 пул меньше, а когда вырастет, упрётся сначала
# сюда, и это будет видно, а не превратится в медленную страницу.
POOL_LIMIT = 200

# Поля вакансии, которые нужны алгоритму. Ровно они, а не `j.*`: подбор не
# должен видеть source_text и extraction — иначе однажды кто-нибудь начнёт
# сравнивать по ним.
_JOB_FIELDS = """
    j.id AS job_id, j.title, j.status::text AS status, j.public_code,
    j.interview_plan_status, j.role_category, j.specialty,
    j.experience_min_months, j.required_skills, j.required_languages,
    j.city, j.districts, j.schedule,
    j.salary_min_uzs, j.salary_max_uzs, j.credential_requirements
"""


# ── Вакансии для селектора ────────────────────────────────────────────────────

async def matchable_jobs(conn) -> list[dict[str, Any]]:
    """Вакансии, под которые можно подбирать.

    Черновики тоже: подобрать людей до публикации полезно — менеджер увидит,
    есть ли вообще кому откликаться, и поправит требования. Приглашать по
    черновику нельзя, и это проверяет `product.send_invitation`, а не список.
    """
    cur = await conn.execute(
        f"""
        SELECT {_JOB_FIELDS},
               s.name_ru AS specialty_name,
               (SELECT count(*) FROM product.matches m
                 WHERE m.job_id = j.id AND m.algorithm_version = %(algo)s) AS matches_count,
               (SELECT count(*) FROM product.invitations i WHERE i.job_id = j.id)
                   AS invitations_count
          FROM product.jobs j
          LEFT JOIN product.specialties s ON s.code = j.specialty
         WHERE j.status <> 'closed'
         ORDER BY (j.status = 'active') DESC, j.updated_at DESC
        """,  # noqa: S608 — _JOB_FIELDS константа модуля, не пользовательский ввод
        {"algo": ALGORITHM_VERSION},
    )
    return await cur.fetchall()


async def job_for_matching(conn, job_id: str) -> dict[str, Any] | None:
    cur = await conn.execute(
        f"SELECT {_JOB_FIELDS}, s.name_ru AS specialty_name "  # noqa: S608 — см. выше
        "FROM product.jobs j LEFT JOIN product.specialties s ON s.code = j.specialty "
        "WHERE j.id = %s",
        (job_id,),
    )
    return await cur.fetchone()


# ── Пул ───────────────────────────────────────────────────────────────────────

async def pool(
    conn,
    *,
    role_category: str | None = None,
    specialty: str | None = None,
    district: str | None = None,
    schedule: str | None = None,
    experience_min: int | None = None,
    salary_max: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Общий поиск по карточкам, которые люди сами вывели в поиск.

    Через `product.pool_candidates`: список колонок там закрытый и анонимный.
    Прямой SELECT по `candidate_profiles` тоже сработал бы — политика отдаёт
    активные профили менеджеру, — но тогда состав полей зависел бы от
    аккуратности этого файла, а не от базы.
    """
    cur = await conn.execute(
        "SELECT * FROM product.pool_candidates(%s, %s, %s, %s, %s, %s, %s, %s)",
        (role_category, specialty, district, schedule,
         experience_min, salary_max, limit, offset),
    )
    return await cur.fetchall()


# ── Пересчёт матчей ───────────────────────────────────────────────────────────

async def recompute(conn, job_id: str) -> Ranking | None:
    """Считает подбор заново и сохраняет результат.

    Порядок важен: сначала пишем то, что получилось, потом убираем то, что
    выпало. Матчи с уже отправленным приглашением не удаляем никогда — иначе
    кнопка «Подобрать» стирала бы историю разговора с человеком, который
    перестал проходить по обновившимся требованиям.
    """
    job = await job_for_matching(conn, job_id)
    if job is None:
        return None

    candidates = await pool(conn, role_category=job["role_category"], limit=POOL_LIMIT)
    ranking = rank(job, candidates)

    for m in ranking.matches:
        await conn.execute(
            """
            INSERT INTO product.matches
                (job_id, candidate_id, level, score_internal,
                 hard_constraints_passed, reasons, gaps, algorithm_version)
            VALUES (%(job)s, %(cand)s, %(level)s::product.match_level, %(score)s,
                    %(hard)s, %(reasons)s::text[], %(gaps)s::text[], %(algo)s)
            ON CONFLICT (job_id, candidate_id, algorithm_version) DO UPDATE
                SET level = EXCLUDED.level,
                    score_internal = EXCLUDED.score_internal,
                    hard_constraints_passed = EXCLUDED.hard_constraints_passed,
                    reasons = EXCLUDED.reasons,
                    gaps = EXCLUDED.gaps
            """,
            {
                "job": job_id, "cand": m.candidate_id, "level": m.level,
                "score": m.score, "hard": m.hard_constraints_passed,
                "reasons": m.reasons, "gaps": m.gaps, "algo": ALGORITHM_VERSION,
            },
        )

    await conn.execute(
        """
        DELETE FROM product.matches m
         WHERE m.job_id = %(job)s
           AND m.algorithm_version = %(algo)s
           AND NOT (m.candidate_id = ANY(%(keep)s::uuid[]))
           AND NOT EXISTS (SELECT 1 FROM product.invitations i
                            WHERE i.job_id = m.job_id AND i.candidate_id = m.candidate_id)
        """,
        {
            "job": job_id, "algo": ALGORITHM_VERSION,
            "keep": [m.candidate_id for m in ranking.matches],
        },
    )

    log.info(
        "подбор по вакансии %s: показываем %s, отсеяно %s (%s)",
        job_id, len(ranking.matches), ranking.excluded_total, ranking.excluded,
    )
    return ranking


async def matches(conn, job_id: str) -> list[dict[str, Any]]:
    """Сохранённый подбор с карточками и состоянием приглашения.

    Имени здесь нет: в пуле человек анонимен до приглашения. В списке откликов
    имя показано, потому что там он пришёл к этой клинике сам.
    """
    cur = await conn.execute(
        """
        SELECT m.id AS match_id, m.candidate_id, m.level::text AS level,
               m.score_internal, m.hard_constraints_passed, m.reasons, m.gaps,
               m.algorithm_version, m.created_at,
               c.role_category, c.specialty, c.experience_months,
               c.skills, c.languages, c.city, c.districts, c.schedule,
               c.salary_min_uzs, c.credential_claims,
               c.self_filled_at IS NOT NULL AS self_filled,
               rc.name_ru AS role_name, s.name_ru AS specialty_name,
               i.id AS invitation_id, i.status::text AS invitation_status,
               i.sent_at AS invited_at, i.responded_at,
               EXISTS (SELECT 1 FROM product.applications a
                        WHERE a.job_id = m.job_id AND a.candidate_id = m.candidate_id)
                   AS has_application
          FROM product.matches m
          JOIN product.candidate_profiles c ON c.id = m.candidate_id
          LEFT JOIN product.role_categories rc ON rc.code = c.role_category
          LEFT JOIN product.specialties s      ON s.code  = c.specialty
          LEFT JOIN product.invitations i
                 ON i.job_id = m.job_id AND i.candidate_id = m.candidate_id
         WHERE m.job_id = %(job)s AND m.algorithm_version = %(algo)s
         ORDER BY m.score_internal DESC, c.experience_months DESC NULLS LAST
        """,
        {"job": job_id, "algo": ALGORITHM_VERSION},
    )
    return await cur.fetchall()


# ── Приглашения ───────────────────────────────────────────────────────────────

async def invite(
    conn, *, job_id: str, candidate_id: str, actor_user_id: int, message: str | None = None
) -> dict[str, Any]:
    """Отправляет приглашение. Все проверки внутри функции базы.

    Там же живёт правило «только на опубликованную вакансию с одобренным
    планом»: приглашение ведёт в собеседование, и без плана человек придёт в
    пустоту.
    """
    cur = await conn.execute(
        "SELECT * FROM product.send_invitation(%s, %s, %s, %s)",
        (job_id, candidate_id, actor_user_id, message),
    )
    row = await cur.fetchone()
    assert row is not None
    return row


async def invitations(conn, job_id: str | None = None) -> list[dict[str, Any]]:
    """Приглашения клиники. Контакт здесь не отдаётся — он открывается
    отдельным действием после accept, и каждое открытие пишется в журнал."""
    cur = await conn.execute(
        """
        SELECT i.id AS invitation_id, i.status::text AS invitation_status,
               i.message, i.sent_at, i.responded_at,
               j.id AS job_id, j.title AS job_title,
               i.candidate_id,
               c.role_category, c.specialty, c.experience_months,
               c.salary_min_uzs,
               s.name_ru AS specialty_name,
               EXISTS (SELECT 1 FROM product.applications a
                        WHERE a.job_id = i.job_id AND a.candidate_id = i.candidate_id)
                   AS has_application
          FROM product.invitations i
          JOIN product.jobs j ON j.id = i.job_id
          JOIN product.candidate_profiles c ON c.id = i.candidate_id
          LEFT JOIN product.specialties s ON s.code = c.specialty
         WHERE (%(job)s::uuid IS NULL OR i.job_id = %(job)s::uuid)
         ORDER BY i.sent_at DESC
        """,
        {"job": job_id},
    )
    return await cur.fetchall()


async def reveal_invited_contact(conn, invitation_id: str, actor_user_id: int) -> dict[str, Any] | None:
    """Контакт приглашённого. Только после accept — проверяет функция базы,
    она же пишет событие в журнал согласий."""
    cur = await conn.execute(
        "SELECT * FROM product.reveal_contact(%s, %s)", (invitation_id, actor_user_id)
    )
    return await cur.fetchone()


# ── Справочники для фильтров ──────────────────────────────────────────────────

async def dictionaries(conn) -> dict[str, Any]:
    """Всё, из чего собираются фильтры подбора.

    Отдаём оба языка: кабинет двуязычный, а справочники статические — второй
    запрос при переключении языка не нужен.
    """
    out: dict[str, Any] = {}
    cur = await conn.execute(
        "SELECT code, name_ru, name_uz FROM product.role_categories ORDER BY sort, code"
    )
    out["roles"] = await cur.fetchall()
    cur = await conn.execute(
        "SELECT code, role_category, name_ru, name_uz FROM product.specialties "
        "ORDER BY role_category, name_ru"
    )
    out["specialties"] = await cur.fetchall()
    cur = await conn.execute(
        "SELECT code, name_ru, name_uz FROM product.districts ORDER BY name_ru"
    )
    out["districts"] = await cur.fetchall()
    cur = await conn.execute(
        "SELECT code, name_ru, name_uz FROM product.schedule_kinds ORDER BY code"
    )
    out["schedules"] = await cur.fetchall()
    return out
