PY   := ./.venv/bin/python
PSQL := psql

.PHONY: lint lint-fix secrets verify test-remote
.PHONY: help tunnel tunnel-up tunnel-down tunnel-status psql db-info ssh \
        migrate deploy azure-status backup-list \
        bot web test check

help:
	@echo "Код локально, база на сервере через SSH-туннель 127.0.0.1:15432"
	@echo ""
	@echo "  bot             — запустить @ishmedbot (polling)"
	@echo "  web             — запустить кабинет клиники на :8000 с autoreload"
	@echo "  test            — прогнать тесты локально (через туннель)"
	@echo "  test-remote     — прогнать тесты на сервере (быстрее и без туннеля)"
	@echo "  lint            — ruff по всему бэкенду"
	@echo "  secrets         — проверить, что в коммит не уезжают секреты"
	@echo "  check           — конфиг, импорты, связь с базой и Azure"
	@echo ""
	@echo "  tunnel-status   — состояние туннеля и связи с базой"
	@echo "  tunnel-up/down  — включить/выключить LaunchAgent туннеля"
	@echo "  psql            — консоль в удалённую базу через туннель"
	@echo "  db-info         — где мы и что в базе"
	@echo "  ssh             — шелл на сервере"
	@echo ""
	@echo "  migrate         — накатить миграции на сервере (нужен суперпользователь)"
	@echo ""
	@echo "  deploy          — залить код на сервер"
	@echo "  backup-list     — список ночных дампов на сервере"
	@echo "  azure-status    — деплойменты Azure OpenAI"
	@echo ""
	@echo "ВАЖНО: polling у Telegram single-consumer. Бот запускается либо тут,"
	@echo "либо на сервере — одновременно нельзя, апдейты начнут теряться."

# Telegram отдаёт апдейты только одному потребителю. Если бот уже поднят на
# сервере, локальный запуск начнёт воровать у него сообщения — поэтому проверяем
# и требуем сначала погасить серверный.
bot:
	@if ssh ishmed 'systemctl is-active --quiet ishmed-bot' 2>/dev/null; then \
	  echo "На сервере уже работает ishmed-bot. Два polling-потребителя несовместимы."; \
	  echo "Сначала: make srv-bot-down"; exit 1; fi
	$(PY) -m app.bot.main

web:
	./.venv/bin/uvicorn app.web.main:app --host 127.0.0.1 --port 8000 --reload

test:
	$(PY) -m pytest

# Прогон на сервере. Пятиминутный набор через SSH-туннель хрупок: один обрыв
# роняет пачку тестов с «SSL SYSCALL error», хотя код в порядке. На сервере
# база локальная, туннеля в пути нет.
test-remote:
	rsync -az --delete -e ssh tests/ ishmed:/opt/ezgumed/tests/
	rsync -az -e ssh pytest.ini ishmed:/opt/ezgumed/pytest.ini
	ssh ishmed 'cd /opt/ezgumed && ./.venv/bin/python -m pytest -q'

lint:
	./.venv/bin/ruff check .

lint-fix:
	./.venv/bin/ruff check --fix .

secrets:
	./deploy/check-secrets.sh

# Что прогонять перед пушем.
verify: lint secrets test

check:
	@$(PY) -c "from app.config import settings; s=settings(); \
	print('конфиг: ok, роль', s.dsn.split('://')[1].split(':')[0], '| диалог', s.dialog_deployment, '| STT', s.stt_deployment)"
	@$(PY) -c "import asyncio, json; from app import db; \
	print('база:  ', json.dumps(asyncio.run(db.healthcheck()), ensure_ascii=False, default=str))"
	@set -a; . ./.env; set +a; \
	  printf 'api-key: %s\n' "$$AZURE_OPENAI_API_KEY" > /tmp/.ishmed-hdr && chmod 600 /tmp/.ishmed-hdr; \
	  curl -sS -m 15 -o /dev/null -w "azure: HTTP %{http_code}\n" \
	    -X POST "$$AZURE_OPENAI_ENDPOINT/openai/deployments/$$AZURE_OPENAI_DIALOG_DEPLOYMENT/chat/completions?api-version=$$AZURE_OPENAI_API_VERSION" \
	    -H "Content-Type: application/json" -H @/tmp/.ishmed-hdr \
	    -d '{"messages":[{"role":"user","content":"ping"}],"max_completion_tokens":16,"reasoning_effort":"minimal"}'; \
	  rm -f /tmp/.ishmed-hdr

tunnel-status:
	@printf "агент:   "; launchctl list 2>/dev/null | grep -q ishmed && echo "загружен" || echo "НЕ загружен"
	@printf "порт:    "; lsof -nP -iTCP:15432 -sTCP:LISTEN >/dev/null 2>&1 && echo "15432 слушается" || echo "15432 НЕ слушается"
	@set -a; . ./.env; set +a; printf "база:    "; \
	  $(PSQL) "$$DATABASE_URL" -tAc "select 'ok, PostgreSQL '||current_setting('server_version')" 2>/dev/null || echo "НЕДОСТУПНА"

tunnel-up:
	launchctl load -w ~/Library/LaunchAgents/uz.ezgupro.ishmed.tunnel.plist

tunnel-down:
	launchctl unload ~/Library/LaunchAgents/uz.ezgupro.ishmed.tunnel.plist

tunnel: tunnel-status

psql:
	@set -a; . ./.env; set +a; $(PSQL) "$$DATABASE_URL"

db-info:
	@set -a; . ./.env; set +a; $(PSQL) "$$DATABASE_URL" -x -c "\
	SELECT inet_server_addr()::text||':'||inet_server_port() AS server, \
	       current_user, current_setting('server_version') AS pg, \
	       pg_size_pretty(pg_database_size(current_database())) AS size;"

ssh:
	ssh ishmed

migrate:
	./db/migrate-remote.sh

deploy:
	./deploy/deploy.sh

backup-list:
	@ssh ishmed 'sudo ls -lh /var/backups/ezgumed/'

azure-status:
	@az cognitiveservices account deployment list \
		-n ezgumed-ai-swc -g ezgumed-ai-rg \
		--query "[].{deployment:name,model:properties.model.name,version:properties.model.version,sku:sku.name,tpm:sku.capacity,state:properties.provisioningState}" \
		-o table

# ── Управление сервисами на сервере ───────────────────────────────────────────
.PHONY: srv-status srv-logs srv-web-up srv-web-down srv-bot-up srv-bot-down srv-review-bot-up srv-review-bot-down \
        srv-restart urls

srv-status:
	@ssh ishmed 'for s in ishmed-web ishmed-bot ishmed-review-bot nginx postgresql; do \
	  printf "  %-14s %s\n" "$$s" "$$(systemctl is-active $$s)"; done'
	@echo ""
	@for u in https://app.ishmed.ezgupro.uz/ https://api.ishmed.ezgupro.uz/ https://ishmed.ezgupro.uz/; do \
	  printf "  %-38s HTTP %s\n" "$$u" "$$(curl -sS -o /dev/null -w '%{http_code}' -m 15 $$u)"; done

srv-logs:
	@ssh ishmed 'sudo journalctl -u ishmed-web -u ishmed-bot -u ishmed-review-bot -n 40 --no-pager -o short-iso'

srv-web-up:
	ssh ishmed 'sudo systemctl restart ishmed-web && sleep 2 && systemctl is-active ishmed-web'

srv-web-down:
	ssh ishmed 'sudo systemctl stop ishmed-web && systemctl is-active ishmed-web || true'

srv-bot-up:
	ssh ishmed 'sudo systemctl restart ishmed-bot && sleep 3 && systemctl is-active ishmed-bot'

srv-review-bot-up:
	ssh ishmed 'sudo systemctl restart ishmed-review-bot && sleep 3 && systemctl is-active ishmed-review-bot'

srv-review-bot-down:
	ssh ishmed 'sudo systemctl stop ishmed-review-bot && systemctl is-active ishmed-review-bot || true'

srv-bot-down:
	ssh ishmed 'sudo systemctl stop ishmed-bot && systemctl is-active ishmed-bot || true'

srv-restart: srv-web-up srv-bot-up srv-review-bot-up

urls:
	@echo "  лендинг         https://ishmed.ezgupro.uz        (статика, заливает основатель)"
	@echo "  кабинет клиники https://app.ishmed.ezgupro.uz"
	@echo "  api             https://api.ishmed.ezgupro.uz"
	@echo "  бот медиков     https://t.me/ishmedbot"

# ── Бэкапы ────────────────────────────────────────────────────────────────────
# Ночные дампы лежат на ТОМ ЖЕ диске, что и база. От логической ошибки это
# спасает, от потери диска или VM — нет. Поэтому свежий дамп надо забирать
# с сервера: deploy/backups/ — единственная копия вне машины.
.PHONY: backup-pull backup-status

backup-pull:
	@mkdir -p deploy/backups
	@latest=$$(ssh ishmed 'sudo ls -1t /var/backups/ezgumed/*.dump | head -1'); \
	  name=$$(basename $$latest); \
	  echo "забираю $$name"; \
	  ssh ishmed "sudo cat $$latest" > deploy/backups/$$name; \
	  ls -lh deploy/backups/$$name
	@echo "локальных копий: $$(ls -1 deploy/backups/*.dump 2>/dev/null | wc -l | tr -d ' ')"
	@ls -1t deploy/backups/*.dump 2>/dev/null | tail -n +4 | while read f; do \
	  echo "удаляю старую копию $$(basename $$f)"; rm -f "$$f"; done

backup-status:
	@echo "на сервере (тот же диск, что у базы):"
	@ssh ishmed 'sudo ls -lh /var/backups/ezgumed/ | tail -n +2 | awk "{print \"  \"\$$9\"  \"\$$5}"'
	@ssh ishmed 'systemctl list-timers ezgumed-backup.timer --no-pager | sed -n 2p | sed "s/^/  таймер: /"'
	@echo "вне сервера (локально):"
	@ls -lh deploy/backups/*.dump 2>/dev/null | awk '{print "  "$$9"  "$$5}' || echo "  НЕТ КОПИЙ ВНЕ СЕРВЕРА"

# ── Демо-данные ───────────────────────────────────────────────────────────────
# Сид и reset идут под ВЛАДЕЛЬЦЕМ базы: RLS намеренно скрывает от прикладной
# роли всё, для чего не выставлен контекст тенанта, поэтому её правами
# засеять данные нельзя. Импортированный каталог raw/core/ai не затрагивается.
.PHONY: seed seed-dental seed-portal seed-course seed-course-reset seed-all demo-reset

seed:
	$(PY) db/seed_demo.py

seed-dental:
	$(PY) db/seed_dental.py

# Тестовый сотрудник с входом в портал: курсы и отзывы о себе.
# Требует seed-dental — доступ выдаётся человеку, который уже в штате.
seed-portal:
	$(PY) db/seed_employee_portal.py

# Тестовый курс с материалом и тестом плюс прохождения сотрудников.
# Без --reset ничего не делает, если курс уже есть: повторный сид не должен
# незаметно затирать чьи-то настоящие попытки.
seed-course:
	$(PY) db/seed_course.py

# Пересобрать курс с нуля: снимает прежние назначения и попытки демо-клиники.
seed-course-reset:
	$(PY) db/seed_course.py --reset

# Двадцать явно синтетических карточек медиков в общий поиск. Нужен, чтобы на
# репетиции раздел «Подбор» было чем наполнить: живые люди заводят карточки
# сами через бота, и до первых из них пул пуст.
seed-pool:
	$(PY) db/seed_pool.py

seed-pool-reset:
	$(PY) db/seed_pool.py --reset

# Всё демо целиком, в правильном порядке.
seed-all: seed seed-dental seed-course seed-portal seed-pool

# Сквозной прогон авто-интервью настоящими вызовами Azure. Требование
# «демо проходит три раза подряд» проверяется буквально: три кандидата,
# три полных собеседования, отчёт по каждому.
demo-interview:
	PYTHONPATH=. $(PY) tools/demo_interview.py --runs 3

# То же, но кандидаты остаются в базе — чтобы в кабинете было что показать.
demo-interview-keep:
	PYTHONPATH=. $(PY) tools/demo_interview.py --runs 2 --keep

demo-reset:
	@echo "Очистит ВСЕ продуктовые данные (клиники, профили, приглашения)."
	@echo "Каталог из 3595 вакансий и медики с зафиксированным согласием сохранятся."
	@read -p "Введи yes для подтверждения: " ok; [ "$$ok" = "yes" ]
	$(PY) db/seed_demo.py --reset

# ── Фронтенд кабинета (React) ─────────────────────────────────────────────────
.PHONY: front-install front-dev front-build front-check

front-install:
	cd frontend && npm install

# Dev-сервер Vite на :5173 с прокси /api на :8000.
# Запускать вместе с `make web` в соседнем терминале.
front-dev:
	cd frontend && npm run dev

front-build:
	cd frontend && npm run build

front-check:
	cd frontend && npx tsc -b
