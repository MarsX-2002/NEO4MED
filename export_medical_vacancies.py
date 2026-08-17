#!/usr/bin/env python3
import csv
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://ishapi.mehnat.uz/api/v1/vacancies"
OUT = Path(__file__).with_name("medical_vacancies_uzbekistan.csv")
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://ish.mehnat.uz",
    "Referer": "https://ish.mehnat.uz/",
    "User-Agent": "Mozilla/5.0",
}

# Clinical and pharmaceutical roles in Russian, Uzbek Latin, and Uzbek Cyrillic.
INCLUDE = re.compile(
    r"(?:"
    r"врач|доктор|медсестр|медицинск(?:ая|ий)\s+сестр|медбрат|фельдшер|акушер|санитар|"
    r"лаборант|рентген|реанимат|анестези|терапевт|педиатр|хирург|стоматолог|"
    r"кардиолог|невролог|онколог|гинеколог|уролог|офтальмолог|отоларинголог|лор\b|"
    r"дерматолог|эндокринолог|инфекционист|психиатр|нарколог|травматолог|ортопед|"
    r"радиолог|гематолог|нефролог|пульмонолог|гастроэнтеролог|патологоанатом|"
    r"эпидемиолог|бактериолог|вирусолог|иммунолог|дефектолог|логопед|"
    r"фармацевт|провизор|фармац|дезинфектор|тиббий|ҳамшира|хамшира|шифокор|"
    r"врач|фельдшер|акушер|санитар|лаборант|рентген|анестезиолог|реаниматолог|"
    r"стоматолог|доришунос|фарматсевт|farmatsevt|provizor|shifokor|hamshira|"
    r"tibbiy\s+aka|tibbiyot\s+aka|feldsher|akusher|sanitar|laborant"
    r")", re.I
)
EXCLUDE = re.compile(
    r"(?:ветеринар|ветврач|фитосанитар|ўсимлик|o['‘’`]?simlik|агрохими|зоотех|"
    r"ветерин|дорихона\s+(?:мудир|сотув)|фармацевтик\s+савдо)", re.I
)


def get_json(url, attempts=4):
    for attempt in range(attempts):
        try:
            req = Request(url, headers=HEADERS)
            with urlopen(req, timeout=30) as response:
                return json.load(response)
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(0.7 * (attempt + 1))


def fetch_page(page):
    query = urlencode({
        "per_page": 100,
        "kodp_keys": "null",
        "sort_key": "salary_asc",
        "nskz": "22,322,323,324",
        "is_reserved": 0,
        "page": page,
    })
    return get_json(f"{BASE}?{query}")["data"]


def fetch_detail(vacancy_id):
    return get_json(f"{BASE}/{vacancy_id}")["data"]


def text_for_filter(item):
    return " ".join(str(item.get(k) or "") for k in (
        "position_name", "position_name_ru", "structure_name", "structure_name_ru"
    ))


def medical_candidate(item):
    text = text_for_filter(item)
    return bool(INCLUDE.search(text)) and not EXCLUDE.search(text)


def profile(detail):
    raw = (detail.get("company") or {}).get("profile_data")
    try:
        return json.loads(raw) if raw else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def join_value(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return "; ".join(str(x) for x in value if x is not None)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def salary_quality(value):
    try:
        salary = float(value)
    except (TypeError, ValueError):
        return "не указана"
    if salary <= 0:
        return "нулевая/ошибка источника"
    if salary < 100_000:
        return "подозрительно низкая — проверить единицы"
    return "как указано в источнике"


def row(detail):
    company = detail.get("company") or {}
    company_data = company.get("data") or {}
    extra = detail.get("additional_info") or {}
    prof = profile(detail)
    region = detail.get("region") or {}
    district = detail.get("district") or {}
    vacancy_id = detail.get("id")
    salary = detail.get("position_salary_manually") or detail.get("position_salary")
    return {
        "id": vacancy_id,
        "должность_ru": detail.get("position_name_ru") or "",
        "должность_uz": detail.get("position_name") or "",
        "организация": detail.get("company_name") or detail.get("company_profile_name") or "",
        "инн": detail.get("company_tin") or "",
        "oked": detail.get("company_oked") or company.get("oked") or "",
        "подразделение": detail.get("structure_name_ru") or detail.get("structure_name") or "",
        "зарплата_как_в_api": salary or "",
        "качество_зарплаты": salary_quality(salary),
        "ставка": detail.get("position_rate") or "",
        "обязанности": detail.get("position_duties") or "",
        "требования": detail.get("position_requirements") or "",
        "условия": detail.get("position_conditions") or "",
        "образование": extra.get("min_education") or "",
        "опыт_лет": extra.get("work_exparence") if extra.get("work_exparence") is not None else "",
        "иностранные_языки": join_value(extra.get("forigen_languages")),
        "льготы": extra.get("add_benefits_for_employees") or "",
        "регион_ru": region.get("name_ru") or "",
        "район_ru": district.get("name_ru") or "",
        "адрес_вакансии": detail.get("vacancy_address") or "",
        "фактический_адрес": prof.get("actual_address") or prof.get("address") or company_data.get("ADDR") or "",
        "телефоны": join_value(detail.get("phones")),
        "email": join_value(detail.get("emails")),
        "дата_начала": detail.get("date_start") or "",
        "дата_окончания": detail.get("date_stop") or "",
        "срочно": "да" if detail.get("vacancy_immediately") else "нет",
        "просмотры": (detail.get("detail") or {}).get("view_count", ""),
        "отклики": (detail.get("detail") or {}).get("vouchers_count", ""),
        "ссылка_api": f"{BASE}/{vacancy_id}",
    }


def main():
    first = fetch_page(1)
    pages = first["last_page"]
    items = list(first["data"])
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(fetch_page, p): p for p in range(2, pages + 1)}
        for future in as_completed(futures):
            items.extend(future.result()["data"])

    candidates = {x["id"]: x for x in items if medical_candidate(x)}
    details = []
    failures = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(fetch_detail, vacancy_id): vacancy_id for vacancy_id in candidates}
        for future in as_completed(futures):
            vacancy_id = futures[future]
            try:
                detail = future.result()
                # Recheck after receiving full title/department and reject known non-medical fields.
                if medical_candidate(detail):
                    details.append(detail)
            except Exception as exc:
                failures.append((vacancy_id, str(exc)))

    rows = sorted((row(x) for x in details), key=lambda x: (x["регион_ru"], x["организация"], x["должность_ru"] or x["должность_uz"]))
    if not rows:
        raise RuntimeError("No medical vacancies found")
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"all_api_rows": len(items), "candidates": len(candidates), "exported": len(rows), "failures": failures, "output": str(OUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
