"""HTTP-слой вакансий и откликов.

Проверяем то, что легко сломать незаметно: порядок роутов, разграничение по
роли, перевод отказов базы в понятные коды ответа. Обращений к моделям здесь
нет — разбор текста и генерация вопросов проверяются прогоном
tools/demo_interview.py, потому что в тестах живой Azure делает набор
медленным и капризным.
"""
from __future__ import annotations

import httpx
import pytest

from app.web.main import app
from tests.conftest import TEST_PASSWORD, admin_execute, admin_fetch_one

pytestmark = pytest.mark.asyncio

QUESTIONS = [
    {"question": "Сколько лет вы работаете стоматологом-терапевтом?", "intent": "опыт"},
    {"question": "Готовы ли вы работать сменами?", "intent": "график"},
    {"question": "Какие у вас ожидания по оплате?", "intent": "оплата"},
]


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://app.ishmed.test"
    ) as c:
        yield c


async def _login(client, account: dict) -> None:
    r = await client.post(
        "/api/auth/login", json={"email": account["email"], "password": TEST_PASSWORD}
    )
    assert r.status_code == 200, r.text


async def _create_job(client) -> str:
    r = await client.post(
        "/api/jobs",
        json={
            "title": "TEST стоматолог-терапевт",
            "role_category": "doctor",
            "specialty": "dentist_therapist",
            "experience_min_months": 36,
            "required_skills": ["эндодонтия"],
            "schedule": ["shift"],
            "salary_min_uzs": 8_000_000,
            "salary_max_uzs": 14_000_000,
            "source_text": "Ищем стоматолога-терапевта, опыт от трёх лет, сменный график.",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ─────────────────────────── доступ ───────────────────────────

async def test_jobs_require_session(client):
    r = await client.get("/api/jobs")
    assert r.status_code == 401


async def test_dictionaries_route_wins_over_job_id(clinic_account, client):
    """/api/jobs/dictionaries не должен попасть в /api/jobs/{job_id}.

    Порядок объявления роутов в FastAPI значим, и такую ошибку видно только
    запросом: подключённые роутеры в app.routes не перечисляются.
    """
    await _login(client, clinic_account)
    r = await client.get("/api/jobs/dictionaries")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "roles" in body and "specialties" in body
    assert any(s["code"] == "dentist_therapist" for s in body["specialties"]), (
        "стоматологические специальности должны быть в справочнике"
    )


# ─────────────────────────── создание и правка ───────────────────────────

async def test_create_job_starts_as_draft(clinic_account, client):
    await _login(client, clinic_account)
    r = await client.post(
        "/api/jobs",
        json={"title": "TEST медсестра", "role_category": "nurse", "specialty": "ward_nurse"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "draft"
    assert body["interview_plan_status"] == "none"
    assert len(body["public_code"]) == 10


async def test_unknown_specialty_is_rejected(clinic_account, client):
    await _login(client, clinic_account)
    r = await client.post(
        "/api/jobs",
        json={"title": "TEST кто-то", "role_category": "nurse", "specialty": "ninja"},
    )
    assert r.status_code == 422
    assert "специальность" in r.json()["detail"].lower()


async def test_salary_range_is_validated(clinic_account, client):
    await _login(client, clinic_account)
    r = await client.post(
        "/api/jobs",
        json={
            "title": "TEST медсестра",
            "role_category": "nurse",
            "salary_min_uzs": 9_000_000,
            "salary_max_uzs": 5_000_000,
        },
    )
    assert r.status_code == 422
    assert "больше максимальной" in r.json()["detail"]


async def test_editing_job_drops_plan_approval(clinic_account, client):
    """Правка требований снимает одобрение плана.

    Иначе вакансию можно поменять после одобрения и опубликовать с вопросами
    не про неё.
    """
    await _login(client, clinic_account)
    job_id = await _create_job(client)
    await client.put(f"/api/jobs/{job_id}/plan", json={"questions": QUESTIONS})
    r = await client.post(f"/api/jobs/{job_id}/plan/approve")
    assert r.status_code == 200, r.text
    assert r.json()["interview_plan_status"] == "approved"

    r = await client.patch(f"/api/jobs/{job_id}", json={"experience_min_months": 60})
    assert r.status_code == 200, r.text
    assert r.json()["interview_plan_status"] == "draft", (
        "после правки вакансии план обязан вернуться на доработку"
    )


# ─────────────────────────── план и публикация ───────────────────────────

async def test_publish_without_plan_is_rejected(clinic_account, client):
    await _login(client, clinic_account)
    job_id = await _create_job(client)
    r = await client.post(f"/api/jobs/{job_id}/publish")
    assert r.status_code == 422
    assert "одобрите план" in r.json()["detail"]


async def test_approve_needs_three_questions(clinic_account, client):
    await _login(client, clinic_account)
    job_id = await _create_job(client)
    await client.put(f"/api/jobs/{job_id}/plan", json={"questions": QUESTIONS[:2]})
    r = await client.post(f"/api/jobs/{job_id}/plan/approve")
    assert r.status_code == 422
    assert "трёх вопросов" in r.json()["detail"]


async def test_publish_returns_deep_link(clinic_account, client):
    await _login(client, clinic_account)
    job_id = await _create_job(client)
    await client.put(f"/api/jobs/{job_id}/plan", json={"questions": QUESTIONS})
    await client.post(f"/api/jobs/{job_id}/plan/approve")

    r = await client.post(f"/api/jobs/{job_id}/publish")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["questions_count"] == 3
    assert body["deep_link"].startswith("https://t.me/")
    assert f"start=job_{body['public_code']}" in body["deep_link"]


async def test_saved_plan_is_never_auto_approved(clinic_account, client):
    """Сохранение плана всегда оставляет его на доработке.

    Одобрение — отдельное осознанное действие менеджера. Если сохранение
    одобряло бы само, слово «одобрено» ничего не значило бы.
    """
    await _login(client, clinic_account)
    job_id = await _create_job(client)
    r = await client.put(f"/api/jobs/{job_id}/plan", json={"questions": QUESTIONS})
    assert r.status_code == 200, r.text
    assert r.json()["plan_status"] == "draft"


async def test_qr_svg_for_published_job(clinic_account, client):
    await _login(client, clinic_account)
    job_id = await _create_job(client)
    await client.put(f"/api/jobs/{job_id}/plan", json={"questions": QUESTIONS})
    await client.post(f"/api/jobs/{job_id}/plan/approve")
    published = (await client.post(f"/api/jobs/{job_id}/publish")).json()

    r = await client.get(f"/api/jobs/{published['public_code']}/qr.svg")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/svg")
    assert b"<svg" in r.content


async def test_qr_denied_for_draft(clinic_account, client):
    await _login(client, clinic_account)
    job_id = await _create_job(client)
    detail = (await client.get(f"/api/jobs/{job_id}")).json()
    r = await client.get(f"/api/jobs/{detail['job']['public_code']}/qr.svg")
    assert r.status_code == 404, "у черновика не должно быть ссылки для кандидатов"


async def test_detail_hides_deep_link_until_published(clinic_account, client):
    await _login(client, clinic_account)
    job_id = await _create_job(client)
    r = await client.get(f"/api/jobs/{job_id}")
    assert r.status_code == 200, r.text
    assert r.json()["deep_link"] is None


# ─────────────────────────── отклики ───────────────────────────

async def test_applications_never_return_contact(clinic_account, client):
    """В списке откликов не должно быть телефона и username кандидата.

    Контакт открывается отдельным действием после принятия отклика — иначе
    обещание приватности не стоит ничего.
    """
    await _login(client, clinic_account)
    job_id = await _create_job(client)
    await client.put(f"/api/jobs/{job_id}/plan", json={"questions": QUESTIONS})
    await client.post(f"/api/jobs/{job_id}/plan/approve")
    await client.post(f"/api/jobs/{job_id}/publish")

    user = await admin_fetch_one(
        """
        INSERT INTO product.users (role, telegram_user_id, full_name, locale,
                                   consent_at, consent_version)
        VALUES ('medic', -900555, 'TEST кандидат', 'ru', now(), 'test')
        RETURNING id
        """
    )
    assert user is not None
    await admin_execute(
        "SELECT product.open_interview(%s, %s)", (user["id"], job_id)
    )

    r = await client.get(f"/api/applications?job_id={job_id}")
    assert r.status_code == 200, r.text
    apps = r.json()["applications"]
    assert len(apps) == 1
    for leaked in ("phone", "telegram_username", "contact_phone", "full_name"):
        assert leaked not in apps[0], f"контактное поле {leaked} не должно попадать в список"


async def test_contact_requires_accepted_application(clinic_account, client):
    await _login(client, clinic_account)
    job_id = await _create_job(client)
    await client.put(f"/api/jobs/{job_id}/plan", json={"questions": QUESTIONS})
    await client.post(f"/api/jobs/{job_id}/plan/approve")
    await client.post(f"/api/jobs/{job_id}/publish")

    user = await admin_fetch_one(
        """
        INSERT INTO product.users (role, telegram_user_id, full_name, locale,
                                   consent_at, consent_version)
        VALUES ('medic', -900556, 'TEST кандидат 2', 'ru', now(), 'test')
        RETURNING id
        """
    )
    assert user is not None
    opened = await admin_fetch_one(
        "SELECT * FROM product.open_interview(%s, %s)", (user["id"], job_id)
    )
    assert opened is not None

    r = await client.post(f"/api/applications/{opened['application_id']}/contact")
    assert r.status_code in (404, 409), (
        f"контакт до принятия отклика открываться не должен, получили {r.status_code}"
    )


async def test_application_status_change(clinic_account, client):
    await _login(client, clinic_account)
    job_id = await _create_job(client)
    await client.put(f"/api/jobs/{job_id}/plan", json={"questions": QUESTIONS})
    await client.post(f"/api/jobs/{job_id}/plan/approve")
    await client.post(f"/api/jobs/{job_id}/publish")

    user = await admin_fetch_one(
        """
        INSERT INTO product.users (role, telegram_user_id, full_name, locale,
                                   consent_at, consent_version)
        VALUES ('medic', -900557, 'TEST кандидат 3', 'ru', now(), 'test')
        RETURNING id
        """
    )
    assert user is not None
    opened = await admin_fetch_one(
        "SELECT * FROM product.open_interview(%s, %s)", (user["id"], job_id)
    )
    assert opened is not None

    r = await client.post(
        f"/api/applications/{opened['application_id']}/status", json={"status": "accepted"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"

    r = await client.post(
        f"/api/applications/{opened['application_id']}/status", json={"status": "нанят"}
    )
    assert r.status_code == 422, "произвольный статус приниматься не должен"
