"""Полнота переводов.

Дырка в узбекском словаре не ломает бота: `t()` молча падает на русский, и
узбекоязычный человек посреди узбекского диалога получает русскую фразу. На
демо это заметит зал, а не мы. Поэтому паритет ключей проверяется тестом.

Тесты в этом файле — единственные во всём наборе, которым не нужна база:
словари это чистые данные. `pytest.ini` гоняет их вместе с остальными, они
занимают миллисекунды.
"""
from __future__ import annotations

import re

import pytest

from app.i18n import DEFAULT_LOCALE, LOCALES, missing_keys, normalize, plural, t
from app.i18n import ru as ru_table

# Плейсхолдеры вида {name}. Если в русском есть {count}, а в узбекском нет,
# str.format молча отдаст фразу без числа — либо упадёт с KeyError на лишнем.
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def test_uz_has_every_key() -> None:
    assert missing_keys("uz") == set(), "в узбекском словаре не хватает ключей"


@pytest.mark.parametrize("locale", sorted(LOCALES))
def test_placeholders_match_russian(locale: str) -> None:
    """Набор подстановок обязан совпадать с русским для каждого ключа."""
    table = LOCALES[locale]
    for key, ru_text in ru_table.T.items():
        text = table.get(key)
        if text is None:
            continue
        assert set(PLACEHOLDER.findall(text)) == set(PLACEHOLDER.findall(ru_text)), (
            f"[{locale}] ключ {key}: подстановки разошлись с русским"
        )


def test_no_extra_keys_in_uz() -> None:
    """Ключ, которого нет в русском, — это мёртвый текст: его никто не покажет."""
    extra = set(LOCALES["uz"]) - set(ru_table.T)
    assert extra == set(), f"лишние ключи в узбекском: {sorted(extra)}"


def test_missing_key_is_visible() -> None:
    """Отсутствующий ключ должен бросаться в глаза, а не читаться как пустота."""
    assert t("нет-такого-ключа") == "[нет-такого-ключа]"


def test_fallback_to_russian() -> None:
    assert t("consent_accept", "en") == t("consent_accept", DEFAULT_LOCALE)


@pytest.mark.parametrize(
    ("n", "expected"),
    [(1, "вопрос"), (2, "вопроса"), (4, "вопроса"), (5, "вопросов"),
     (11, "вопросов"), (21, "вопрос"), (22, "вопроса"), (100, "вопросов")],
)
def test_russian_plural(n: int, expected: str) -> None:
    assert plural(n, "questions", "ru") == expected


def test_uzbek_plural_is_invariant() -> None:
    """В узбекском существительное после числительного не меняется."""
    assert {plural(n, "questions", "uz") for n in (1, 2, 5, 11, 21)} == {"savol"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("uz", "uz"), ("uz-UZ", "uz"), ("UZ", "uz"), ("ru", "ru"),
     ("en", "ru"), ("", "ru"), (None, "ru")],
)
def test_normalize(raw: str | None, expected: str) -> None:
    assert normalize(raw) == expected


# Узбекская таблица написана латиницей целиком. Кириллица в ней — почти всегда
# опечатка при наборе: раскладка не переключилась на одном слове, и в тексте
# появляется «takliflарni». Паритет ключей такое не ловит, а на демо это
# читается как брак. Поймано на живой правке, поэтому проверка здесь.
CYRILLIC = re.compile(r"[а-яА-ЯёЁ]")

# Два ключа называют русский язык по-русски, и это правильно: выбор языка
# показывается на обоих языках сразу, иначе его не выберет тот, кто не читает
# на втором.
CYRILLIC_ALLOWED = {"choose_language", "review_switch_language"}


def test_uzbek_table_has_no_accidental_cyrillic() -> None:
    from app.i18n import uz as uz_table

    stray = {
        key: "".join(sorted(set(CYRILLIC.findall(text))))
        for key, text in uz_table.T.items()
        if key not in CYRILLIC_ALLOWED and CYRILLIC.search(text)
    }
    assert stray == {}, f"кириллица в узбекских текстах: {stray}"
