"""Своя карточка медика и приглашения — сторона бота.

Тонкие обёртки над функциями `product.*`, как в `services/interview.py`. Логика
живёт в базе не из любви к SQL: карточку правит человек через бота, а видимость
этой карточки другим клиникам — обещание из текста согласия. Проверять полноту и
писать событие в журнал должно одно место, а не каждый хендлер.

Контекста тенанта у бота нет, поэтому все функции здесь `SECURITY DEFINER` и
принимают `product.users.id`. Не `telegram_user_id`: оба bigint, ошибка типов не
возникнет, а внешний ключ упадёт где-то в глубине.
"""
from __future__ import annotations

import logging
from typing import Any

from app import db

log = logging.getLogger(__name__)

# Шаги формы в том порядке, в котором их проходит человек. Порядок — часть
# сценария: специальность нельзя спросить раньше категории роли, а телефон
# уместен последним, когда уже понятно, зачем он.
FORM_STEPS = ("role_category", "specialty", "experience_months", "districts", "schedule")

# Опыт кнопками, а не вводом числа. «3 года» набирается одним нажатием, а
# свободный ввод пришлось бы разбирать: люди пишут «3», «три года», «с 2021».
EXPERIENCE_CHOICES = (0, 12, 24, 36, 60, 84, 120, 180)

# Ожидание по зарплате — тоже кнопками, шагами по рынку Ташкента.
SALARY_CHOICES = (3_000_000, 4_000_000, 5_000_000, 6_000_000, 8_000_000, 12_000_000)


async def form(user_id: int) -> dict[str, Any] | None:
    """Состояние карточки. None — профиля ещё нет вовсе."""
    return await db.fetch_one("SELECT * FROM product.my_profile_form(%s)", (user_id,))


async def save(user_id: int, **fields: Any) -> str:
    """Частичная запись. Возвращает id профиля, создавая его при необходимости.

    Именованные аргументы совпадают с колонками: role_category, specialty,
    experience_months, skills, languages, districts, schedule, salary_min_uzs,
    credential_claims.
    """
    row = await db.fetch_one(
        """
        SELECT product.save_my_profile(
            %(user_id)s, %(role_category)s, %(specialty)s, %(experience_months)s,
            %(skills)s::text[], %(languages)s::text[], %(districts)s::text[],
            %(schedule)s::text[], %(salary_min_uzs)s, %(credential_claims)s::text[]
        ) AS candidate_id
        """,
        {
            "user_id": user_id,
            "role_category": fields.get("role_category"),
            "specialty": fields.get("specialty"),
            "experience_months": fields.get("experience_months"),
            "skills": fields.get("skills"),
            "languages": fields.get("languages"),
            "districts": fields.get("districts"),
            "schedule": fields.get("schedule"),
            "salary_min_uzs": fields.get("salary_min_uzs"),
            "credential_claims": fields.get("credential_claims"),
        },
    )
    assert row is not None
    return str(row["candidate_id"])


async def publish(user_id: int) -> dict[str, Any]:
    """Выводит карточку в общий поиск клиник.

    Неполную не выводит: в ответе `published=false` и `missing` — список того,
    чего не хватает. Отказ без объяснения человек читает как поломку.
    """
    row = await db.fetch_one("SELECT * FROM product.publish_my_profile(%s)", (user_id,))
    assert row is not None
    return row


async def hide(user_id: int) -> None:
    """Убирает из поиска, ничего не стирая. Удаление — это `users.forget`."""
    await db.execute("SELECT product.hide_my_profile(%s)", (user_id,))


def next_step(card: dict[str, Any] | None) -> str | None:
    """Первый незаполненный шаг формы. None — карточка готова к публикации.

    Шаг выводится из данных, а не хранится: состояние формы это сама строка в
    базе. Бота можно перезапустить посреди анкеты, человек продолжит там же —
    ровно как в интервью.
    """
    if card is None:
        return FORM_STEPS[0]
    for step in FORM_STEPS:
        value = card.get(step)
        if isinstance(value, (list, tuple)):
            if not value:
                return step
        elif value is None:
            return step
    return None


# ── Приглашения ───────────────────────────────────────────────────────────────

async def invitations(user_id: int) -> list[dict[str, Any]]:
    return await db.fetch_all("SELECT * FROM product.my_invitations(%s)", (user_id,))


async def respond(invitation_id: str, user_id: int, *, accept: bool) -> dict[str, Any]:
    """Ответ на приглашение. Только из статуса sent — проверяет база."""
    row = await db.fetch_one(
        "SELECT * FROM product.respond_invitation(%s, %s, %s)",
        (invitation_id, user_id, accept),
    )
    assert row is not None
    return row


class AlreadyAnswered(Exception):
    """На приглашение уже отвечали. Переигрывать нельзя: клиника могла увидеть
    контакт."""


async def respond_safely(invitation_id: str, user_id: int, *, accept: bool) -> dict[str, Any]:
    try:
        return await respond(invitation_id, user_id, accept=accept)
    except Exception as e:
        if "уже отвечено" in str(e):
            raise AlreadyAnswered(str(e)) from None
        raise


# ── Справочники ───────────────────────────────────────────────────────────────
# Кэшируем в процессе: словари пополняются только миграциями, а форма ходит по
# ним на каждое нажатие кнопки.
_DICTS: dict[str, list[dict[str, Any]]] | None = None


async def dictionaries() -> dict[str, list[dict[str, Any]]]:
    global _DICTS
    if _DICTS is None:
        _DICTS = {
            "roles": await db.fetch_all(
                "SELECT code, name_ru, name_uz FROM product.role_categories ORDER BY sort, code"
            ),
            "specialties": await db.fetch_all(
                "SELECT code, role_category, name_ru, name_uz FROM product.specialties "
                "ORDER BY role_category, name_ru"
            ),
            "districts": await db.fetch_all(
                "SELECT code, name_ru, name_uz FROM product.districts ORDER BY name_ru"
            ),
            "schedules": await db.fetch_all(
                "SELECT code, name_ru, name_uz FROM product.schedule_kinds ORDER BY code"
            ),
        }
    return _DICTS


def dict_name(row: dict[str, Any], locale: str) -> str:
    return str(row.get(f"name_{locale}") or row.get("name_ru") or row.get("code"))


async def names_for(kind: str, codes: list[str] | None, locale: str) -> list[str]:
    """Человекочитаемые названия по кодам. Порядок словаря, не порядок кодов:
    список районов должен читаться одинаково при каждом показе."""
    if not codes:
        return []
    table = (await dictionaries())[kind]
    wanted = set(codes)
    return [dict_name(r, locale) for r in table if r["code"] in wanted]
