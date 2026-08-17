#!/usr/bin/env python
"""Демо-данные клиники: тенант, владелец с паролем, структура и вакансия.

Работает под ВЛАДЕЛЬЦЕМ базы (DATABASE_URL), а не под ishmed_app: RLS
намеренно скрывает от прикладной роли всё, для чего не выставлен контекст
тенанта, и засеять данные её правами невозможно. Это ожидаемо, а не помеха.

Пароль берётся из DEMO_CLINIC_PASSWORD и в базу попадает только argon2-хэшем.

    ./.venv/bin/python db/seed_demo.py [--reset]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from argon2 import PasswordHasher
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DEMO_CLINIC_NAME = os.environ.get("DEMO_CLINIC_NAME", "Demo Clinic Tashkent")
DEMO_EMAIL = os.environ["DEMO_CLINIC_EMAIL"]
DEMO_PASSWORD = os.environ["DEMO_CLINIC_PASSWORD"]

ph = PasswordHasher()


def reset(cur) -> None:
    """Сносит ТОЛЬКО продуктовые данные. Импортированный каталог raw/core/ai
    не трогается — на нём держится демонстрация поиска (критерий A13)."""
    # Таблицы перечислены вручную, а не через CASCADE от clinics: так сразу
    # видно, если появится новая таблица, о которой сброс не знает.
    cur.execute(
        """
        TRUNCATE product.review_attachments, product.reviews, product.review_targets,
                 product.course_attempts, product.course_assignments,
                 product.course_options, product.course_questions,
                 product.course_lessons, product.courses,
                 product.employee_invites, product.employees,
                 product.kb_chunks, product.kb_documents,
                 product.consent_events, product.applications, product.invitations,
                 product.matches,
                 product.candidate_contacts, product.candidate_profiles,
                 product.intake_sessions, product.jobs, product.staff_positions,
                 product.clinic_units, product.clinic_members, product.sessions,
                 product.user_credentials, product.clinics
        RESTART IDENTITY CASCADE
        """
    )
    # Пользователей-медиков оставляем: у них зафиксировано согласие, и стирать
    # его втихую нельзя. Сбрасываем только сотрудников клиник — и менеджеров,
    # и обучающихся: их аккаунт без клиники бесполезен, а email иначе останется
    # занятым и повторный сид портала упрётся в конфликт.
    cur.execute("DELETE FROM product.users WHERE role IN ('clinic_user', 'employee')")
    print("  продуктовые данные очищены (каталог raw/core/ai не тронут)")


def seed(cur) -> dict:
    cur.execute(
        """
        INSERT INTO product.clinics (name, city, address, is_demo)
        VALUES (%s, 'tashkent', 'Ташкент, Чиланзарский район', true)
        RETURNING id
        """,
        (DEMO_CLINIC_NAME,),
    )
    clinic_id = cur.fetchone()[0]

    cur.execute(
        """
        INSERT INTO product.users (role, email, locale, full_name, consent_at, consent_version)
        VALUES ('clinic_user', %s, 'ru', 'Демо-клиника, администратор', now(), %s)
        ON CONFLICT (email) DO UPDATE SET full_name = EXCLUDED.full_name
        RETURNING id
        """,
        (DEMO_EMAIL, os.environ.get("CONSENT_VERSION", "2026-08-16")),
    )
    user_id = cur.fetchone()[0]

    cur.execute(
        """
        INSERT INTO product.user_credentials (user_id, password_hash)
        VALUES (%s, %s)
        ON CONFLICT (user_id) DO UPDATE
            SET password_hash = EXCLUDED.password_hash, password_set_at = now(),
                failed_attempts = 0, locked_until = NULL
        """,
        (user_id, ph.hash(DEMO_PASSWORD)),
    )

    cur.execute(
        """
        INSERT INTO product.clinic_members (clinic_id, user_id, role)
        VALUES (%s, %s, 'owner') ON CONFLICT DO NOTHING
        """,
        (clinic_id, user_id),
    )

    cur.execute(
        """
        INSERT INTO product.clinic_units (clinic_id, name, district)
        VALUES (%s, 'Поликлиника на Чиланзаре', 'chilanzar')
        RETURNING id
        """,
        (clinic_id,),
    )
    unit_id = cur.fetchone()[0]

    # Штатная единица с двумя ставками, одна занята: на ней видно, что кнопки
    # «вакантно / занято» работают со счётчиком, а не с булевым флагом.
    cur.execute(
        """
        INSERT INTO product.staff_positions
            (clinic_id, unit_id, title, role_category, specialty, seats, seats_filled)
        VALUES (%s, %s, 'Процедурная медсестра', 'nurse', 'procedural_nurse', 2, 1)
        RETURNING id, seats_open
        """,
        (clinic_id, unit_id),
    )
    position_id, seats_open = cur.fetchone()

    cur.execute(
        """
        INSERT INTO product.jobs
            (clinic_id, staff_position_id, title, role_category, specialty,
             experience_min_months, required_skills, required_languages,
             districts, schedule, salary_min_uzs, salary_max_uzs,
             status, source, source_text, created_by)
        VALUES (%s, %s, 'Процедурная медсестра', 'nurse', 'procedural_nurse',
                24, ARRAY['injections','iv_therapy'], ARRAY['uz'],
                ARRAY['chilanzar'], ARRAY['shift'], 4000000, 6000000,
                'active', 'manual', %s, %s)
        RETURNING id
        """,
        (
            clinic_id,
            position_id,
            "Требуется процедурная медсестра в поликлинику на Чиланзаре. "
            "Опыт от 2 лет, сменный график, зарплата 4-6 млн сум. "
            "Узбекский обязателен, русский желателен.",
            user_id,
        ),
    )
    job_id = cur.fetchone()[0]

    return {
        "clinic_id": clinic_id,
        "user_id": user_id,
        "unit_id": unit_id,
        "position_id": position_id,
        "seats_open": seats_open,
        "job_id": job_id,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true",
                    help="очистить продуктовые данные перед засевом")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("нужен DATABASE_URL владельца: RLS не даст засеять данные правами ishmed_app")
        return 1

    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        if args.reset:
            reset(cur)
        ids = seed(cur)
        conn.commit()

    print(f"  клиника:        {ids['clinic_id']}")
    print(f"  владелец:       {DEMO_EMAIL} (пароль из DEMO_CLINIC_PASSWORD, хэш argon2)")
    print(f"  подразделение:  {ids['unit_id']}")
    print(f"  штатная единица: 2 ставки, 1 занята, открыто {ids['seats_open']}")
    print(f"  вакансия:       {ids['job_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
