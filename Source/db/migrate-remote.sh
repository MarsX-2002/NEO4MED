#!/usr/bin/env bash
# Накат миграций на сервер ezgumed-app-01 с машины разработчика.
# Доставляет актуальный код и запускает db/migrate.sh там под postgres.
#
# Почему не через туннель: CREATE EXTENSION требует суперпользователя, а
# наружу торчит только роль ezgumed. Суперпользователь остаётся локальным
# для сервера — это правильно, так его пароль вообще не ходит по сети.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="$(dirname "$HERE")"
HOST="${ISHMED_HOST:-ishmed}"
DEST=/opt/ezgumed

echo ">> доставляю миграции на $HOST"
rsync -az --delete \
  -e ssh \
  "$SRC_DIR/db/" "$HOST:$DEST/db/"

echo ">> прогоняю на сервере"
ssh "$HOST" "sudo -u postgres bash $DEST/db/migrate.sh ezgumed"
