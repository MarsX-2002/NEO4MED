"""Вакансии и авто-интервью в кабинете клиники."""
from __future__ import annotations

import io
import logging

import segno
from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from app.config import settings
from app.services import job_store as store
from app.services import jobs as ai_jobs
from app.services.llm import LLMError
from app.web.deps import Manager, ManagerConn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["jobs"])

async def _schedule_codes(conn) -> set[str]:
    """Допустимые коды графика — из словаря базы, а не из списка в коде.

    Раньше здесь стоял захардкоженный набор, и он разошёлся с
    `product.schedule_kinds` в ОБЕ стороны: пропускал `weekend`, которого в
    словаре нет, и отвергал `day` и `rotational`, которые в нём есть. Обе
    ошибки тихие. Вакансия с кодом вне словаря выглядит нормально в кабинете, но
    подбор по графику её никогда ни с кем не сведёт: у кандидата такого кода
    быть не может. А `day` — самый частый график в клиниках, и его нельзя было
    указать вовсе.
    """
    cur = await conn.execute("SELECT code FROM product.schedule_kinds")
    return {r["code"] for r in await cur.fetchall()}


async def _reject_unknown_schedule(conn, schedule: list[str]) -> None:
    bad = set(schedule) - await _schedule_codes(conn)
    if bad:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Неизвестный график: {', '.join(sorted(bad))}",
        )


class JobIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    role_category: str = Field(min_length=2, max_length=40)
    specialty: str | None = None
    experience_min_months: int | None = Field(default=None, ge=0, le=600)
    required_skills: list[str] = Field(default_factory=list)
    required_languages: list[str] = Field(default_factory=list)
    city: str = "tashkent"
    districts: list[str] = Field(default_factory=list)
    schedule: list[str] = Field(default_factory=list)
    salary_min_uzs: int | None = Field(default=None, ge=0)
    salary_max_uzs: int | None = Field(default=None, ge=0)
    credential_requirements: list[str] = Field(default_factory=list)
    source_text: str | None = Field(default=None, max_length=8000)
    interview_intro: str | None = Field(default=None, max_length=600)


class JobPatch(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    role_category: str | None = None
    specialty: str | None = None
    experience_min_months: int | None = Field(default=None, ge=0, le=600)
    required_skills: list[str] | None = None
    required_languages: list[str] | None = None
    city: str | None = None
    districts: list[str] | None = None
    schedule: list[str] | None = None
    salary_min_uzs: int | None = Field(default=None, ge=0)
    salary_max_uzs: int | None = Field(default=None, ge=0)
    credential_requirements: list[str] | None = None
    source_text: str | None = Field(default=None, max_length=8000)
    interview_intro: str | None = Field(default=None, max_length=600)


class DraftText(BaseModel):
    """Свободный текст вакансии: менеджер написал или надиктовал."""

    text: str = Field(min_length=20, max_length=8000)


class QuestionIn(BaseModel):
    question: str = Field(min_length=5, max_length=400)
    intent: str | None = Field(default=None, max_length=40)
    edited: bool = False


class PlanIn(BaseModel):
    questions: list[QuestionIn] = Field(min_length=1, max_length=12)


@router.get("")
async def listing(
    conn: ManagerConn,
    _: Manager,
    status_filter: str | None = Query(default=None, alias="status"),
):
    return {
        "jobs": await store.listing(conn, status=status_filter),
        "summary": await store.summary(conn),
        "bot_username": settings().telegram_bot_username,
    }


@router.get("/{code}/qr.svg")
async def qr_svg(
    code: str,
    conn: ManagerConn,
    _: Manager,
    scale: int = Query(default=8, ge=2, le=40),
):
    """QR со ссылкой на вакансию.

    Рисуется по запросу и нигде не хранится — как и QR отзывов. Проверяем, что
    код принадлежит вакансии этой клиники: RLS не даст прочитать чужую, и
    отсутствие строки здесь означает отказ.
    """
    cur = await conn.execute(
        "SELECT public_code FROM product.jobs WHERE public_code = %s AND status = 'active'",
        (code,),
    )
    if await cur.fetchone() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Опубликованная вакансия не найдена"
        )

    link = f"https://t.me/{settings().telegram_bot_username}?start=job_{code}"
    qr = segno.make(link, error="m")
    # segno пишет SVG байтами, а не строкой, поэтому буфер именно BytesIO.
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=scale, border=3, dark="#17191c", light="#ffffff")
    return Response(
        content=buf.getvalue(),
        media_type="image/svg+xml",
        headers={"Content-Disposition": f'inline; filename="ishmed-job-{code}.svg"'},
    )


@router.get("/dictionaries")
async def dictionaries(conn: ManagerConn, _: Manager):
    return await store.dictionaries(conn)


@router.post("/parse")
async def parse_text(payload: DraftText, conn: ManagerConn, _: Manager):
    """Разбирает свободный текст в поля. Ничего не сохраняет.

    Отдельным шагом, а не внутри создания: менеджер должен увидеть, что
    получилось, и поправить. Модель тут помощник, а не источник правды.
    """
    dicts = await store.dictionaries(conn)
    try:
        fields = await ai_jobs.extract_fields(
            payload.text, roles=dicts["roles"], specialties=dicts["specialties"]
        )
    except LLMError as e:
        log.warning("разбор текста вакансии не удался: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Не удалось разобрать текст. Заполните поля вручную или попробуйте ещё раз.",
        ) from None
    return {"fields": fields, "source_text": payload.text}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(payload: JobIn, conn: ManagerConn, user: Manager):
    await _reject_unknown_schedule(conn, payload.schedule)
    if (
        payload.salary_min_uzs is not None
        and payload.salary_max_uzs is not None
        and payload.salary_min_uzs > payload.salary_max_uzs
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Минимальная оплата больше максимальной",
        )
    try:
        row = await store.create(conn, created_by=user.user_id, **payload.model_dump())
    except Exception as e:
        if "jobs_specialty_fkey" in str(e) or "jobs_role_category_fkey" in str(e):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Неизвестная специальность или категория",
            ) from None
        raise
    log.info("вакансия создана: %s", row["id"])
    return row


@router.get("/{job_id}")
async def detail(job_id: str, conn: ManagerConn, _: Manager):
    job = await store.get(conn, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Вакансия не найдена")
    return {
        "job": job,
        "questions": await store.questions(conn, job_id),
        "deep_link": _deep_link(job),
    }


@router.patch("/{job_id}")
async def patch(job_id: str, payload: JobPatch, conn: ManagerConn, _: Manager):
    given = payload.model_dump(exclude_unset=True)
    # Правка тоже обязана проверять график: создание проверяло, а PATCH — нет,
    # и через него в вакансию попадал любой код.
    if given.get("schedule") is not None:
        await _reject_unknown_schedule(conn, given["schedule"])
    row = await store.update(conn, job_id, **given)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Вакансия не найдена")
    return row


# ── План интервью ─────────────────────────────────────────────────────────────

@router.post("/{job_id}/plan/suggest")
async def suggest_plan(job_id: str, conn: ManagerConn, _: Manager):
    """Предлагает вопросы по вакансии и сохраняет как черновик плана."""
    job = await store.get(conn, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Вакансия не найдена")
    try:
        items = await ai_jobs.suggest_questions(job)
    except LLMError as e:
        log.warning("генерация плана интервью не удалась: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Не удалось предложить вопросы. Добавьте их вручную или повторите попытку.",
        ) from None
    saved = await store.replace_questions(conn, job_id, items, origin="ai")
    return {"questions": saved, "plan_status": "draft"}


@router.put("/{job_id}/plan")
async def save_plan(job_id: str, payload: PlanIn, conn: ManagerConn, _: Manager):
    saved = await store.replace_questions(
        conn, job_id, [q.model_dump() for q in payload.questions], origin="manual"
    )
    return {"questions": saved, "plan_status": "draft"}


@router.post("/{job_id}/plan/approve")
async def approve_plan(job_id: str, conn: ManagerConn, _: Manager):
    row = await store.approve_plan(conn, job_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="В плане должно быть не меньше трёх вопросов",
        )
    return row


# ── Публикация ────────────────────────────────────────────────────────────────

@router.post("/{job_id}/publish")
async def publish(job_id: str, conn: ManagerConn, _: Manager):
    try:
        row = await store.publish(conn, job_id)
    except Exception as e:
        message = str(e)
        if "не одобрен" in message:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Сначала одобрите план вопросов интервью",
            ) from None
        if "меньше трёх" in message:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="В плане интервью меньше трёх вопросов",
            ) from None
        if "не найдена" in message:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Вакансия не найдена"
            ) from None
        raise
    log.info("вакансия опубликована: %s, код %s", job_id, row["public_code"])
    return {
        **row,
        "deep_link": f"https://t.me/{settings().telegram_bot_username}"
                     f"?start=job_{row['public_code']}",
    }


@router.post("/{job_id}/close", status_code=status.HTTP_204_NO_CONTENT)
async def close(job_id: str, conn: ManagerConn, _: Manager):
    await store.close(conn, job_id)


# ── Отклики ───────────────────────────────────────────────────────────────────

@router.get("/{job_id}/applications")
async def job_applications(job_id: str, conn: ManagerConn, _: Manager):
    return {"applications": await store.applications(conn, job_id)}


@router.get("/{job_id}/applications/{interview_id}/transcript")
async def interview_transcript(
    job_id: str, interview_id: str, conn: ManagerConn, _: Manager
):
    """Полный транскрипт разговора. Клиника видит и саммари, и каждую реплику."""
    turns = await store.transcript(conn, interview_id)
    if not turns:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Транскрипт недоступен"
        )
    return {"turns": turns}


def _deep_link(job: dict) -> str | None:
    if job.get("status") != "active" or not job.get("public_code"):
        return None
    return (
        f"https://t.me/{settings().telegram_bot_username}?start=job_{job['public_code']}"
    )
