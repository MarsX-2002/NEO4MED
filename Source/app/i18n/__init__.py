"""Словари интерфейса. Русский и узбекский равноправны.

Тексты держим в питоне, а не в .po: на P0 их немного, а лишний шаг компиляции
переводов только замедлит правки во время репетиций демо.
"""
from __future__ import annotations

from app.i18n import ru, uz

LOCALES = {"ru": ru.T, "uz": uz.T}
DEFAULT_LOCALE = "ru"


def normalize(locale: str | None) -> str:
    """Приводит что угодно к поддерживаемой локали.

    Пригодится там, где язык приходит снаружи: `language_code` Telegram или
    заголовок браузера. Всё незнакомое — русский, а не падение.
    """
    code = (locale or "").strip().lower()[:2]
    return code if code in LOCALES else DEFAULT_LOCALE


def t(key: str, locale: str | None = None, **kwargs) -> str:
    """Строка по ключу. Отсутствующий перевод падает на русский,
    отсутствующий ключ виден как [key] — чтобы дырку было видно на репетиции,
    а не читалось как пустое сообщение."""
    table = LOCALES.get(locale or DEFAULT_LOCALE, ru.T)
    text = table.get(key) or ru.T.get(key)
    if text is None:
        return f"[{key}]"
    return text.format(**kwargs) if kwargs else text


def plural(n: int, prefix: str, locale: str | None = None) -> str:
    """Форма существительного при числе n: `prefix` + _one/_few/_many.

    Нужна из-за русского: «1 вопрос», «2 вопроса», «5 вопросов». В узбекском
    существительное после числительного не меняется, поэтому все три ключа там
    одинаковые — правило остаётся одно на оба языка, а не ветвится по локали.
    """
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        form = "one"
    elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        form = "few"
    else:
        form = "many"
    return t(f"{prefix}_{form}", locale)


def labels(*keys: str) -> set[str]:
    """Все языковые варианты подписей кнопок для перечисленных ключей.

    Нужна, чтобы отличить нажатие кнопки меню от ответа на вопрос интервью.
    Reply-клавиатура присылает обычный текст, и без такой проверки «Помощь»
    записывалась в транскрипт как ответ кандидата — клиника читала это в
    саммари. Сравниваем по всем локалям сразу: человек мог сменить язык, а
    клавиатура у него в чате осталась старая.
    """
    out: set[str] = set()
    for table in LOCALES.values():
        for key in keys:
            value = table.get(key)
            if value:
                out.add(value)
    return out


def missing_keys(locale: str) -> set[str]:
    """Ключи, которых нет в переводе. Используется тестом, чтобы дырка в
    узбекском не доезжала до демо незамеченной."""
    table = LOCALES.get(locale)
    if table is None:
        return set(ru.T)
    return {k for k in ru.T if not table.get(k)}
