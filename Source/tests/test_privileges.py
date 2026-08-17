"""Границы прав роли ishmed_app.

Это фундамент под будущую гарантию по контактам (A10): если приложение ходит
в базу владельцем объектов, никакие проверки в коде ничего не гарантируют.
Тест закрепляет, что приложение работает урезанной ролью — и падает, если
кто-то однажды выдаст ей лишнее «чтобы заработало».
"""
from __future__ import annotations

import pytest

from app import db
from app.config import settings

pytestmark = pytest.mark.asyncio


async def test_app_connects_as_restricted_role():
    row = await db.fetch_one(
        "SELECT current_user AS role, (SELECT rolsuper FROM pg_roles WHERE rolname = current_user) AS is_super"
    )
    assert row is not None
    assert row["role"] == "ishmed_app", "приложение обязано ходить не под владельцем базы"
    assert row["is_super"] is False, "прикладная роль не может быть суперпользователем"


async def test_app_does_not_own_product_tables():
    row = await db.fetch_one(
        """
        SELECT count(*) AS owned
        FROM pg_tables
        WHERE schemaname IN ('product', 'ai')
          AND tableowner = current_user
        """
    )
    assert row is not None
    assert row["owned"] == 0, "владение таблицами обнуляет смысл ограничений прав"


@pytest.mark.parametrize(
    "sql, what",
    [
        ("CREATE TABLE product.should_not_exist_2 (x int)", "создание таблиц в product"),
        ("DELETE FROM product.users WHERE false", "удаление пользователей"),
        ("CREATE TABLE product.should_not_exist (x int)", "создание таблиц в product"),
    ],
)
async def test_forbidden_operations(sql: str, what: str):
    with pytest.raises(Exception) as exc:
        await db.execute(sql)
    assert "denied" in str(exc.value).lower(), f"должно быть запрещено: {what}"


async def test_allowed_reads():
    """Справочники читаются без контекста тенанта: они общие для всех клиник."""
    row = await db.fetch_one("SELECT count(*) AS n FROM product.role_categories")
    assert row is not None and row["n"] > 0, "справочник категорий должен читаться"


async def test_agent_schema_writable_for_checkpoints():
    """LangGraph создаёт свои таблицы сам, поэтому схема agent должна быть
    доступна приложению на запись — но только она."""
    await db.execute("CREATE TABLE IF NOT EXISTS agent.privilege_probe (x int)")
    await db.execute("DROP TABLE agent.privilege_probe")


async def test_dsn_uses_app_role():
    assert "ishmed_app" in settings().dsn, "в APP_DATABASE_URL должна быть прикладная роль"
