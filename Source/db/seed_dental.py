#!/usr/bin/env python
"""Демо-структура стоматологии: два филиала, этажи, отделения, кабинеты.

Нужна, чтобы дерево в кабинете было видно на реальном масштабе, а не на трёх
узлах: у клиники будет двадцать-сто подразделений, и интерфейс должен это
переживать. Заодно появляются сотрудники, к которым можно выдавать QR.

Работает под ВЛАДЕЛЬЦЕМ базы: RLS скрывает от прикладной роли всё, для чего
не выставлен контекст тенанта.

    ./.venv/bin/python db/seed_dental.py [--reset-structure]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Структура настоящей стоматологии: филиал → этаж → отделение → кабинет.
# Этаж — такой же узел, как отделение, просто уровнем выше: у разных клиник
# вложенность разная, и жёсткие уровни пришлось бы ломать на второй клинике.
TREE: dict[str, dict[str, dict[str, list[str]]]] = {
    "Филиал на Чиланзаре": {
        "1 этаж": {
            "Регистратура": [],
            "Терапия": ["Кабинет 101", "Кабинет 102", "Кабинет 103"],
            "Гигиена и профилактика": ["Кабинет 104"],
        },
        "2 этаж": {
            "Ортодонтия": ["Кабинет 201", "Кабинет 202"],
            "Хирургия": ["Операционная 203", "Кабинет 204"],
            "Имплантология": ["Кабинет 205"],
        },
        "Диагностика": {
            "Рентген-кабинет": [],
            "КТ": [],
        },
    },
    "Филиал на Юнусабаде": {
        "1 этаж": {
            "Регистратура": [],
            "Терапия": ["Кабинет 1", "Кабинет 2"],
            "Детская стоматология": ["Кабинет 3", "Кабинет 4"],
        },
        "2 этаж": {
            "Ортопедия": ["Кабинет 5"],
            "Зуботехническая лаборатория": [],
        },
    },
}

DISTRICTS = {"Филиал на Чиланзаре": "chilanzar", "Филиал на Юнусабаде": "yunusabad"}

# Сотрудники: подразделение → (ФИО, категория, специальность)
STAFF = [
    ("Терапия", "Ахмедова Азиза Рустамовна", "nurse", "general_nurse"),
    ("Терапия", "Юсупов Бекзод Алишерович", "doctor", "doctor_any"),
    ("Ортодонтия", "Каримова Нилуфар Шухратовна", "doctor", "doctor_any"),
    ("Ортодонтия", "Сафарова Дилноза Икромовна", "nurse", "procedural_nurse"),
    ("Хирургия", "Толипов Жасур Кахрамонович", "doctor", "doctor_any"),
    ("Хирургия", "Мирзаева Гулнора Абдуллаевна", "nurse", "operating_nurse"),
    ("Детская стоматология", "Рахимова Шахло Батировна", "doctor", "doctor_any"),
    ("Гигиена и профилактика", "Исмаилова Феруза Тохировна", "nurse", "general_nurse"),
    ("Рентген-кабинет", "Назаров Улугбек Фарходович", "diagnostics", "xray"),
    ("Регистратура", "Хамидова Мадина Санжаровна", "junior", "orderly"),
]


def clinic_id(cur) -> str:
    cur.execute("SELECT id FROM product.clinics WHERE is_demo ORDER BY created_at LIMIT 1")
    row = cur.fetchone()
    if row is None:
        raise SystemExit("демо-клиники нет — сначала ./.venv/bin/python db/seed_demo.py")
    return row[0]


def reset_structure(cur, cid: str) -> None:
    """Сносит структуру и сотрудников, но НЕ отзывы: они привязаны к целям QR,
    и терять оценки пациентов при пересборке демо нельзя."""
    cur.execute(
        "SELECT count(*) FROM product.reviews WHERE clinic_id = %s", (cid,)
    )
    reviews = cur.fetchone()[0]
    if reviews:
        print(f"  внимание: у клиники {reviews} отзывов, они привязаны к QR-целям")
        print("  структуру с отзывами не сношу — используйте другую клинику для чистого демо")
        return
    cur.execute("DELETE FROM product.employees WHERE clinic_id = %s", (cid,))
    cur.execute("DELETE FROM product.clinic_units WHERE clinic_id = %s", (cid,))
    print("  структура и сотрудники очищены")


def build(cur, cid: str) -> dict[str, str]:
    """Создаёт дерево и возвращает карту «название отделения → id»."""
    by_name: dict[str, str] = {}

    def add(name: str, parent: str | None, district: str | None = None) -> str:
        cur.execute(
            """
            INSERT INTO product.clinic_units (clinic_id, parent_id, name, district)
            VALUES (%s, %s, %s, %s)
            -- Индекс уникальности объявлен по выражению lower(name), поэтому
            -- и ON CONFLICT должен ссылаться ровно на него, иначе Postgres
            -- не найдёт подходящее ограничение.
            ON CONFLICT (clinic_id, parent_id, lower(name)) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
            """,
            (cid, parent, name, district),
        )
        node_id = cur.fetchone()[0]
        by_name.setdefault(name, node_id)
        return node_id

    for branch, floors in TREE.items():
        branch_id = add(branch, None, DISTRICTS.get(branch))
        for floor, departments in floors.items():
            floor_id = add(floor, branch_id)
            for dept, rooms in departments.items():
                dept_id = add(dept, floor_id)
                for room in rooms:
                    add(room, dept_id)
    return by_name


def hire(cur, cid: str, by_name: dict[str, str]) -> int:
    added = 0
    for unit_name, full_name, role, specialty in STAFF:
        unit_id = by_name.get(unit_name)
        cur.execute(
            "SELECT 1 FROM product.employees WHERE clinic_id = %s AND full_name = %s",
            (cid, full_name),
        )
        if cur.fetchone():
            continue
        cur.execute(
            """
            INSERT INTO product.employees
                (clinic_id, unit_id, full_name, role_category, specialty, hired_at, status)
            VALUES (%s, %s, %s, %s, %s, current_date - (random() * 900)::int, 'active')
            """,
            (cid, unit_id, full_name, role, specialty),
        )
        added += 1
    return added


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset-structure", action="store_true")
    args = ap.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("нужен DATABASE_URL владельца")
        return 1

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cid = clinic_id(cur)
        if args.reset_structure:
            reset_structure(cur, cid)
        by_name = build(cur, cid)
        added = hire(cur, cid, by_name)
        conn.commit()

        cur.execute("SELECT count(*) FROM product.clinic_units WHERE clinic_id = %s", (cid,))
        units = cur.fetchone()[0]
        cur.execute(
            "SELECT count(*) FROM product.employees WHERE clinic_id = %s AND status <> 'dismissed'",
            (cid,),
        )
        staff = cur.fetchone()[0]

    print(f"  узлов структуры: {units}")
    print(f"  сотрудников: {staff} (добавлено {added})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
