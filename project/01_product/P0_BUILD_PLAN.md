# IshMed P0 — Telegram MVP Build Plan

**Статус:** единственный инженерный scope до demo freeze.  
**Цель:** один работающий mutual contact между клиникой и медсестрой через Telegram.

## 1. Definition of Done

Без ручного изменения БД проходят два Telegram-сценария:

### Медик

`/start → язык → согласие → роль «Медик» → голос → transcript → карточка → подтверждение → профиль active → приглашение → accept/decline`.

### Клиника

`/start → роль «Клиника» → demo access → текст вакансии → карточка → подтверждение → список match → invite → ожидание ответа → открытый контакт после accept`.

После перезапуска бота состояние сохраняется. До `invite + accept` приватный контакт нельзя получить ни кнопкой, ни прямым запросом к сервисному слою.

## 2. P0 пользовательский контракт

### 2.1 `/start`

- Выбор языка: русский / o‘zbekcha.
- Короткое понятное согласие на обработку данных.
- Выбор роли: `Я медик` / `Я клиника`.
- Telegram user ID используется как identity P0.

### 2.2 Медик

Бот просит одно голосовое сообщение с:

- профессией/специальностью;
- опытом;
- ключевыми навыками;
- языками;
- городом и желательными районами;
- графиком;
- минимальной зарплатой.

После extraction бот показывает все поля. Кнопки:

- `Подтвердить`;
- `Изменить поле`;
- `Записать заново`;
- `Скрыть профиль`.

Контакт запрашивается отдельным Telegram `request_contact` либо сохраняется как username, только после явного действия пользователя. Контакт не входит в публичную карточку.

### 2.3 Клиника

P0-доступ — clinic allowlist или invite code. Клиника вставляет текст одной вакансии. AI извлекает поля, пользователь подтверждает или исправляет их.

Клиника получает до пяти анонимных карточек. В каждой:

- уровень `Сильное / Возможное совпадение`;
- 2–3 причины;
- до двух вопросов/пробелов;
- кнопка `Пригласить`.

### 2.4 Согласие

- `invite` = зафиксированный интерес клиники;
- `accept` = согласие медика;
- `decline` ничего не раскрывает;
- только `invite + accept` создаёт `mutually_revealed`;
- после reveal обе стороны получают разрешённый контакт и дальнейшую инструкцию.

## 3. Структура данных

Создать отдельную схему `product`. Не изменять смысл `core.vacancies`.

Минимальные таблицы:

- `product.users` — UUID, telegram_user_id unique, role, locale, consent_at, created_at;
- `product.clinics` — UUID, owner_user_id, name, access_status;
- `product.candidate_profiles` — UUID, user_id, role_category, specialty, experience_months, skills, languages, city, districts, schedule, salary_min_uzs, credential_claims, status, source, timestamps;
- `product.candidate_contacts` — candidate_id, phone, telegram_username, visibility;
- `product.jobs` — UUID, clinic_id, title, role_category, specialty, experience_min_months, required_skills, required_languages, city, districts, schedule, salary_min_uzs, salary_max_uzs, credential_requirements, status, source_text, timestamps;
- `product.intake_sessions` — user_id, kind, state, audio reference, transcript, extraction payload, error, timestamps;
- `product.matches` — job_id, candidate_id, level, score_internal, hard_constraints_passed, reasons, gaps, algorithm_version, created_at;
- `product.invitations` — job_id, candidate_id, status, sent_at, responded_at;
- `product.consent_events` — invitation_id, actor_user_id, event_type, created_at.

Использовать UUID. Приватная таблица контактов не должна попадать в обычный candidate query/serializer.

## 4. Extraction schemas

LLM возвращает только валидный JSON. Неизвестное значение — `null`/пустой список, не догадка.

### Candidate extraction

```json
{
  "role_category": "nurse",
  "specialty": "procedural_nurse",
  "experience_months": 48,
  "skills": ["injections", "iv_therapy"],
  "languages": ["uz", "ru"],
  "city": "tashkent",
  "districts": ["chilanzar", "uchtepa"],
  "schedule": ["shift"],
  "salary_min_uzs": 4000000,
  "credential_claims": [],
  "questions": []
}
```

### Job extraction

```json
{
  "title": "Процедурная медсестра",
  "role_category": "nurse",
  "specialty": "procedural_nurse",
  "experience_min_months": 24,
  "required_skills": ["injections", "iv_therapy"],
  "required_languages": ["uz", "ru"],
  "city": "tashkent",
  "districts": ["chilanzar"],
  "schedule": ["shift"],
  "salary_min_uzs": 4000000,
  "salary_max_uzs": 6000000,
  "credential_requirements": [],
  "questions": []
}
```

Перед сохранением пользователь видит нормализованные человекочитаемые значения и может их изменить.

## 5. Matching v1

Matching — отдельная чистая функция/сервис с тестами. Embeddings не нужны для принятия P0-решения.

### Hard fail

- другая `role_category`;
- другой город без готовности кандидата;
- несовместимый график;
- зарплата вакансии ниже минимума кандидата;
- отсутствует обязательный язык;
- отсутствует обязательный self-reported credential claim.

### Ranking после hard filters

- точная specialty: 35;
- достаточный опыт: 20;
- overlap required skills: до 20;
- предпочтительный район: 10;
- языки сверх обязательного: до 5;
- полнота профиля: до 10.

Порог и веса можно вынести в config. Число score остаётся внутренним; пользователю показываются level, reasons и gaps. Каждый показанный match обязан иметь минимум две конкретные причины.

## 6. AI и fallback

Три независимых адаптера:

- `SpeechToText.transcribe(audio) -> transcript`;
- `CandidateExtractor.extract(transcript) -> candidate payload`;
- `JobExtractor.extract(text) -> job payload`.

### Live path

- Extraction использует существующий Azure deployment `gpt-5`.
- STT provider/deployment на старте отсутствует; подключить после выдачи credential/deployment.

### Demo fallback

- заранее записанное audio имеет заранее проверенный transcript fixture;
- transcript может пройти через live extraction;
- при сбое extraction доступен заранее проверенный payload fixture;
- fallback включается явным `DEMO_MODE`, логируется и не маскируется как live AI в инженерных отчётах.

## 7. Порядок реализации

### P0.0 — Bootstrap

- зависимости и lock/requirements;
- `.env.example` без секретов;
- bot process, `/start`, health check/logging;
- русский и узбекский словари UI;
- команда запуска в README внутри `Source/`.

Готово, когда бот отвечает и соединяется с БД.

### P0.1 — Product schema и privacy

- migration `011_product.sql`;
- demo seed и безопасный reset только product schema;
- repository/service слой;
- тест, что hidden contact недоступен до mutual reveal.

Готово, когда миграция накатывается поверх существующих 10 миграций и не меняет `raw/core/ai`.

### P0.2 — Working spine без AI

- оба ролевых flow на кнопках/структурированных fixtures;
- профиль, вакансия, match, invite, accept/decline, reveal;
- состояние переживает restart.

Готово, когда полный сценарий проходит в Telegram без ручного SQL.

### P0.3 — Rules-first matching

- hard filters;
- ranking;
- reasons/gaps;
- unit tests, включая нерелевантного врача против процедурной медсестры.

Готово, когда правильная медсестра выше всех допустимых кандидатов, а врач исключён.

### P0.4 — Text и voice AI

- vacancy text extraction;
- transcript candidate extraction;
- voice download и STT adapter;
- экран подтверждения/исправления;
- fixture fallback.

Готово, когда основная demo-фраза корректно разбирается минимум 4 раза из 5, а пятый случай можно исправить.

### P0.5 — Demo freeze

- 20 явно синтетических профилей + один live/consent profile;
- один demo clinic и одна vacancy;
- `/demo_reset` только для admin allowlist;
- понятные ошибки и повтор шага;
- три полных прогона;
- backup transcript/payload;
- короткая инструкция оператору.

После freeze — только блокирующие исправления.

## 8. Acceptance criteria

- **A1:** `/start` и выбор роли работают на RU и UZ.
- **A2:** согласие фиксируется до приёма персональных данных.
- **A3:** voice/transcript превращается в редактируемый профиль.
- **A4:** clinic text превращается в редактируемую вакансию.
- **A5:** профиль и вакансия сохраняются после restart.
- **A6:** hard mismatch по роли исключается.
- **A7:** match показывает минимум две причины и вопросы/пробелы.
- **A8:** invite доставляется нужному Telegram user.
- **A9:** decline не раскрывает контакт.
- **A10:** до accept сервис не возвращает контакт.
- **A11:** после invite + accept контакт доступен обеим сторонам.
- **A12:** полный demo проходит без SQL и изменения кода.
- **A13:** reset восстанавливает demo state, не затрагивая импортированный каталог.
- **A14:** секреты отсутствуют в репозитории и логах.

## 9. Stop list

Не делать web-кабинеты, auto-posting, платежи, рейтинги, документы, аналитику, WFM/LMS/ATS, новые источники, редизайн pitch-site и улучшение embeddings. Это P1 либо отдельная задача.

## 10. Формат handoff Claude

После каждого этапа:

- что реально работает end-to-end;
- какие A1–A14 закрыты;
- команда запуска и тестов;
- миграции и новые env variables;
- один следующий этап;
- блокер, требующий действия основателя.

