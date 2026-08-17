#!/usr/bin/env bash
# Раскладка IshMed на ezgumed-app-01: код, окружение, nginx, systemd.
#
# Секреты не коммитятся. Серверный .env собирается здесь из двух источников:
#   deploy/.env.server — всё, что специфично для сервера (доступ к БД, ключ сессий)
#   .env               — общее (Azure, Telegram, настройки продукта)
#
# Ключи БД берутся ТОЛЬКО из .env.server: локально мы ходим через туннель на
# порт 15432, на сервере — напрямую в 5432, и путать их нельзя.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$(dirname "$HERE")"
HOST="${ISHMED_HOST:-ishmed}"
DEST="/opt/ezgumed"
SSH=(ssh "$HOST")

[[ -f "$HERE/.env.server" ]] || { echo "нет deploy/.env.server"; exit 1; }
[[ -f "$SRC/.env" ]]         || { echo "нет .env"; exit 1; }

echo ">> каталог на сервере"
"${SSH[@]}" "sudo mkdir -p $DEST && sudo chown \$(whoami):\$(whoami) $DEST"

echo ">> синхронизация кода"
# Исключения rsync и .gitignore — разные списки, и это уже стоило нам 99 МБ
# node_modules и 19 МБ дампов базы на сервере. Собранный фронт уезжает отдельно
# в /var/www/ishmed-app, исходники фронта серверу вообще не нужны.
rsync -az --delete \
  --exclude '.venv' --exclude '.env' --exclude '__pycache__' --exclude '.pytest_cache' \
  --exclude '.ruff_cache' \
  --exclude 'deploy/.env.server' \
  --exclude 'frontend/node_modules' --exclude 'frontend/dist' \
  -e ssh "$SRC/" "$HOST:$DEST/"

# Файлы, исключённые из rsync, --delete не удаляет: их нужно убрать явно,
# иначе однажды попавший мусор останется на сервере навсегда.
"${SSH[@]}" "rm -rf $DEST/frontend/node_modules $DEST/frontend/dist $DEST/deploy/backups"

echo ">> сборка .env на сервере"
# Сборка окружения через ЧЁРНЫЙ список, а не белый.
#
# Раньше здесь был перечень разрешённых префиксов, и каждая новая переменная
# требовала о нём вспомнить. Дважды не вспомнил: сначала уехал без токена
# бота медиков, потом без токена бота отзывов, и оба раза сервис молча падал
# в цикле перезапуска. Теперь наоборот: берём из .env всё, кроме ключей,
# которые обязаны приходить со стороны сервера. Забыть новую переменную
# невозможно — она попадёт автоматически.
SERVER_KEYS='^(PGHOST|PGPORT|PGDATABASE|PGUSER|PGPASSWORD|DATABASE_URL|APP_DATABASE_URL|WEB_SECRET_KEY|WEB_BASE_URL)='
{
    grep -E "$SERVER_KEYS" "$HERE/.env.server"
    # Из локального .env: только строки вида КЛЮЧ=значение, без комментариев,
    # и без тех ключей, что уже пришли с серверной стороны.
    grep -E '^[A-Z][A-Z0-9_]*=' "$SRC/.env" | grep -vE "$SERVER_KEYS"
} | "${SSH[@]}" "umask 077 && cat > $DEST/.env && chmod 600 $DEST/.env"

EXPECTED=(APP_DATABASE_URL DATABASE_URL WEB_SECRET_KEY WEB_BASE_URL
          AZURE_OPENAI_ENDPOINT AZURE_OPENAI_API_KEY AZURE_OPENAI_API_VERSION
          AZURE_OPENAI_DIALOG_DEPLOYMENT AZURE_OPENAI_STT_DEPLOYMENT
          TELEGRAM_BOT_TOKEN REVIEW_BOT_TOKEN REVIEW_BOT_USERNAME CONSENT_VERSION)
missing="$("${SSH[@]}" "for k in ${EXPECTED[*]}; do grep -q \"^\$k=\" $DEST/.env || echo \$k; done")"
if [[ -n "$missing" ]]; then
    echo "   в серверном .env не хватает: $(echo "$missing" | tr '\n' ' ')"
    exit 1
fi
echo "   все обязательные переменные на месте"

echo ">> venv и зависимости"
"${SSH[@]}" "bash -lc '
set -e
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-venv python3-pip rsync >/dev/null
cd $DEST
[[ -d .venv ]] || python3 -m venv .venv
./.venv/bin/pip -q install --upgrade pip
./.venv/bin/pip -q install -r requirements.txt
./.venv/bin/python -c \"import aiogram, fastapi, langgraph, psycopg; print(\\\"зависимости ok\\\")\"
'"

echo ">> сборка фронтенда"
if [[ -d "$SRC/frontend" ]]; then
    ( cd "$SRC/frontend" && npm ci --silent 2>/dev/null || npm install --silent; npm run build >/dev/null )
    # Каталог остаётся за деплоящим пользователем, а не за www-data: иначе
    # следующий rsync не сможет туда писать. nginx статику только читает,
    # для этого достаточно прав на чтение.
    "${SSH[@]}" "sudo mkdir -p /var/www/ishmed-app \
                 && sudo chown -R \$(whoami):\$(whoami) /var/www/ishmed-app"
    # --delete обязателен: старые ассеты с прежними хэшами иначе копятся вечно.
    rsync -az --delete -e ssh "$SRC/frontend/dist/" "$HOST:/var/www/ishmed-app/"
    "${SSH[@]}" "chmod -R a+rX /var/www/ishmed-app"
    echo "   собран и разложен в /var/www/ishmed-app"
fi

echo ">> nginx"
"${SSH[@]}" "sudo cp $DEST/deploy/nginx/ishmed.conf /etc/nginx/sites-available/ishmed.conf \
  && sudo ln -sf /etc/nginx/sites-available/ishmed.conf /etc/nginx/sites-enabled/ishmed.conf \
  && sudo nginx -t && sudo systemctl reload nginx && echo 'nginx перезагружен'"

echo ">> systemd"
"${SSH[@]}" "sudo cp $DEST/deploy/systemd/ishmed-web.service $DEST/deploy/systemd/ishmed-bot.service $DEST/deploy/systemd/ishmed-review-bot.service /etc/systemd/system/ \
  && sudo systemctl daemon-reload && echo 'юниты обновлены'"

echo "Готово: $DEST"
echo "Дальше:  make srv-web-up   /  make srv-bot-up"
