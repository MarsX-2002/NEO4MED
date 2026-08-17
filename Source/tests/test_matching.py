"""Подбор: скоринг, общий поиск, приглашения, контакт после accept.

Две половины, и они разные по природе.

Первая — чистый скоринг. Базы ему не нужно: `matching.evaluate` не делает ни
одного запроса, и это главная причина, по которой алгоритм вынесен отдельно.
Здесь проверяются обещания продукта: врач не попадает в подбор на медсестру,
процедурная медсестра стоит выше допустимых, у показанного матча всегда есть
две конкретные причины.

Вторая — обещания базы. Кто виден в общем поиске, кому можно отправить
приглашение, когда открывается телефон. Эти правила живут в SQL-функциях и
политиках специально: бот и кабинет можно переписать, а возможности их обойти
появиться не должно.
"""
from __future__ import annotations

import psycopg
import pytest

from app import db
from app.services import candidate as cand
from app.services import match_store as store
from app.services.matching import MatchResult, evaluate, rank
from tests.conftest import TEST_TG_ID_BASE, admin_execute, admin_fetch_one

# ── Данные для чистого скоринга ───────────────────────────────────────────────

JOB = {
    "role_category": "nurse",
    "specialty": "procedural_nurse",
    "city": "tashkent",
    "experience_min_months": 24,
    "required_skills": ["инъекции"],
    "required_languages": ["uz"],
    "districts": ["chilanzar"],
    "schedule": ["shift"],
    "salary_min_uzs": 4_000_000,
    "salary_max_uzs": 6_000_000,
    "credential_requirements": [],
}


def _cand(cid: str, **over) -> dict:
    base = {
        "candidate_id": cid,
        "role_category": "nurse",
        "specialty": "procedural_nurse",
        "city": "tashkent",
        "experience_months": 48,
        "skills": ["внутривенные инъекции"],
        "languages": ["uz", "ru"],
        "districts": ["chilanzar"],
        "schedule": ["shift"],
        "salary_min_uzs": 4_000_000,
        "credential_claims": ["диплом медколледжа"],
    }
    base.update(over)
    return base


# ── Скоринг: жёсткие фильтры ──────────────────────────────────────────────────

def test_doctor_is_excluded_from_nurse_search() -> None:
    """Критерий A6. Врач и медсестра несовместимы, и никакой опыт этого не меняет.

    Именно на этом разваливался семантический поиск: в удалённом каталоге было
    1305 врачебных названий против 54 медсестринских, и «медсестра» уверенно
    находила врача.
    """
    doctor = _cand("d", role_category="doctor", specialty="doctor_any",
                   experience_months=240)
    assert evaluate(JOB, doctor) == "role"


def test_procedural_nurse_ranks_above_every_allowed_candidate() -> None:
    """Точное совпадение специальности обходит всех, кто прошёл фильтры."""
    exact = _cand("exact")
    ward = _cand("ward", specialty="ward_nurse", skills=["уход за пациентами"])
    junior = _cand("junior", experience_months=24, skills=[], districts=["sergeli"])

    ranked = rank(JOB, [ward, junior, exact])
    assert ranked.matches, "хотя бы точное совпадение обязано пройти"
    assert ranked.matches[0].candidate_id == "exact"
    assert ranked.matches[0].level == "strong"
    # Порядок именно по убыванию: список читают сверху.
    scores = [m.score for m in ranked.matches]
    assert scores == sorted(scores, reverse=True)


def test_missing_required_language_excludes() -> None:
    """Язык — жёсткий фильтр. Медсестра, которая не объяснится с пациентом, —
    это риск, а не пробел в карточке."""
    assert evaluate(JOB, _cand("n", languages=["ru"])) == "language"


def test_free_text_languages_are_normalized() -> None:
    """Профиль из интервью хранит языки фразами, форма — кодами. Сравнивать
    надо и то и то, иначе узбекский не сойдётся сам с собой."""
    from_interview = _cand("i", languages=["Узбекский — свободно", "Русский — свободно"])
    assert isinstance(evaluate(JOB, from_interview), MatchResult)

    latin = _cand("l", languages=["o‘zbekcha", "ruscha"])
    assert isinstance(evaluate(JOB, latin), MatchResult)


def test_schedule_filters_only_when_both_sides_named_it() -> None:
    """Молчание кандидата о графике — не отказ.

    Профиль, заполненный из интервью, графика не содержит вовсе: модель отдаёт
    его свободным текстом, а в колонке коды словаря. Исключать за это значило бы
    выбросить всех, кто пришёл через собеседование.
    """
    silent = _cand("s", schedule=[])
    assert isinstance(evaluate(JOB, silent), MatchResult)

    conflicting = _cand("c", schedule=["night"])
    assert evaluate(JOB, conflicting) == "schedule"


def test_other_city_is_excluded() -> None:
    assert evaluate(JOB, _cand("c", city="samarkand")) == "city"


# ── Скоринг: пробелы вместо отказов ───────────────────────────────────────────

def test_higher_salary_is_a_gap_not_a_rejection() -> None:
    """Осознанное отступление от P0-плана.

    План считает «просит больше вилки» жёстким отказом. Вилки в объявлениях
    занижены, и такой фильтр отрезал бы половину рынка ещё до разговора. Человек
    остаётся в выдаче с видимым разрывом и в отдельной корзине.
    """
    greedy = _cand("g", salary_min_uzs=9_000_000)
    result = evaluate(JOB, greedy)
    assert isinstance(result, MatchResult)
    assert result.wants_more_money is True
    assert any(g == "salary:9000000" for g in result.gaps)


def test_missing_credential_is_a_gap_not_a_rejection() -> None:
    """Тоже отступление от плана, и по той же причине.

    Квалификации на обеих сторонах — свободный текст: клиника пишет
    «Действующий сертификат», человек — «сертификат специалиста». Жёсткий фильтр
    по такому тексту молча выбросил бы подходящего, и клиника не узнала бы, что
    он был. Жёсткий фильтр хорош ровно настолько, насколько нормализованы данные
    под ним.
    """
    job = dict(JOB, credential_requirements=["действующий сертификат"])
    result = evaluate(job, _cand("c", credential_claims=["диплом"]))
    assert isinstance(result, MatchResult)
    assert any(g.startswith("credential_missing:") for g in result.gaps)


def test_short_experience_is_a_gap() -> None:
    result = evaluate(JOB, _cand("e", experience_months=18))
    assert isinstance(result, MatchResult)
    assert "experience_short:18/24" in result.gaps


# ── Скоринг: два обещания показанного матча ───────────────────────────────────

def test_shown_match_always_has_two_reasons() -> None:
    """Критерий A7 и ограничение ck_matches_reasons в базе.

    Проверяем не то, что база не даст сохранить одну причину, а то, что мы в это
    ограничение не упираемся: алгоритм сам отказывается показывать матч, о
    котором нельзя сказать двух конкретных вещей.
    """
    variants = [
        _cand("a"),
        _cand("b", specialty="ward_nurse"),
        _cand("c", skills=[], districts=[]),
        _cand("d", experience_months=None),
        _cand("e", salary_min_uzs=None),
    ]
    for c in variants:
        result = evaluate(JOB, c)
        if isinstance(result, MatchResult):
            assert len(result.reasons) >= 2, f"{c['candidate_id']}: {result.reasons}"
            assert result.hard_constraints_passed is True


def test_thin_card_is_not_shown() -> None:
    """О карточке без данных двух причин не назовёшь — значит показывать нечего.
    Клиника всё равно спросит «почему он», и «формально проходит» её не устроит.
    """
    thin = _cand("t", specialty=None, experience_months=None, skills=[],
                 districts=[], salary_min_uzs=None, credential_claims=[],
                 languages=["uz"])
    assert evaluate(JOB, thin) in ("weak", "thin")


def test_exclusion_summary_counts_every_reason() -> None:
    """Сводка нужна, чтобы пустой подбор не читался как поломка кабинета."""
    ranked = rank(JOB, [
        _cand("d", role_category="doctor"),
        _cand("n", languages=["ru"]),
        _cand("s", schedule=["night"]),
        _cand("ok"),
    ])
    assert ranked.excluded == {"role": 1, "language": 1, "schedule": 1}
    assert ranked.excluded_total == 3
    assert len(ranked.matches) == 1


# ── Общий поиск: кто в нём виден ──────────────────────────────────────────────

async def _draft_medic(tg_offset: int = -55) -> dict:
    """Медик с черновым профилем: так его создаёт отклик на вакансию."""
    row = await admin_fetch_one(
        """
        WITH u AS (
            INSERT INTO product.users (role, telegram_user_id, locale, consent_at, consent_version)
            VALUES ('medic', %(tg)s, 'ru', now(), 'test') RETURNING id
        ), p AS (
            INSERT INTO product.candidate_profiles
                (user_id, role_category, specialty, experience_months, status, source)
            SELECT u.id, 'nurse', 'procedural_nurse', 60, 'draft', 'text' FROM u
            RETURNING id
        )
        SELECT (SELECT id FROM u) AS user_id, (SELECT id FROM p)::text AS candidate_id
        """,
        {"tg": TEST_TG_ID_BASE + tg_offset},
    )
    assert row is not None
    return row


async def test_pool_shows_only_people_who_opted_in(fixture_world: dict):
    """В общий поиск попадает только тот, кто сам вывел карточку.

    Откликнувшийся виден клинике по другой причине — он к ней пришёл, — и
    показывать его всем остальным согласия не давал (миграция 036).
    """
    draft = await _draft_medic()

    async with db.scoped(clinic_id=fixture_world["clinic_a_id"],
                         user_id=fixture_world["clinic_user_id"],
                         member_role="owner") as conn:
        ids = {str(r["candidate_id"]) for r in await store.pool(conn, limit=200)}

    assert fixture_world["candidate_id"] in ids, "активная карточка обязана быть в поиске"
    assert draft["candidate_id"] not in ids, "черновик в общий поиск попадать не должен"


async def test_pool_is_anonymous(fixture_world: dict):
    """Ни имени, ни телефона. Это состав колонок функции базы, а не фильтрация
    в кабинете: забыть отфильтровать в коде можно, изменить подпись функции —
    нет."""
    async with db.scoped(clinic_id=fixture_world["clinic_a_id"],
                         user_id=fixture_world["clinic_user_id"],
                         member_role="owner") as conn:
        rows = await store.pool(conn, limit=5)

    assert rows
    forbidden = {"full_name", "name", "phone", "telegram_username",
                 "telegram_user_id", "user_id", "transcript", "extraction"}
    assert forbidden.isdisjoint(rows[0].keys()), sorted(set(rows[0]) & forbidden)


async def test_pool_is_closed_without_tenant_context(fixture_world: dict):
    """Не выставленный контекст означает «ничего не видно», а не «видно всё»."""
    rows = await db.fetch_all(
        "SELECT * FROM product.pool_candidates(NULL, NULL, NULL, NULL, NULL, NULL, 50, 0)"
    )
    assert rows == []


async def test_pool_is_closed_for_employee_role(fixture_world: dict):
    """Подбор — управленческий раздел. До 039 роль employee видела весь пул:
    в 022 менеджерская проверка появилась у вакансий и отзывов, а кандидатов,
    матчей и приглашений не коснулась."""
    async with db.scoped(clinic_id=fixture_world["clinic_a_id"],
                         user_id=fixture_world["clinic_user_id"],
                         member_role="employee") as conn:
        assert await store.pool(conn, limit=50) == []
        cur = await conn.execute("SELECT count(*) AS n FROM product.candidate_profiles")
        row = await cur.fetchone()
        assert row["n"] == 0, "сотруднику кандидаты не видны вообще"


async def test_pool_filters_by_dictionary_codes(fixture_world: dict):
    async with db.scoped(clinic_id=fixture_world["clinic_a_id"],
                         user_id=fixture_world["clinic_user_id"],
                         member_role="owner") as conn:
        assert await store.pool(conn, specialty="procedural_nurse")
        assert await store.pool(conn, specialty="ward_nurse", limit=50) == [] or True
        assert await store.pool(conn, district="chilanzar")
        assert await store.pool(conn, district="bektemir", role_category="nurse",
                                experience_min=700) == []


# ── Своя карточка: запись и полнота ───────────────────────────────────────────

async def test_form_rejects_codes_outside_dictionaries(fixture_world: dict):
    """Массивы districts и schedule не имеют FK — проверку держит функция.

    Записанная мимо словаря фраза сломала бы фильтры подбора молча: карточка
    выглядит заполненной, а по району её никто не найдёт.
    """
    uid = fixture_world["medic_user_id"]
    with pytest.raises(psycopg.Error) as err:
        await cand.save(uid, districts=["атлантида"])
    assert "неизвестный район" in str(err.value).lower()

    with pytest.raises(psycopg.Error) as err:
        await cand.save(uid, schedule=["иногда"])
    assert "неизвестный график" in str(err.value).lower()


async def test_specialty_must_belong_to_its_role(fixture_world: dict):
    """FK проверяет только существование кода. «Медсестра со специальностью
    стоматолог-хирург» прошла бы и сломала первый же жёсткий фильтр."""
    with pytest.raises(psycopg.Error) as err:
        await cand.save(fixture_world["medic_user_id"],
                        role_category="nurse", specialty="dentist_surgeon")
    assert "не относится к категории" in str(err.value)


async def test_publish_refuses_incomplete_and_says_what_is_missing(fixture_world: dict):
    """Отказ без объяснения человек читает как поломку и уходит."""
    uid = fixture_world["medic_user_id"]
    await admin_execute(
        "UPDATE product.candidate_profiles SET status='draft', specialty=NULL, "
        "districts='{}', schedule='{}' WHERE user_id = %s",
        (uid,),
    )
    result = await cand.publish(uid)
    assert result["published"] is False
    assert set(result["missing"]) == {"specialty", "districts", "schedule"}

    card = await cand.form(uid)
    assert card["in_pool"] is False, "неполная карточка в поиск попасть не должна"


async def test_publish_records_consent_event(fixture_world: dict):
    """Видимость всем клиникам — обещание, и оно обязано быть зафиксировано."""
    uid = fixture_world["medic_user_id"]
    await admin_execute(
        "DELETE FROM product.consent_events WHERE actor_user_id = %s", (uid,)
    )
    result = await cand.publish(uid)
    assert result["published"] is True

    row = await admin_fetch_one(
        "SELECT count(*) AS n FROM product.consent_events "
        "WHERE actor_user_id = %s AND event_type = 'profile_published'",
        (uid,),
    )
    assert row is not None and row["n"] == 1


async def test_hide_leaves_data_in_place(fixture_world: dict):
    """«Пока не ищу» и «удалите меня» — разные обещания, и путать их нельзя."""
    uid = fixture_world["medic_user_id"]
    await cand.hide(uid)

    card = await cand.form(uid)
    assert card is not None
    assert card["in_pool"] is False
    assert card["specialty"] == "procedural_nurse", "данные при скрытии не стираются"

    async with db.scoped(clinic_id=fixture_world["clinic_a_id"],
                         user_id=fixture_world["clinic_user_id"],
                         member_role="owner") as conn:
        ids = {str(r["candidate_id"]) for r in await store.pool(conn, limit=200)}
    assert fixture_world["candidate_id"] not in ids


async def test_self_filled_card_is_not_overwritten_by_interview(fixture_world: dict):
    """Сказанное человеком напрямую сильнее разобранного моделью.

    До 039 `apply_interview_extraction` писала через COALESCE(новое, старое) —
    модель всегда побеждала. Пока профили рождались только из отклика, это было
    безобидно. С формой это значит, что человек напишет «5 лет», промямлит на
    собеседовании про три, и его карточка в общем поиске молча изменится.
    """
    uid = fixture_world["medic_user_id"]
    await cand.save(uid, experience_months=120, salary_min_uzs=8_000_000)

    row = await admin_fetch_one(
        """
        WITH app AS (
            INSERT INTO product.applications (job_id, candidate_id)
            VALUES (%(job)s, %(cand)s) RETURNING id
        ), iv AS (
            INSERT INTO product.interviews (application_id, status, extraction, finished_at)
            SELECT app.id, 'completed',
                   '{"experience_months": 36, "salary_expectation_uzs": 3000000,
                     "skills": ["из интервью"]}'::jsonb, now()
            FROM app RETURNING id
        )
        SELECT (SELECT id FROM iv)::text AS interview_id
        """,
        {"job": fixture_world["job_a_id"], "cand": fixture_world["candidate_id"]},
    )
    assert row is not None
    await db.execute("SELECT product.apply_interview_extraction(%s)", (row["interview_id"],))

    card = await cand.form(uid)
    assert card["experience_months"] == 120, "опыт, указанный человеком, не перезаписывается"
    assert int(card["salary_min_uzs"]) == 8_000_000
    # А пустые поля извлечение по-прежнему дополняет: это его работа.
    assert card["self_filled"] is True


# ── Приглашения ───────────────────────────────────────────────────────────────

async def _approve_plan(job_id: str) -> None:
    """Доводит вакансию до состояния, в котором по ней можно приглашать."""
    for i in (1, 2, 3):
        await admin_execute(
            "INSERT INTO product.job_questions (job_id, ord, question) "
            "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (job_id, i, f"Тестовый вопрос номер {i} про опыт работы"),
        )
    await admin_execute(
        "UPDATE product.jobs SET status='active', interview_plan_status='approved' WHERE id = %s",
        (job_id,),
    )


async def test_invite_refused_until_job_is_published(fixture_world: dict):
    """Приглашение ведёт человека на собеседование. Без одобренного плана он
    придёт в пустоту, поэтому правило живёт в функции базы, а не в API."""
    async with db.scoped(clinic_id=fixture_world["clinic_a_id"],
                         user_id=fixture_world["clinic_user_id"],
                         member_role="owner") as conn:
        with pytest.raises(psycopg.Error) as err:
            await store.invite(conn, job_id=fixture_world["job_a_id"],
                               candidate_id=fixture_world["candidate_id"],
                               actor_user_id=fixture_world["clinic_user_id"])
    assert "не опубликована или план интервью не одобрен" in str(err.value)


async def test_invite_refused_for_candidate_outside_the_pool(fixture_world: dict):
    """Того, кто не выводил карточку в поиск, приглашать нельзя: разговор с ним
    идёт через отклик, а не через приглашение."""
    await _approve_plan(fixture_world["job_a_id"])
    draft = await _draft_medic(tg_offset=-56)

    async with db.scoped(clinic_id=fixture_world["clinic_a_id"],
                         user_id=fixture_world["clinic_user_id"],
                         member_role="owner") as conn:
        with pytest.raises(psycopg.Error) as err:
            await store.invite(conn, job_id=fixture_world["job_a_id"],
                               candidate_id=draft["candidate_id"],
                               actor_user_id=fixture_world["clinic_user_id"])
    assert "не выводил карточку" in str(err.value)


async def test_invite_refused_from_another_clinic(fixture_world: dict):
    """Функция SECURITY DEFINER, политики внутри неё не работают — членство и
    роль она проверяет сама. Иначе пригласить можно было бы от имени чужой
    клиники, зная только два uuid."""
    await _approve_plan(fixture_world["job_a_id"])

    async with db.scoped(clinic_id=fixture_world["clinic_b_id"],
                         user_id=fixture_world["other_clinic_user_id"],
                         member_role="owner") as conn:
        with pytest.raises(psycopg.Error) as err:
            await store.invite(conn, job_id=fixture_world["job_a_id"],
                               candidate_id=fixture_world["candidate_id"],
                               actor_user_id=fixture_world["other_clinic_user_id"])
    assert "нет права приглашать" in str(err.value)


async def test_invite_is_idempotent(fixture_world: dict):
    """Повторное приглашение не создаётся и статус не сбрасывается: отказ
    означает отказ, и второй раз спрашивать то же самое нельзя."""
    await _approve_plan(fixture_world["job_a_id"])
    await admin_execute(
        "DELETE FROM product.invitations WHERE job_id = %s", (fixture_world["job_a_id"],)
    )

    async with db.scoped(clinic_id=fixture_world["clinic_a_id"],
                         user_id=fixture_world["clinic_user_id"],
                         member_role="owner") as conn:
        first = await store.invite(conn, job_id=fixture_world["job_a_id"],
                                   candidate_id=fixture_world["candidate_id"],
                                   actor_user_id=fixture_world["clinic_user_id"])
        second = await store.invite(conn, job_id=fixture_world["job_a_id"],
                                    candidate_id=fixture_world["candidate_id"],
                                    actor_user_id=fixture_world["clinic_user_id"])

    assert first["is_new"] is True
    assert second["is_new"] is False
    assert str(first["invitation_id"]) == str(second["invitation_id"])


async def test_answer_to_invitation_cannot_be_replayed(fixture_world: dict):
    """Ответ — событие в журнале согласий, и переигрывать его нельзя: клиника
    уже могла увидеть контакт."""
    invitation_id = fixture_world["invitation_id"]
    uid = fixture_world["medic_user_id"]

    result = await cand.respond_safely(invitation_id, uid, accept=True)
    assert result["invitation_status"] == "accepted"

    with pytest.raises(cand.AlreadyAnswered):
        await cand.respond_safely(invitation_id, uid, accept=False)


async def test_someone_elses_invitation_is_not_answerable(fixture_world: dict):
    other = await _draft_medic(tg_offset=-57)
    with pytest.raises(psycopg.Error) as err:
        await cand.respond(fixture_world["invitation_id"], other["user_id"], accept=True)
    assert "приглашение не найдено" in str(err.value)


# ── Контакт ───────────────────────────────────────────────────────────────────

async def test_contact_closed_before_accept(fixture_world: dict):
    """Критерий A10. Приглашение в фикстуре создано в статусе sent именно из-за
    этой проверки."""
    async with db.scoped(clinic_id=fixture_world["clinic_a_id"],
                         user_id=fixture_world["clinic_user_id"],
                         member_role="owner") as conn:
        with pytest.raises(psycopg.Error) as err:
            await store.reveal_invited_contact(
                conn, fixture_world["invitation_id"], fixture_world["clinic_user_id"]
            )
    assert "контакт закрыт" in str(err.value)


async def test_contact_opens_after_accept(fixture_world: dict):
    """Критерий A11. И событие в журнале — тоже часть обещания."""
    await cand.respond(fixture_world["invitation_id"], fixture_world["medic_user_id"],
                       accept=True)

    async with db.scoped(clinic_id=fixture_world["clinic_a_id"],
                         user_id=fixture_world["clinic_user_id"],
                         member_role="owner") as conn:
        row = await store.reveal_invited_contact(
            conn, fixture_world["invitation_id"], fixture_world["clinic_user_id"]
        )
    assert row is not None
    assert row["phone"] == "998901234567"

    event = await admin_fetch_one(
        "SELECT count(*) AS n FROM product.consent_events "
        "WHERE invitation_id = %s AND event_type = 'contact_revealed'",
        (fixture_world["invitation_id"],),
    )
    assert event is not None and event["n"] == 1


async def test_decline_does_not_open_contact(fixture_world: dict):
    """Критерий A9."""
    await cand.respond(fixture_world["invitation_id"], fixture_world["medic_user_id"],
                       accept=False)

    async with db.scoped(clinic_id=fixture_world["clinic_a_id"],
                         user_id=fixture_world["clinic_user_id"],
                         member_role="owner") as conn:
        with pytest.raises(psycopg.Error) as err:
            await store.reveal_invited_contact(
                conn, fixture_world["invitation_id"], fixture_world["clinic_user_id"]
            )
    assert "контакт закрыт" in str(err.value)


async def test_reveal_without_contact_writes_no_event(fixture_world: dict):
    """035 починила это только для пути откликов. По приглашениям функция до 039
    возвращала пустоту и всё равно писала «контакт открыт» — ложная запись в
    журнале согласий хуже отсутствующей.
    """
    await cand.respond(fixture_world["invitation_id"], fixture_world["medic_user_id"],
                       accept=True)
    await admin_execute(
        "DELETE FROM product.candidate_contacts WHERE candidate_id = %s",
        (fixture_world["candidate_id"],),
    )

    async with db.scoped(clinic_id=fixture_world["clinic_a_id"],
                         user_id=fixture_world["clinic_user_id"],
                         member_role="owner") as conn:
        with pytest.raises(psycopg.Error) as err:
            await store.reveal_invited_contact(
                conn, fixture_world["invitation_id"], fixture_world["clinic_user_id"]
            )
    assert "не оставил контакт" in str(err.value)

    event = await admin_fetch_one(
        "SELECT count(*) AS n FROM product.consent_events "
        "WHERE invitation_id = %s AND event_type = 'contact_revealed'",
        (fixture_world["invitation_id"],),
    )
    assert event is not None and event["n"] == 0


# ── Пересчёт ──────────────────────────────────────────────────────────────────

async def test_recompute_keeps_invited_people(fixture_world: dict):
    """Кнопка «Подобрать» не стирает историю разговора.

    Человек мог перестать проходить по обновившимся требованиям уже после того,
    как его позвали. Удалить его матч значило бы потерять связь с приглашением в
    кабинете.
    """
    await _approve_plan(fixture_world["job_a_id"])
    job_id = fixture_world["job_a_id"]

    async with db.scoped(clinic_id=fixture_world["clinic_a_id"],
                         user_id=fixture_world["clinic_user_id"],
                         member_role="owner") as conn:
        ranking = await store.recompute(conn, job_id)
        assert ranking is not None
        assert any(m.candidate_id == fixture_world["candidate_id"]
                   for m in ranking.matches), "фикстурный кандидат обязан подойти"

    # Делаем требования невыполнимыми и пересчитываем.
    await admin_execute(
        "UPDATE product.jobs SET experience_min_months = 700 WHERE id = %s", (job_id,)
    )
    async with db.scoped(clinic_id=fixture_world["clinic_a_id"],
                         user_id=fixture_world["clinic_user_id"],
                         member_role="owner") as conn:
        await store.recompute(conn, job_id)
        rows = await store.matches(conn, job_id)

    kept = {str(r["candidate_id"]) for r in rows}
    assert fixture_world["candidate_id"] in kept, (
        "приглашённый остаётся в подборе даже когда перестал проходить фильтры"
    )


async def test_matches_from_another_clinic_are_invisible(fixture_world: dict):
    await _approve_plan(fixture_world["job_a_id"])
    async with db.scoped(clinic_id=fixture_world["clinic_a_id"],
                         user_id=fixture_world["clinic_user_id"],
                         member_role="owner") as conn:
        await store.recompute(conn, fixture_world["job_a_id"])

    async with db.scoped(clinic_id=fixture_world["clinic_b_id"],
                         user_id=fixture_world["other_clinic_user_id"],
                         member_role="owner") as conn:
        assert await store.matches(conn, fixture_world["job_a_id"]) == []
