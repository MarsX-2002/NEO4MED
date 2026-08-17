#!/usr/bin/env python3
import csv
import json
from pathlib import Path

import export_medical_vacancies as exp

OUT = Path(__file__).with_name("medical_vacancies_uzbekistan.csv")


def title_is_medical(item):
    title = " ".join(str(item.get(k) or "") for k in ("position_name", "position_name_ru"))
    context = " ".join(str(item.get(k) or "") for k in (
        "position_name", "position_name_ru", "company_name", "structure_name", "structure_name_ru"
    ))
    return bool(exp.INCLUDE.search(title)) and not exp.EXCLUDE.search(context)


def main():
    enriched = {}
    if OUT.exists():
        with OUT.open(encoding="utf-8-sig", newline="") as f:
            for old in csv.DictReader(f):
                enriched[str(old["id"])] = old

    first = exp.fetch_page(1)
    items = list(first["data"])
    for page in range(2, first["last_page"] + 1):
        items.extend(exp.fetch_page(page)["data"])

    final_by_id = {}
    for item in items:
        if not title_is_medical(item):
            continue
        vacancy_id = str(item["id"])
        if vacancy_id in enriched:
            record = enriched[vacancy_id]
            record["полнота_данных"] = "подробная карточка"
        else:
            record = exp.row(item)
            record["полнота_данных"] = "основная выдача; детали временно ограничены API"
        # Pagination can shift while the live dataset is being updated; ID is canonical.
        final_by_id[vacancy_id] = record

    final = list(final_by_id.values())
    final.sort(key=lambda x: (x.get("регион_ru", ""), x.get("организация", ""), x.get("должность_ru") or x.get("должность_uz", "")))
    fields = list(final[0])
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(final)
    print(json.dumps({
        "rows": len(final),
        "unique_ids": len({str(x["id"]) for x in final}),
        "detailed": sum(x["полнота_данных"] == "подробная карточка" for x in final),
        "basic": sum(x["полнота_данных"] != "подробная карточка" for x in final),
        "output": str(OUT),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
