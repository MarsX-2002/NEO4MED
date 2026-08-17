#!/usr/bin/env bash
# Генерирует пароль роли ishmed_app, ставит его на сервере и прописывает
# APP_DATABASE_URL в локальный .env (через туннель) и в deploy/.env.server (локально
# для сервера). Секрет не печатается и не попадает в миграции.
#
# Запускать после наката миграции 011.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(dirname "$HERE")"
HOST="${ISHMED_HOST:-ishmed}"

PW="$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-28)"

ssh "$HOST" "sudo -u postgres psql -v ON_ERROR_STOP=1 -q -d ezgumed \
  -c \"ALTER ROLE ishmed_app PASSWORD '$PW'\"" >/dev/null
echo "пароль ishmed_app установлен на сервере"

upsert() {
    local file="$1" key="$2" value="$3"
    touch "$file"
    if grep -q "^${key}=" "$file"; then
        python3 - "$file" "$key" "$value" <<'PY'
import sys, re, pathlib
f, k, v = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(f)
p.write_text(re.sub(rf'^{re.escape(k)}=.*$', f'{k}={v}', p.read_text(), flags=re.M))
PY
    else
        printf '%s=%s\n' "$key" "$value" >> "$file"
    fi
    chmod 600 "$file"
}

# Локально ходим через туннель, на сервере — напрямую в localhost.
upsert "$SRC_DIR/.env"             APP_DATABASE_URL "postgresql://ishmed_app:${PW}@127.0.0.1:15432/ezgumed"
upsert "$HERE/../deploy/.env.server" APP_DATABASE_URL "postgresql://ishmed_app:${PW}@127.0.0.1:5432/ezgumed"

echo "APP_DATABASE_URL прописан в .env и deploy/.env.server (значение не печатаю)"

export PGPASSWORD="$PW"
psql -h 127.0.0.1 -p 15432 -U ishmed_app -d ezgumed -tAc \
  "select 'проверка: подключение под '||current_user||' работает'"
