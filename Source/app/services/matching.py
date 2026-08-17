"""Подбор кандидатов под вакансию: чистая функция без обращений к базе.

Ни одного запроса, ни одного вызова модели. Причина не в чистоте ради чистоты:
это единственная часть продукта, где решение «показать человека клинике или нет»
принимается алгоритмом, и его надо уметь прогонять на десятке придуманных
случаев за миллисекунды, а не через туннель к серверной базе.

Эмбеддингов здесь нет и не будет. Семантический поиск по медицинским
названиям уже разваливался на этом же месте: в удалённом каталоге было 1305
врачебных названий против 54 медсестринских, и «медсестра» уверенно находила
врача. Роль и специальность — словарные коды, их надо сравнивать, а не угадывать.

Что показывается человеку, а что нет:

  * `score` внутренний. Клиника видит уровень (`strong` / `possible`), причины
    и пробелы. Число сравнивает кандидатов между собой, но не описывает
    человека, и показывать его — значит делать вид, что 62 объективно лучше 58;
  * причины и пробелы — КОДЫ, а не фразы. Матч видят обе стороны, каждая на
    своём языке, и фраза, зафиксированная в момент расчёта, была бы навсегда
    на языке того, кто нажал кнопку. Формат: `код` или `код:аргумент`;
  * решение не принимается. Совпадение — это приглашение поговорить, а не
    вывод о пригодности.

Хранятся только прошедшие жёсткие фильтры. Отсеянные считаются и отдаются
сводкой («34 отсеяно: 20 по роли, 10 по графику»): клинике полезно понимать,
что фильтр съел половину рынка, а держать в базе строки про тех, кого мы
никогда не покажем, незачем.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

# Версия попадает в product.matches.algorithm_version и входит в UNIQUE
# (job_id, candidate_id, algorithm_version). Меняется при любой правке весов
# или фильтров: старые матчи остаются как были, новые считаются заново, и
# видно, каким алгоритмом получен каждый.
ALGORITHM_VERSION = "v1"

# Веса из project/01_product/P0_BUILD_PLAN.md, раздел «Ranking после hard
# filters». Сумма 100, чтобы score читался как проценты и порог можно было
# обсуждать словами, а не подбирать.
WEIGHTS: dict[str, int] = {
    "specialty": 35,
    "experience": 20,
    "skills": 20,
    "district": 10,
    "languages": 5,
    "completeness": 10,
}

# Порог «сильного» совпадения и нижняя граница показа. Ниже MIN_SCORE матч не
# создаётся вообще: список из тридцати «возможных» с двумя формальными
# причинами хуже, чем список из пяти, потому что его не читают.
STRONG_SCORE = 55
MIN_SCORE = 30

# Критерий A7: каждый показанный матч обязан иметь минимум две конкретные
# причины. То же требует ограничение ck_matches_reasons в базе. Если назвать две
# причины не получилось — матч не показываем, а не добираем причины до нужного
# числа общими словами.
MIN_REASONS = 2


# ── Нормализация свободного текста ────────────────────────────────────────────
# Навыки и языки на стороне кандидата могут быть как кодами (человек заполнял
# форму кнопками), так и свободным текстом («Русский — свободно»), если профиль
# заполнялся из интервью. Сравнивать надо и то и то, поэтому обе стороны
# приводятся к одному виду, а не предполагается формат.

_SPACES = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s\u0400-\u04FF]+")


def _norm(value: Any) -> str:
    """Нижний регистр, без пунктуации, одиночные пробелы.

    `ё` → `е` и `‘` уже съедены пунктуацией: «o‘zbek» и «ozbek» должны
    совпадать, иначе узбекский латиницей не сойдётся сам с собой.
    """
    text = str(value or "").lower().replace("ё", "е")
    text = _PUNCT.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def _clean(values: Iterable[Any] | None) -> list[str]:
    return [n for n in (_norm(v) for v in (values or [])) if n]


# Языки приводим к кодам. Список закрытый и короткий: это Узбекистан, и
# сверх этих языков в медицинском найме не встречается ничего.
_LANGUAGE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ru", ("ru", "рус", "rus", "russ")),
    ("uz", ("uz", "узб", "uzb", "ozb", "o zb")),
    ("en", ("en", "англ", "eng", "ingliz", "ingl")),
    ("kk", ("kk", "каз", "kaz", "qaz")),
    ("tg", ("tg", "тадж", "tadj", "tojik", "taj")),
    ("kaa", ("kaa", "каракалп", "qoraqalpoq", "karakalpak")),
)


def _language_codes(values: Iterable[Any] | None) -> set[str]:
    """Коды языков из чего угодно: 'ru', 'Русский — свободно', 'o‘zbekcha'."""
    out: set[str] = set()
    for raw in _clean(values):
        for code, hints in _LANGUAGE_HINTS:
            if any(h in raw for h in hints):
                out.add(code)
                break
        else:
            # Незнакомое оставляем как есть: вдруг клиника требует что-то,
            # чего нет в списке. Лучше не сойтись, чем сойтись неправильно.
            out.add(raw)
    return out


def _overlap(required: Sequence[str], available: Sequence[str]) -> list[str]:
    """Какие из требуемых пунктов нашлись у человека.

    Сравнение по вхождению в обе стороны: «инъекции» должно находиться в
    «внутривенные инъекции», а «внутривенные инъекции» — соответствовать
    требованию «инъекции». Строгое равенство здесь бесполезно, потому что обе
    стороны пишут люди.
    """
    found: list[str] = []
    for need in required:
        if any(need in have or have in need for have in available):
            found.append(need)
    return found


# ── Результат ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MatchResult:
    """Одно совпадение. Ровно то, что ложится в product.matches."""

    candidate_id: str
    level: str                      # strong | possible
    score: int                      # 0..100, внутренний
    hard_constraints_passed: bool
    reasons: list[str]
    gaps: list[str]
    algorithm_version: str = ALGORITHM_VERSION

    @property
    def wants_more_money(self) -> bool:
        """Прошёл фильтры, но просит больше, чем вакансия платит.

        Отдельная корзина в кабинете. Жёстким отказом это не делаем сознательно:
        вилки в объявлениях занижены, и фильтр по зарплате отрезал бы половину
        рынка ещё до того, как люди поговорили.
        """
        return any(g.startswith("salary:") for g in self.gaps)


@dataclass(frozen=True)
class Ranking:
    matches: list[MatchResult]
    #  код причины отсева -> сколько людей отсеяно. Для честной сводки в
    #  кабинете: «отсеяно 34» без разбивки не даёт понять, что менять.
    excluded: dict[str, int] = field(default_factory=dict)

    @property
    def excluded_total(self) -> int:
        return sum(self.excluded.values())


# ── Жёсткие фильтры ───────────────────────────────────────────────────────────

def _hard_fail(job: Mapping[str, Any], cand: Mapping[str, Any]) -> str | None:
    """Причина, по которой человека нельзя показывать. None — можно.

    Зарплаты здесь нет: см. MatchResult.wants_more_money.
    """
    # Роль — первый и главный фильтр. Врач и медсестра несовместимы, и на этом
    # разваливался семантический поиск.
    if _norm(job.get("role_category")) != _norm(cand.get("role_category")):
        return "role"

    # Город. Готовности к переезду в модели нет, поэтому другой город — отказ.
    job_city, cand_city = _norm(job.get("city")), _norm(cand.get("city"))
    if job_city and cand_city and job_city != cand_city:
        return "city"

    # График сравниваем только если обе стороны его назвали. Кандидат, который
    # график не указал, не исключается: молчание — не отказ, это пробел.
    job_sched, cand_sched = _clean(job.get("schedule")), _clean(cand.get("schedule"))
    if job_sched and cand_sched and not set(job_sched) & set(cand_sched):
        return "schedule"

    # Обязательный язык. Здесь молчание работает против кандидата, и правильно:
    # медсестра, которая не может объясниться с пациентом, — риск, а не пробел.
    need_langs = _language_codes(job.get("required_languages"))
    if need_langs and not need_langs <= _language_codes(cand.get("languages")):
        return "language"

    # Документов здесь НЕТ, и это отступление от P0-плана.
    #
    # План считает отсутствующий credential жёстким отказом. Так и было бы,
    # будь квалификации нормализованы. Но на обеих сторонах это свободный текст:
    # клиника пишет «Действующий сертификат», человек — «сертификат
    # специалиста». Ни одно вхождение не совпадёт, и хороший кандидат исчезнет
    # из выдачи молча — а клиника даже не узнает, что он был.
    #
    # Жёсткий фильтр хорош ровно настолько, насколько нормализованы данные под
    # ним. Пока квалификаций нет в словарях, документы уходят в пробелы: пусть
    # менеджер спросит про сертификат сам, это одна фраза в разговоре.
    return None


# ── Оценка ────────────────────────────────────────────────────────────────────

def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_money(value: Any) -> int | None:
    """numeric из psycopg приходит Decimal. int здесь достаточно: суммы в сумах
    целые, а Decimal не сериализуется в JSON и уже ломал сборку промпта."""
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


_PROFILE_FIELDS = (
    "specialty", "experience_months", "skills", "languages",
    "districts", "schedule", "salary_min_uzs", "credential_claims",
)


def _completeness(cand: Mapping[str, Any]) -> float:
    """Доля заполненных полей карточки, 0..1.

    Полная карточка — не достоинство человека, а признак того, что клиника
    сможет с ним говорить предметно. Поэтому вес маленький (10 из 100), но не
    нулевой.
    """
    filled = 0
    for name in _PROFILE_FIELDS:
        value = cand.get(name)
        if isinstance(value, (list, tuple)):
            filled += 1 if len(value) else 0
        else:
            filled += 1 if value not in (None, "") else 0
    return filled / len(_PROFILE_FIELDS)


def evaluate(job: Mapping[str, Any], cand: Mapping[str, Any]) -> MatchResult | str:
    """Оценивает одного кандидата под одну вакансию.

    Возвращает `MatchResult` либо строку — код причины, по которой человек не
    попал в выдачу: `role`, `city`, `schedule`, `language` для жёстких фильтров,
    `weak` для слишком низкого балла и `thin` для карточки, о которой нельзя
    сказать двух конкретных вещей.

    Строка вместо исключения и вместо None, потому что причина отсева нужна
    вызывающему: из неё собирается сводка «почему в подборе пусто».
    """
    failed = _hard_fail(job, cand)
    if failed is not None:
        return failed

    reasons: list[str] = []
    gaps: list[str] = []
    score = 0.0

    # ── Специальность ────────────────────────────────────────────────────────
    job_spec, cand_spec = job.get("specialty"), cand.get("specialty")
    if job_spec and cand_spec and _norm(job_spec) == _norm(cand_spec):
        score += WEIGHTS["specialty"]
        reasons.append(f"specialty_exact:{cand_spec}")
    elif not job_spec:
        # Вакансия специальность не назвала — спрашивать её у кандидата
        # некорректно. Половина веса за совпадение категории роли, которое уже
        # проверено жёстким фильтром.
        score += WEIGHTS["specialty"] / 2
        reasons.append(f"role_match:{cand.get('role_category')}")
    else:
        gaps.append(f"specialty_other:{cand_spec}" if cand_spec else "specialty_unknown")

    # ── Опыт ─────────────────────────────────────────────────────────────────
    need_exp = _to_int(job.get("experience_min_months"))
    has_exp = _to_int(cand.get("experience_months"))
    if has_exp is None:
        gaps.append("experience_unknown")
    elif not need_exp:
        # Требования нет: любой названный опыт — довод, но не полный вес.
        score += WEIGHTS["experience"] / 2
        reasons.append(f"experience:{has_exp}")
    elif has_exp >= need_exp:
        score += WEIGHTS["experience"]
        reasons.append(f"experience:{has_exp}")
    elif has_exp >= need_exp * 0.6:
        # Почти дотягивает. Это разговор, а не отказ: год разницы у медсестры с
        # пятью годами стажа ничего не решает.
        score += WEIGHTS["experience"] / 2
        gaps.append(f"experience_short:{has_exp}/{need_exp}")
    else:
        gaps.append(f"experience_short:{has_exp}/{need_exp}")

    # ── Навыки ───────────────────────────────────────────────────────────────
    need_skills = _clean(job.get("required_skills"))
    has_skills = _clean(cand.get("skills"))
    if need_skills:
        found = _overlap(need_skills, has_skills)
        score += WEIGHTS["skills"] * len(found) / len(need_skills)
        if found:
            reasons.append(f"skills:{len(found)}/{len(need_skills)}")
        missing = [s for s in need_skills if s not in found]
        if missing:
            gaps.append("skills_missing:" + ",".join(missing[:3]))

    # ── Район ────────────────────────────────────────────────────────────────
    job_districts, cand_districts = _clean(job.get("districts")), _clean(cand.get("districts"))
    if job_districts and cand_districts:
        common = set(job_districts) & set(cand_districts)
        if common:
            score += WEIGHTS["district"]
            reasons.append("district:" + sorted(common)[0])
        else:
            gaps.append("district_other:" + ",".join(sorted(cand_districts)[:3]))
    elif job_districts and not cand_districts:
        gaps.append("district_unknown")

    # ── Языки сверх обязательного ────────────────────────────────────────────
    need_langs = _language_codes(job.get("required_languages"))
    has_langs = _language_codes(cand.get("languages"))
    extra = has_langs - need_langs
    if extra:
        score += min(WEIGHTS["languages"], len(extra) * 2)
        reasons.append("languages:" + ",".join(sorted(extra)))

    # ── Полнота карточки ─────────────────────────────────────────────────────
    score += WEIGHTS["completeness"] * _completeness(cand)

    # ── Документы: пробел, а не отказ ────────────────────────────────────────
    need_cred = _clean(job.get("credential_requirements"))
    if need_cred:
        missing_cred = [
            c for c in need_cred
            if c not in _overlap(need_cred, _clean(cand.get("credential_claims")))
        ]
        if missing_cred:
            gaps.append("credential_missing:" + ",".join(missing_cred[:2]))

    # ── Зарплата: пробел, а не отказ ─────────────────────────────────────────
    wants = _to_money(cand.get("salary_min_uzs"))
    pays_max = _to_money(job.get("salary_max_uzs"))
    pays_min = _to_money(job.get("salary_min_uzs"))
    if wants is None:
        gaps.append("salary_unknown")
    elif pays_max is not None and wants > pays_max:
        gaps.append(f"salary:{wants}")
    elif pays_min is not None and wants <= pays_min:
        reasons.append(f"salary_fits:{wants}")

    total = round(score)
    if total < MIN_SCORE:
        return "weak"
    if len(reasons) < MIN_REASONS:
        # Двух конкретных доводов нет. Показывать нечего: клиника всё равно
        # спросит «почему он», и ответ «формально проходит» её не устроит.
        return "thin"

    return MatchResult(
        candidate_id=str(cand["candidate_id"]),
        level="strong" if total >= STRONG_SCORE else "possible",
        score=min(total, 100),
        hard_constraints_passed=True,
        reasons=reasons,
        gaps=gaps,
    )


def rank(job: Mapping[str, Any], candidates: Iterable[Mapping[str, Any]]) -> Ranking:
    """Все кандидаты по одной вакансии, от сильных к слабым.

    Сортировка по убыванию балла, при равенстве — по большему опыту: два
    одинаковых балла у менеджера всё равно требуют порядка, и опыт понятнее,
    чем порядок строк из базы.
    """
    matches: list[MatchResult] = []
    excluded: dict[str, int] = {}
    by_id: dict[str, Mapping[str, Any]] = {}

    for cand in candidates:
        outcome = evaluate(job, cand)
        if isinstance(outcome, str):
            excluded[outcome] = excluded.get(outcome, 0) + 1
            continue
        matches.append(outcome)
        by_id[outcome.candidate_id] = cand

    matches.sort(
        key=lambda m: (
            m.score,
            _to_int(by_id[m.candidate_id].get("experience_months")) or 0,
        ),
        reverse=True,
    )
    return Ranking(matches=matches, excluded=excluded)
