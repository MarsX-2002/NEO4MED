#!/usr/bin/env python
"""Синтетические карточки медиков для демонстрации подбора.

Пул наполняют живые люди через бота, а на репетиции демо их ещё нет. Без
карточек раздел «Подбор» показывает честное пустое состояние — правильное,
но не то, что нужно показывать залу.

Данные ЯВНО синтетические, и это требование, а не осторожность: имена начинаются
с «Демо», telegram_user_id отрицательные (у настоящих Telegram ID всегда
положительные, коллизия исключена), телефоны из диапазона 998900000xxx. Спутать
их с реальными людьми нельзя ни в кабинете, ни в базе.

Работает под ВЛАДЕЛЬЦЕМ базы (DATABASE_URL): профиль пишется от имени человека,
а `p_candidates_own` в WITH CHECK пускает только владельца профиля. Прикладной
ролью такое не засеять, и это ожидаемо.

    ./.venv/bin/python db/seed_pool.py [--reset]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Отрицательные id, отдельный диапазон от тестовых (-900_000): демо-данные и
# тестовые данные не должны стирать друг друга.
TG_BASE = -800_000
NAME_PREFIX = "Демо"

# Карточки подобраны так, чтобы подбор было на чём показать:
#   * есть точные попадания и есть «рядом» — иначе все матчи одинаковы;
#   * есть тот, кто просит больше вилки, — показать корзину «просят больше»;
#   * есть врачи и лаборанты — показать, что жёсткий фильтр по роли работает;
#   * есть карточки без зарплаты и без навыков — показать пробелы.
#
# Поля ровно те, что заполняет форма в боте: коды словарей, а не фразы.
POOL: tuple[dict, ...] = (
    # ── Процедурные медсёстры: ядро демонстрации ──────────────────────────────
    {"name": "Дилноза", "role": "nurse", "spec": "procedural_nurse", "exp": 60,
     "skills": ["внутривенные инъекции", "капельницы", "забор крови"],
     "langs": ["uz", "ru"], "districts": ["chilanzar", "uchtepa"],
     "schedule": ["shift", "day"], "salary": 4_500_000,
     "creds": ["диплом медколледжа"]},
    {"name": "Гулнора", "role": "nurse", "spec": "procedural_nurse", "exp": 36,
     "skills": ["инъекции", "перевязки"], "langs": ["uz"],
     "districts": ["chilanzar"], "schedule": ["shift"], "salary": 4_000_000,
     "creds": ["диплом медколледжа"]},
    # Просит заметно больше вилки: попадёт в корзину «просят больше».
    {"name": "Зулфия", "role": "nurse", "spec": "procedural_nurse", "exp": 132,
     "skills": ["внутривенные инъекции", "капельницы", "работа с портами"],
     "langs": ["uz", "ru", "en"], "districts": ["yunusabad", "mirzo_ulugbek"],
     "schedule": ["day"], "salary": 9_000_000,
     "creds": ["диплом медколледжа", "сертификат по инфузионной терапии"]},
    # Опыта меньше требуемого, но не втрое: покажет пробел «опыта меньше».
    {"name": "Нилуфар", "role": "nurse", "spec": "procedural_nurse", "exp": 18,
     "skills": ["инъекции"], "langs": ["uz", "ru"], "districts": ["sergeli"],
     "schedule": ["shift", "night"], "salary": 3_500_000, "creds": []},
    # Сумму не назвала: покажет пробел «не назвал ожидания».
    {"name": "Шахзода", "role": "nurse", "spec": "procedural_nurse", "exp": 48,
     "skills": ["капельницы", "забор крови"], "langs": ["uz", "ru"],
     "districts": ["chilanzar", "yakkasaray"], "schedule": ["shift"],
     "salary": None, "creds": ["диплом медколледжа"]},

    # ── Другие медсёстры: рядом, но не то ─────────────────────────────────────
    {"name": "Мадина", "role": "nurse", "spec": "ward_nurse", "exp": 72,
     "skills": ["уход за пациентами", "инъекции"], "langs": ["uz", "ru"],
     "districts": ["chilanzar"], "schedule": ["shift"], "salary": 4_200_000,
     "creds": ["диплом медколледжа"]},
    {"name": "Феруза", "role": "nurse", "spec": "operating_nurse", "exp": 96,
     "skills": ["ассистирование на операциях", "стерилизация"],
     "langs": ["uz", "ru"], "districts": ["mirabad", "yakkasaray"],
     "schedule": ["shift", "night"], "salary": 6_500_000,
     "creds": ["диплом медколледжа", "сертификат операционной сестры"]},
    {"name": "Сабина", "role": "nurse", "spec": "anesthesia_nurse", "exp": 84,
     "skills": ["анестезиологическое пособие", "мониторинг"],
     "langs": ["ru"], "districts": ["mirzo_ulugbek"], "schedule": ["shift"],
     "salary": 7_000_000, "creds": ["диплом медколледжа"]},
    {"name": "Камола", "role": "nurse", "spec": "reception_nurse", "exp": 24,
     "skills": ["первичный осмотр", "оформление документов"],
     "langs": ["uz", "ru"], "districts": ["yunusabad"], "schedule": ["day"],
     "salary": 3_800_000, "creds": []},
    {"name": "Азиза", "role": "nurse", "spec": "general_nurse", "exp": 12,
     "skills": ["инъекции"], "langs": ["uz"], "districts": ["uchtepa", "sergeli"],
     "schedule": ["part_time"], "salary": 3_000_000, "creds": []},
    # Стоматологическая ассистентка: под стоматологию из seed-dental.
    {"name": "Хилола", "role": "nurse", "spec": "dental_assistant", "exp": 42,
     "skills": ["ассистирование в четыре руки", "стерилизация инструментов"],
     "langs": ["uz", "ru"], "districts": ["chilanzar", "yakkasaray"],
     "schedule": ["shift", "full_time"], "salary": 5_000_000,
     "creds": ["диплом медколледжа"]},
    {"name": "Севара", "role": "nurse", "spec": "dental_hygienist", "exp": 30,
     "skills": ["профессиональная гигиена", "снятие налёта"],
     "langs": ["uz", "ru"], "districts": ["mirabad"], "schedule": ["full_time"],
     "salary": 5_500_000, "creds": ["диплом медколледжа"]},

    # ── Лаборанты и диагностика ───────────────────────────────────────────────
    {"name": "Отабек", "role": "lab", "spec": "lab_technician", "exp": 66,
     "skills": ["биохимия", "гематология", "работа с анализаторами"],
     "langs": ["uz", "ru"], "districts": ["chilanzar", "sergeli"],
     "schedule": ["day"], "salary": 5_000_000, "creds": ["диплом медколледжа"]},
    {"name": "Малика", "role": "lab", "spec": "lab_assistant", "exp": 15,
     "skills": ["забор крови"], "langs": ["uz"], "districts": ["uchtepa"],
     "schedule": ["day", "part_time"], "salary": 3_200_000, "creds": []},
    {"name": "Рустам", "role": "diagnostics", "spec": "ultrasound", "exp": 108,
     "skills": ["УЗИ брюшной полости", "УЗИ щитовидной железы"],
     "langs": ["uz", "ru"], "districts": ["yunusabad", "mirzo_ulugbek"],
     "schedule": ["day"], "salary": 8_000_000,
     "creds": ["диплом", "сертификат по УЗИ"]},
    {"name": "Дилшод", "role": "diagnostics", "spec": "xray", "exp": 54,
     "skills": ["рентгенография", "радиационная безопасность"],
     "langs": ["uz", "ru"], "districts": ["bektemir", "yashnabad"],
     "schedule": ["shift"], "salary": 6_000_000, "creds": ["диплом"]},

    # ── Врачи: проверяют, что жёсткий фильтр по роли работает ─────────────────
    {"name": "Бахтиёр", "role": "doctor", "spec": "dentist_therapist", "exp": 120,
     "skills": ["лечение каналов", "реставрации"], "langs": ["uz", "ru"],
     "districts": ["chilanzar"], "schedule": ["shift", "full_time"],
     "salary": 12_000_000, "creds": ["диплом", "действующий сертификат"]},
    {"name": "Нодира", "role": "doctor", "spec": "pediatric_dentist", "exp": 78,
     "skills": ["лечение детей", "профилактика"], "langs": ["uz", "ru"],
     "districts": ["chilanzar", "yunusabad"], "schedule": ["shift"],
     "salary": 10_000_000, "creds": ["диплом"]},

    # ── Акушерка и младший персонал ───────────────────────────────────────────
    {"name": "Зарина", "role": "midwife", "spec": "midwife", "exp": 90,
     "skills": ["ведение родов", "наблюдение беременных"],
     "langs": ["uz", "ru"], "districts": ["sergeli", "yangihayot"],
     "schedule": ["shift", "night"], "salary": 5_500_000,
     "creds": ["диплом медколледжа"]},
    {"name": "Умида", "role": "junior", "spec": "orderly", "exp": 36,
     "skills": ["санитарная обработка"], "langs": ["uz"],
     "districts": ["uchtepa", "chilanzar"], "schedule": ["shift"],
     "salary": 2_500_000, "creds": []},
)


def reset(cur) -> None:
    """Убирает ТОЛЬКО демо-карточки пула, по отрицательному диапазону id.

    Настоящих людей не касается: у них telegram_user_id положительный. Контакты
    и матчи уходят каскадом от профиля, приглашения — тоже.
    """
    cur.execute(
        """
        DELETE FROM product.consent_events
         WHERE actor_user_id IN (SELECT id FROM product.users
                                  WHERE telegram_user_id BETWEEN %s AND %s)
        """,
        (TG_BASE - 999, TG_BASE),
    )
    cur.execute(
        "DELETE FROM product.users WHERE telegram_user_id BETWEEN %s AND %s",
        (TG_BASE - 999, TG_BASE),
    )
    print(f"  удалено демо-медиков: {cur.rowcount}")


def seed(cur) -> int:
    created = 0
    for i, person in enumerate(POOL):
        tg_id = TG_BASE - i
        # Согласие ставим сразу: эти карточки существуют ровно для того, чтобы
        # быть в поиске, и профиль без согласия был бы противоречием.
        cur.execute(
            """
            INSERT INTO product.users
                (role, telegram_user_id, full_name, locale, consent_at, consent_version)
            VALUES ('medic', %s, %s, %s, now(), 'demo')
            ON CONFLICT (telegram_user_id) DO UPDATE SET full_name = EXCLUDED.full_name
            RETURNING id
            """,
            (tg_id, f"{NAME_PREFIX} {person['name']}",
             "uz" if "uz" in person["langs"] else "ru"),
        )
        user_id = cur.fetchone()[0]

        # Через ту же функцию, которой пользуется бот: если она сломается,
        # демо-сид сломается вместе с ней, а не покажет данные, которых
        # настоящий путь получить не может.
        cur.execute(
            """
            SELECT product.save_my_profile(
                %s, %s, %s, %s, %s::text[], %s::text[], %s::text[], %s::text[], %s, %s::text[])
            """,
            (user_id, person["role"], person["spec"], person["exp"],
             person["skills"], person["langs"], person["districts"],
             person["schedule"], person["salary"], person["creds"]),
        )
        cur.execute("SELECT product.save_contact(%s, %s, %s)",
                    (user_id, f"99890000{1000 + i}"[:12], f"demo_medic_{i}"))

        cur.execute("SELECT * FROM product.publish_my_profile(%s)", (user_id,))
        row = cur.fetchone()
        published, missing = row[1], row[2]
        if not published:
            print(f"  !! {person['name']}: не опубликован, не хватает {missing}")
            continue
        created += 1
    return created


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true",
                        help="сначала удалить прежние демо-карточки")
    args = parser.parse_args()

    import os

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("нет DATABASE_URL владельца базы — засеять пул нельзя", file=sys.stderr)
        return 1

    with psycopg.connect(dsn, autocommit=False) as conn, conn.cursor() as cur:
        if args.reset:
            reset(cur)
        created = seed(cur)
        conn.commit()

        cur.execute(
            "SELECT count(*) FROM product.candidate_profiles WHERE status = 'active'"
        )
        total = cur.fetchone()[0]

    print(f"Карточек в общем поиске создано: {created} из {len(POOL)}")
    print(f"Всего активных карточек в базе: {total}")
    print("Кабинет клиники: раздел «Подбор» → «Поиск по базе»")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
