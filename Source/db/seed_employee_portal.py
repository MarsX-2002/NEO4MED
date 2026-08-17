#!/usr/bin/env python
"""Тестовый сотрудник с доступом в портал: курсы и отзывы о себе.

Доступ выдаётся тем же путём, что настоящему сотруднику: менеджер создаёт
одноразовое приглашение, сотрудник по нему задаёт пароль. Специально не
вставляем строки напрямую — иначе демо проверяло бы не тот механизм, который
работает в бою.

Заодно у сотрудника появляется QR-цель и несколько отзывов пациентов: в портале
есть раздел «Отзывы обо мне», и пустой он ничего не показывает. Отзывы идут
через product.submit_review — ту же функцию, что вызывает публичная страница.

    ./.venv/bin/python db/seed_employee_portal.py
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sys
from pathlib import Path

import psycopg
from argon2 import PasswordHasher
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

EMPLOYEE_NAME = "Ахмедова Азиза Рустамовна"
EMPLOYEE_EMAIL = "azisa@ishmed.uz"
# Пароль демо-сотрудника. Не секрет: аккаунт синтетический и живёт
# только в демо-клинике.
EMPLOYEE_PASSWORD = "medik@2026"  # noqa: S105

ph = PasswordHasher()

# Отзывы пациентов о сотруднике: (оценка, что понравилось, что улучшить,
# комментарий, дней назад). Один низкий — обязателен: портал должен показывать
# и то, что человеку неприятно читать, иначе смысла в обратной связи нет.
REVIEWS: list[tuple[int, list[str], list[str], str | None, int]] = [
    (5, ["politeness", "clarity"], [], "Азиза объяснила всё по шагам, совсем не было страшно.", 1),
    (5, ["politeness", "cleanliness"], [], None, 3),
    (4, ["clarity"], ["waiting"], "Приём хороший, но ждала в коридоре минут двадцать.", 6),
    (5, ["politeness"], [], "Спасибо, укол вообще не почувствовала.", 9),
    (2, [], ["politeness", "waiting"], "Разговаривали со мной так, будто я мешаю. Неприятно.", 14),
]


def seed_reviews(cur, employee_id: str) -> tuple[str, int]:
    """QR-цель сотрудника и отзывы через штатную функцию приёма.

    Дату сдвигаем UPDATE'ом уже после вставки: submit_review не принимает
    created_at, и правильно — публичная форма не должна уметь ставить прошлое.
    Владельцу базы для демо это можно.
    """
    cur.execute("SELECT product.ensure_employee_review_target(%s)", (employee_id,))
    target_id = cur.fetchone()[0]
    cur.execute("SELECT slug FROM product.review_targets WHERE id = %s", (target_id,))
    slug = cur.fetchone()[0]

    cur.execute("SELECT count(*) FROM product.reviews WHERE target_id = %s", (target_id,))
    if cur.fetchone()[0]:
        print("  отзывы о сотруднике уже есть — не дублирую")
        return slug, 0

    for rating, good, bad, comment, days_ago in REVIEWS:
        # ip_hash не передаём: лимит «один отзыв с устройства в час» иначе
        # отклонит второй же вызов, и это правильное поведение функции.
        cur.execute(
            """
            SELECT product.submit_review(
                %s, %s::smallint, %s::text[], %s::text[], %s, NULL, false, 'ru', NULL
            )
            """,
            (slug, rating, good, bad, comment),
        )
        review_id = cur.fetchone()[0]
        cur.execute(
            "UPDATE product.reviews SET created_at = now() - (%s || ' days')::interval "
            "WHERE id = %s",
            (days_ago, review_id),
        )
    return slug, len(REVIEWS)


def main() -> int:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("нужен DATABASE_URL владельца")
        return 1

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM product.clinics WHERE is_demo ORDER BY created_at LIMIT 1")
        row = cur.fetchone()
        if row is None:
            print("демо-клиники нет: сначала db/seed_demo.py")
            return 1
        clinic_id = row[0]
        cur.execute("SELECT set_config('ishmed.clinic_id', %s, false)", (str(clinic_id),))

        cur.execute(
            "SELECT id, user_id FROM product.employees WHERE clinic_id = %s AND full_name = %s",
            (clinic_id, EMPLOYEE_NAME),
        )
        emp = cur.fetchone()
        if emp is None:
            print(f"сотрудника «{EMPLOYEE_NAME}» нет: сначала db/seed_dental.py")
            return 1
        employee_id, existing_user = emp

        if existing_user:
            # Пересоздаём доступ: пароль в демо должен быть предсказуемым, а
            # прошлую учётную запись надо убрать, иначе email займётся.
            cur.execute("DELETE FROM product.users WHERE id = %s", (existing_user,))
            print("  прежний доступ сотрудника удалён")
        cur.execute("DELETE FROM product.users WHERE lower(email) = %s", (EMPLOYEE_EMAIL,))

        # 1. Менеджер выдаёт приглашение. В ссылку уходит токен, в базу — хэш.
        token = secrets.token_urlsafe(24)
        cur.execute(
            """
            INSERT INTO product.employee_invites
                (clinic_id, employee_id, token_hash, expires_at)
            VALUES (%s, %s, %s, now() + interval '7 days')
            RETURNING id
            """,
            (clinic_id, employee_id, hashlib.sha256(token.encode()).hexdigest()),
        )
        invite_id = cur.fetchone()[0]

        # 2. Сотрудник открывает ссылку — видит, кто его пригласил.
        cur.execute(
            "SELECT full_name, clinic_name, unit_name, is_valid, reason "
            "FROM product.peek_employee_invite(%s)",
            (hashlib.sha256(token.encode()).hexdigest(),),
        )
        full_name, clinic_name, unit_name, is_valid, reason = cur.fetchone()
        if not is_valid:
            print(f"  приглашение недействительно: {reason}")
            return 1

        # 3. Задаёт пароль. В функцию уходит уже хэш: открытый пароль не должен
        #    попадать в базу даже транзитом через аргумент.
        cur.execute(
            "SELECT product.accept_employee_invite(%s, %s, %s)",
            (
                hashlib.sha256(token.encode()).hexdigest(),
                EMPLOYEE_EMAIL,
                ph.hash(EMPLOYEE_PASSWORD),
            ),
        )
        user_id = cur.fetchone()[0]

        # 4. Отзывы о нём. Нужны, чтобы в портале раздел «Отзывы обо мне» был
        #    не пустым: сотрудник заходит именно за обратной связью.
        slug, added = seed_reviews(cur, employee_id)
        conn.commit()

    print(f"  сотрудник:     {full_name}")
    print(f"  подразделение: {unit_name or '—'} ({clinic_name})")
    print(f"  приглашение:   {invite_id} (использовано)")
    print(f"  вход:          {EMPLOYEE_EMAIL} / {EMPLOYEE_PASSWORD}")
    print(f"  user_id:       {user_id}, роль employee")
    print(f"  QR-цель:       {slug}, отзывов добавлено {added}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
