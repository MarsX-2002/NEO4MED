# Claude — START HERE

Твоя единственная текущая задача — реализовать P0 Telegram MVP IshMed в `../../Source/`.

## Прочитать

1. `P0_BUILD_PLAN.md` — исполнимый контракт.
2. `../00_CURRENT_STATE.md` — проверенные факты.

Для понимания продукта, но не для расширения scope: `../00_MASTER_PLAN.md`.

Не читать `../../research/` как ТЗ. Не брать задачи из pitch/brand/validation документов. Не реализовывать `P1_ROADMAP.md`.

## Начать сейчас

1. Проверить текущий `Source/` и не ломать `raw/core/ai`.
2. Создать минимальный запускаемый Telegram bot skeleton с `/start` и выбором роли «Медик / Клиника».
3. Добавить migration `011_product.sql` и product schema из P0-плана.
4. Сначала провести полный сценарий на структурированных данных без AI.
5. Затем подключить vacancy extraction, candidate transcript extraction и speech-to-text.

Текущий блокер: в Azure есть `gpt-5` и `text-embedding-3-small`, но speech-to-text deployment/provider не настроен. Сразу создай интерфейс STT и demo fixture fallback; сообщи основателю, какой credential/deployment нужен для live audio. Не останавливай остальную реализацию из-за STT.

## Обязательный первый отчёт

Покажи:

- выбранную минимальную архитектуру и новые зависимости;
- список создаваемых файлов;
- migration `011`;
- команду запуска бота;
- как задать `TELEGRAM_BOT_TOKEN` и clinic allowlist/invite code;
- какие acceptance criteria P0 уже проходят;
- только реальные блокеры, без предложений P1.

