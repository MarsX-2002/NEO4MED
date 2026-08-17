"""Кабинет клиники: JSON API на FastAPI плюс раздача собранного React.

Разделение обязанностей:
  * весь бэкенд — Python. Извлечение полей и матчинг живут здесь и нигде больше,
    чтобы правила подбора не разъехались между двумя реализациями;
  * фронтенд — React, собирается в статику и в проде раздаётся nginx.
    FastAPI отдаёт её только при локальной разработке, если сборка есть.

Запуск (разработка):
    ./.venv/bin/uvicorn app.web.main:app --reload --port 8000
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.services import auth as auth_service
from app.web.api import applications as applications_api
from app.web.api import auth as auth_api
from app.web.api import courses as courses_api
from app.web.api import employees as employees_api
from app.web.api import jobs as jobs_api
from app.web.api import matching as matching_api
from app.web.api import portal as portal_api
from app.web.api import public as public_api
from app.web.api import reviews as reviews_api
from app.web.api import structure as structure_api

log = logging.getLogger("ishmed.web")

WEB_DIR = Path(__file__).resolve().parent
FRONTEND_DIST = WEB_DIR.parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    health = await db.healthcheck()
    log.info(
        "веб поднят: роль %s, PostgreSQL %s, пользователей %s",
        health.get("role"), health.get("pg_version"), health.get("users"),
    )
    # Просроченные сессии иначе копятся без предела: таблица растёт, а толку ноль.
    # Чистим на старте, а не по таймеру — сервис перезапускается при каждом
    # деплое, этого достаточно, и лишнего расписания не нужно.
    removed = await auth_service.purge_expired_sessions()
    if removed:
        log.info("удалено просроченных сессий: %s", removed)
    yield
    await db.close_pool()


app = FastAPI(
    title="IshMed — кабинет клиники",
    docs_url=None,      # публичная схема API на проде не нужна
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

app.include_router(auth_api.router)
app.include_router(structure_api.router)
app.include_router(employees_api.router)
app.include_router(jobs_api.router)
app.include_router(applications_api.router)
app.include_router(matching_api.router)
app.include_router(reviews_api.router)
app.include_router(courses_api.router)
# Портал сотрудника — единственный раздел, куда допущена роль employee.
app.include_router(portal_api.router)
# Публичная страница отзыва: подключается ДО SPA-заглушки, иначе /r/{slug}
# перехватит маршрутизация на клиенте и пациент увидит пустой кабинет.
app.include_router(public_api.router)


@app.get("/health")
async def health() -> JSONResponse:
    """Проверка живости для systemd и nginx. Наружу закрыта на уровне nginx."""
    try:
        h = await db.healthcheck()
        return JSONResponse({
            "status": "ok",
            "db_role": h.get("role"),
            "pg_version": h.get("pg_version"),
            "users": h.get("users"),
        })
    except Exception as e:
        log.exception("healthcheck упал")
        return JSONResponse({"status": "error", "detail": type(e).__name__}, status_code=503)


# ── Раздача фронтенда при локальной разработке ────────────────────────────────
# В проде этим занимается nginx: он быстрее и умеет кэшировать. Здесь — чтобы
# `make web` показывал приложение без второго процесса.
if (FRONTEND_DIST / "index.html").exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(FRONTEND_DIST / "assets")),
        name="assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str, request: Request):
        """Любой неизвестный путь отдаёт index.html: маршрутизация на клиенте.
        API отвечает раньше, потому что его роутеры зарегистрированы выше."""
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    @app.get("/", include_in_schema=False)
    async def no_frontend() -> JSONResponse:
        return JSONResponse(
            {
                "status": "api_only",
                "detail": "Фронтенд не собран. Выполните: make front-build",
            },
            status_code=503,
        )
