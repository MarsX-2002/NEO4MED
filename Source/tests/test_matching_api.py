"""HTTP-слой подбора.

Проверяем то, что ломается незаметно: порядок роутов, разграничение по роли,
перевод отказов функций базы в понятные коды ответа. Сами правила подбора
проверяются в `test_matching.py` — здесь только то, что добавляет веб.

Отдельный файл, потому что `app.routes` в FastAPI не показывает подключённые
роутеры (`_IncludedRouter`), и регистрацию можно проверить только запросом. Если
роутер забудут подключить в `web/main.py`, узнать об этом должен тест, а не
демонстрация.
"""
from __future__ import annotations

from typing import ClassVar

import httpx
import pytest

from app import db
from app.web.main import app
from tests.conftest import TEST_PASSWORD, admin_fetch_one

pytestmark = pytest.mark.asyncio


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


# ── Доступ ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "path",
    ["/api/matching/jobs", "/api/matching/pool", "/api/matching/invitations"],
)
async def test_matching_requires_session(client, path: str):
    """Без сессии — 401, а не пустой список. Пустой список означал бы, что
    раздел работает и в нём никого нет."""
    r = await client.get(path)
    assert r.status_code == 401, r.text


async def test_pool_is_reachable_and_anonymous(client, clinic_account: dict):
    """Роутер подключён, и в ответе нет ни имени, ни телефона."""
    await _login(client, clinic_account)
    r = await client.get("/api/matching/pool")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"candidates", "total", "limit", "offset"}
    forbidden = {"full_name", "phone", "telegram_username", "user_id"}
    for card in body["candidates"]:
        assert forbidden.isdisjoint(card.keys()), sorted(set(card) & forbidden)


async def test_dictionaries_come_with_jobs(client, clinic_account: dict):
    """Справочники отдаются вместе со списком вакансий: фильтры нужны на том же
    экране, и второй запрос ради шести районов — лишний круг."""
    await _login(client, clinic_account)
    r = await client.get("/api/matching/jobs")
    assert r.status_code == 200, r.text
    dicts = r.json()["dictionaries"]
    assert set(dicts) == {"roles", "specialties", "districts", "schedules"}
    # Коды графика в кабинете должны совпадать со словарём базы: до 039 в API
    # был захардкожен `weekend`, которого в словаре нет.
    codes = {s["code"] for s in dicts["schedules"]}
    assert "weekend" not in codes
    assert {"day", "shift", "rotational"} <= codes


async def test_pool_validates_filter_ranges(client, clinic_account: dict):
    """Опыт в 900 месяцев — это 75 лет. Такой фильтр не запрос, а опечатка."""
    await _login(client, clinic_account)
    r = await client.get("/api/matching/pool?experience_min=900")
    assert r.status_code == 422, r.text


# ── Приглашения ───────────────────────────────────────────────────────────────

async def test_invite_on_unpublished_job_is_rejected(client, clinic_account: dict):
    """422 с внятным текстом, а не 500. Причина не в вежливости: менеджер должен
    понять, что делать дальше — опубликовать вакансию."""
    await _login(client, clinic_account)
    r = await client.post(
        "/api/jobs",
        json={
            "title": "TEST процедурная медсестра",
            "role_category": "nurse",
            "specialty": "procedural_nurse",
            "schedule": ["shift"],
            "source_text": "Ищем процедурную медсестру.",
        },
    )
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]

    # Кандидат в пуле нужен настоящий: проверяем отказ по вакансии, а не по
    # несуществующему человеку.
    cand = await admin_fetch_one(
        """
        WITH u AS (
            INSERT INTO product.users (role, telegram_user_id, locale, consent_at, consent_version)
            VALUES ('medic', -900123, 'ru', now(), 'test') RETURNING id
        ), p AS (
            INSERT INTO product.candidate_profiles
                (user_id, role_category, specialty, experience_months,
                 districts, schedule, status, source, self_filled_at)
            SELECT u.id, 'nurse', 'procedural_nurse', 48,
                   ARRAY['chilanzar'], ARRAY['shift'], 'active', 'manual', now()
            FROM u RETURNING id
        )
        SELECT (SELECT id FROM p)::text AS candidate_id
        """
    )
    assert cand is not None

    r = await client.post(
        "/api/matching/invitations",
        json={"job_id": job_id, "candidate_id": cand["candidate_id"]},
    )
    assert r.status_code == 422, r.text
    assert "опубликуйте" in r.json()["detail"].lower()


async def test_unknown_schedule_code_is_rejected(client, clinic_account: dict):
    """График проверяется по словарю базы, а не по списку в коде.

    Раньше здесь стоял захардкоженный набор, и он разошёлся со словарём в обе
    стороны: пропускал `weekend`, отвергал `day` и `rotational`. Вакансия с
    кодом вне словаря выглядит нормально, но подбор по графику её ни с кем не
    сведёт — у кандидата такого кода взяться негде.
    """
    await _login(client, clinic_account)
    r = await client.post(
        "/api/jobs",
        json={
            "title": "TEST вакансия с выходными",
            "role_category": "nurse",
            "schedule": ["weekend"],
            "source_text": "Работа по выходным.",
        },
    )
    assert r.status_code == 422, r.text
    assert "график" in r.json()["detail"].lower()


async def test_valid_dictionary_schedule_is_accepted(client, clinic_account: dict):
    """`day` есть в словаре и раньше отвергался — самый частый график в клиниках
    нельзя было указать вовсе."""
    await _login(client, clinic_account)
    r = await client.post(
        "/api/jobs",
        json={
            "title": "TEST дневная смена",
            "role_category": "nurse",
            "schedule": ["day", "rotational"],
            "source_text": "Дневной график, возможна вахта.",
        },
    )
    assert r.status_code == 201, r.text


async def test_patch_also_validates_schedule(client, clinic_account: dict):
    """Создание проверяло график, а PATCH — нет, и через него в вакансию
    попадал любой код."""
    await _login(client, clinic_account)
    r = await client.post(
        "/api/jobs",
        json={
            "title": "TEST правка графика",
            "role_category": "nurse",
            "schedule": ["shift"],
            "source_text": "Сменный график.",
        },
    )
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]

    r = await client.patch(f"/api/jobs/{job_id}", json={"schedule": ["каждый вторник"]})
    assert r.status_code == 422, r.text


async def test_recompute_returns_exclusion_summary(client, clinic_account: dict):
    """Пустой подбор без объяснения читается как поломка. Сводка обязана
    приходить вместе с результатом, а не собираться на фронте."""
    await _login(client, clinic_account)
    r = await client.post(
        "/api/jobs",
        json={
            "title": "TEST подбор",
            "role_category": "nurse",
            "specialty": "procedural_nurse",
            "experience_min_months": 24,
            "schedule": ["shift"],
            "source_text": "Процедурная медсестра, опыт от двух лет.",
        },
    )
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]

    r = await client.post(f"/api/matching/jobs/{job_id}/recompute", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {"matches", "excluded", "excluded_total", "strong", "wants_more_money"}
    assert isinstance(body["excluded"], dict)


async def test_recompute_of_missing_job_is_404(client, clinic_account: dict):
    await _login(client, clinic_account)
    r = await client.post(
        "/api/matching/jobs/00000000-0000-0000-0000-000000000000/recompute", json={}
    )
    assert r.status_code == 404, r.text


class FakeBot:
    """Двойник aiogram.Bot: приглашение не должно уходить в живой Telegram.

    Токен в тестах настоящий, и «отправить сообщение отрицательному chat_id»
    отличается от «написать живому человеку» одной опечаткой в фикстуре.
    """

    sent: ClassVar[list[dict]] = []

    class _Session:
        async def close(self) -> None:
            return None

    def __init__(self, *args, **kwargs):
        self.session = FakeBot._Session()

    async def send_message(self, chat_id, text, **kwargs):
        FakeBot.sent.append({"chat_id": chat_id, "text": text, **kwargs})


async def test_full_invite_path_and_contact_stays_closed(
    client, clinic_account: dict, monkeypatch
):
    """Сквозной путь по HTTP: вакансия → план → публикация → приглашение → контакт.

    Две фикстуры разом (`clinic_account` и `fixture_world`) использовать нельзя:
    каждая вызывает `_purge()` на входе и стирает данные другой. Поэтому мир
    собирается здесь, и заодно проверяется весь путь, а не одна ручка.
    """
    from app.services import notify

    FakeBot.sent = []
    monkeypatch.setattr(notify, "Bot", FakeBot)
    await _login(client, clinic_account)

    r = await client.post(
        "/api/jobs",
        json={
            "title": "TEST приглашение из подбора",
            "role_category": "nurse",
            "specialty": "procedural_nurse",
            "experience_min_months": 24,
            "schedule": ["shift"],
            "districts": ["chilanzar"],
            "salary_min_uzs": 4_000_000,
            "salary_max_uzs": 6_000_000,
            "source_text": "Процедурная медсестра, сменный график, Чиланзар.",
        },
    )
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]

    r = await client.put(
        f"/api/jobs/{job_id}/plan",
        json={"questions": [
            {"question": "Сколько лет вы работаете процедурной медсестрой?"},
            {"question": "Готовы ли вы работать сменами?"},
            {"question": "Какие у вас ожидания по оплате?"},
        ]},
    )
    assert r.status_code == 200, r.text
    assert (await client.post(f"/api/jobs/{job_id}/plan/approve", json={})).status_code == 200
    assert (await client.post(f"/api/jobs/{job_id}/publish", json={})).status_code == 200

    cand = await admin_fetch_one(
        """
        WITH u AS (
            INSERT INTO product.users
                (role, telegram_user_id, full_name, locale, consent_at, consent_version)
            VALUES ('medic', -900124, 'TEST Гулнора', 'ru', now(), 'test') RETURNING id
        ), p AS (
            INSERT INTO product.candidate_profiles
                (user_id, role_category, specialty, experience_months, skills, languages,
                 districts, schedule, salary_min_uzs, status, source, self_filled_at)
            SELECT u.id, 'nurse', 'procedural_nurse', 48, ARRAY['инъекции'],
                   ARRAY['uz','ru'], ARRAY['chilanzar'], ARRAY['shift'], 4500000,
                   'active', 'manual', now()
            FROM u RETURNING id
        ), c AS (
            INSERT INTO product.candidate_contacts (candidate_id, phone, telegram_username)
            SELECT p.id, '998901112233', 'test_pool_medic' FROM p RETURNING candidate_id
        )
        SELECT (SELECT id FROM p)::text AS candidate_id, (SELECT id FROM u) AS user_id
        """
    )
    assert cand is not None

    # Подбор находит её и объясняет, почему.
    r = await client.post(f"/api/matching/jobs/{job_id}/recompute", json={})
    assert r.status_code == 200, r.text
    matches = r.json()["matches"]
    assert any(m["candidate_id"] == cand["candidate_id"] for m in matches), r.text
    shown = next(m for m in matches if m["candidate_id"] == cand["candidate_id"])
    assert len(shown["reasons"]) >= 2
    assert "full_name" not in shown and "phone" not in shown

    r = await client.post(
        "/api/matching/invitations",
        json={"job_id": job_id, "candidate_id": cand["candidate_id"],
              "message": "Приходите поговорить"},
    )
    assert r.status_code == 201, r.text
    invitation_id = r.json()["invitation_id"]
    assert FakeBot.sent, "приглашение обязано доехать до Telegram"
    assert "Приходите поговорить" in FakeBot.sent[0]["text"]

    # Критерий A10: до accept контакт закрыт, и отказ объясняет, чего ждать.
    r = await client.post(f"/api/matching/invitations/{invitation_id}/contact", json={})
    assert r.status_code == 409, r.text
    assert "не принял" in r.json()["detail"].lower()

    # Критерий A11: после accept — открыт.
    await db.execute(
        "SELECT * FROM product.respond_invitation(%s, %s, true)",
        (invitation_id, cand["user_id"]),
    )
    r = await client.post(f"/api/matching/invitations/{invitation_id}/contact", json={})
    assert r.status_code == 200, r.text
    assert r.json()["phone"] == "998901112233"
