"""HTTP-слой кабинета.

Аудит показал реальный пробел: тесты проверяли сервисный слой
(auth.authenticate), но не сами роуты. Значит сломанная регистрация роутера,
неверная зависимость или потерянный флаг cookie прошли бы мимо тестов —
именно так однажды и получилось, когда я решил, что роутер не подключился,
хотя дело было в способе проверки.

Здесь ходим по приложению как браузер: через ASGI, с cookie, без сети.
"""
from __future__ import annotations

import httpx
import pytest

from app.web.main import app
from tests.conftest import TEST_PASSWORD, admin_execute

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client():
    """Клиент поверх ASGI: без сокета и без запущенного сервера.

    lifespan не поднимаем: он лезет в базу за healthcheck и чистит сессии,
    а тестам нужны только роуты. Пул подключений открывается сам при первом
    обращении.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://app.ishmed.test"
    ) as c:
        yield c


async def test_me_requires_session(client):
    r = await client.get("/api/auth/me")
    assert r.status_code == 401
    assert "detail" in r.json()


async def test_login_sets_httponly_cookie(clinic_account, client):
    a = clinic_account
    r = await client.post(
        "/api/auth/login", json={"email": a["email"], "password": TEST_PASSWORD}
    )
    assert r.status_code == 200, r.text

    body = r.json()
    assert body["email"] == a["email"]
    assert body["clinic_id"] == a["clinic_id"]
    assert body["clinic_name"] == "TEST login clinic"

    raw = r.headers.get("set-cookie", "")
    assert "ishmed_session=" in raw
    assert "HttpOnly" in raw, "токен сессии не должен быть доступен из JavaScript"
    assert "SameSite=lax" in raw or "samesite=lax" in raw.lower()
    # Токена в теле ответа быть не должно: он только в cookie.
    assert "ishmed_session" not in r.text


async def test_login_then_me_works(clinic_account, client):
    a = clinic_account
    await client.post("/api/auth/login", json={"email": a["email"], "password": TEST_PASSWORD})

    r = await client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == a["email"]


async def test_logout_kills_session(clinic_account, client):
    a = clinic_account
    await client.post("/api/auth/login", json={"email": a["email"], "password": TEST_PASSWORD})
    assert (await client.get("/api/auth/me")).status_code == 200

    r = await client.post("/api/auth/logout")
    assert r.status_code == 204

    assert (await client.get("/api/auth/me")).status_code == 401


async def test_wrong_password_returns_401_with_generic_detail(clinic_account, client):
    r = await client.post(
        "/api/auth/login", json={"email": clinic_account["email"], "password": "nope"}
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "Неверный email или пароль"
    assert "set-cookie" not in {k.lower() for k in r.headers}


async def test_lockout_returns_retry_after(clinic_account, client):
    """Клиенту нужно знать, что ждать, а не гадать. Заголовок Retry-After —
    единственный случай, когда мы говорим больше обычного отказа."""
    a = clinic_account
    for _ in range(9):
        await client.post("/api/auth/login", json={"email": a["email"], "password": "bad"})

    r = await client.post("/api/auth/login", json={"email": a["email"], "password": "bad"})
    assert r.status_code == 401
    assert "Слишком много" in r.json()["detail"]
    assert int(r.headers["retry-after"]) > 0


async def test_malformed_payload_is_422(client):
    r = await client.post("/api/auth/login", json={"email": "не-email", "password": "x"})
    assert r.status_code == 422, "валидация схемы должна отсекать мусор до обращения к базе"


async def test_blocked_user_loses_access_immediately(clinic_account, client):
    """Блокировка сотрудника должна действовать на текущую сессию, а не после
    её истечения."""
    a = clinic_account
    await client.post("/api/auth/login", json={"email": a["email"], "password": TEST_PASSWORD})
    assert (await client.get("/api/auth/me")).status_code == 200

    await admin_execute("UPDATE product.users SET is_blocked = true WHERE id = %s", (a["user_id"],))
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_api_unknown_path_is_404_not_spa(client):
    """Неизвестный путь под /api не должен подменяться index.html: иначе
    клиент получит HTML вместо ошибки и не поймёт, что произошло."""
    r = await client.get("/api/nothing-here")
    assert r.status_code == 404
    assert "text/html" not in r.headers.get("content-type", "")
