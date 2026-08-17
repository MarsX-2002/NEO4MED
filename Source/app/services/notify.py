"""Сообщения медику из кабинета клиники.

Одна поверхность пишет в другую. Раньше принятие отклика не выходило за
пределы базы: менеджер нажимал «Принять», а человек, который отвечал на
вопросы, об этом никогда не узнавал. Хуже: если он не оставил телефон, у
клиники не оставалось вообще никакого способа с ним связаться, и отклик
превращался в тупик с двух сторон.

Bot создаётся на одно сообщение и закрывается. Веб-процесс ботом не владеет и
polling не ведёт — держать долгоживущую сессию ради редкого события незачем,
а два polling-клиента на один токен Telegram и не разрешит.

Сбой отправки никогда не ломает запрос кабинета: решение менеджера уже принято
и записано, а недоставленное уведомление — это неприятность, а не потеря данных.
"""
from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app import db
from app.config import settings
from app.i18n import DEFAULT_LOCALE, t

log = logging.getLogger(__name__)


async def _candidate_of_application(conn, application_id: str) -> dict[str, Any] | None:
    """Кому писать и о чём. Идёт в контексте тенанта — то есть только по своему
    отклику: чужой просто не найдётся."""
    cur = await conn.execute(
        """
        SELECT u.id AS user_id, u.telegram_user_id, u.locale::text AS locale,
               j.title AS job_title, cl.name AS clinic_name
          FROM product.applications a
          JOIN product.candidate_profiles c ON c.id = a.candidate_id
          JOIN product.users u  ON u.id = c.user_id
          JOIN product.jobs j  ON j.id = a.job_id
          JOIN product.clinics cl ON cl.id = j.clinic_id
         WHERE a.id = %s
        """,
        (application_id,),
    )
    return await cur.fetchone()


async def application_accepted(conn, application_id: str) -> bool:
    """Сообщает медику, что клиника приняла его отклик.

    Если телефона у человека нет, к сообщению прикладывается кнопка «Отправить
    мой номер»: это последняя точка, где контакт ещё можно замкнуть, и здесь
    просьба уже понятна — его позвали, а не выпрашивают данные заранее.
    """
    s = settings()
    row = await _candidate_of_application(conn, application_id)
    if row is None or not row.get("telegram_user_id"):
        return False

    locale = row.get("locale") or DEFAULT_LOCALE
    # has_contact знает только функция базы: у прикладной роли нет прав на
    # таблицу контактов, и это правило мы здесь не обходим.
    card = await db.fetch_one(
        "SELECT has_contact FROM product.my_candidate_card(%s)", (row["user_id"],)
    )
    has_contact = bool((card or {}).get("has_contact"))

    text = t("accepted_notice", locale,
             job=row["job_title"], clinic=row["clinic_name"])
    if not has_contact:
        text = f"{text}\n\n{t('accepted_needs_contact', locale)}"

    bot = Bot(
        token=s.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        markup = None
        if not has_contact:
            # Импорт здесь, а не сверху: клавиатуры принадлежат боту, и
            # тянуть их в веб на уровне модуля означало бы связать поверхности
            # там, где связь нужна в одной функции.
            from app.bot import keyboards as kb

            markup = kb.share_contact(locale)
        await bot.send_message(row["telegram_user_id"], text, reply_markup=markup)
        log.info("медик уведомлён о принятом отклике %s", application_id)
        return True
    except Exception as e:
        # Человек мог заблокировать бота — это его право, а не наша ошибка.
        log.warning("не удалось уведомить медика по отклику %s: %s", application_id, e)
        return False
    finally:
        await bot.session.close()


async def invitation_sent(conn, invitation_id: str) -> bool:
    """Сообщает медику, что клиника его нашла и приглашает на собеседование.

    Это первое сообщение, которое человек получает не по своей инициативе:
    вакансию он не открывал, отклик не отправлял. Поэтому в тексте сразу видно,
    какая клиника, на какую вакансию и что решение за ним — и вместе с
    сообщением приходят обе кнопки, «принять» и «отказаться». Кнопка отказа
    здесь обязательна: приглашение без возможности сказать «нет» — это спам.

    Телефон в этот момент не просим. Он нужен только после accept, а просьба
    отдать номер до того, как человек решил, выглядит как условие.
    """
    s = settings()
    cur = await conn.execute(
        """
        SELECT u.telegram_user_id, u.locale::text AS locale,
               j.title AS job_title, cl.name AS clinic_name,
               i.message
          FROM product.invitations i
          JOIN product.candidate_profiles c ON c.id = i.candidate_id
          JOIN product.users u   ON u.id = c.user_id
          JOIN product.jobs j    ON j.id = i.job_id
          JOIN product.clinics cl ON cl.id = j.clinic_id
         WHERE i.id = %s
        """,
        (invitation_id,),
    )
    row = await cur.fetchone()
    if row is None or not row.get("telegram_user_id"):
        return False

    locale = row.get("locale") or DEFAULT_LOCALE
    text = t("invite_notice", locale, clinic=row["clinic_name"], job=row["job_title"])
    if row.get("message"):
        # Сообщение менеджера не переводим и не правим: это его слова.
        text = f"{text}\n\n<i>{row['message']}</i>"

    bot = Bot(
        token=s.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        from app.bot import keyboards as kb

        await bot.send_message(
            row["telegram_user_id"], text,
            reply_markup=kb.invitation_decision(locale, str(invitation_id)),
        )
        log.info("медик приглашён, уведомление отправлено: %s", invitation_id)
        return True
    except Exception as e:
        log.warning("не удалось доставить приглашение %s: %s", invitation_id, e)
        return False
    finally:
        await bot.session.close()
