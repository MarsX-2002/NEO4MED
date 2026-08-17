#!/usr/bin/env bash
# Прогон миграций по порядку с учётом уже применённых.
#
# Выполняется НА ТОМ ЖЕ ХОСТЕ, где живёт кластер, под суперпользователем:
# CREATE EXTENSION иначе не пройдёт. С машины разработчика запускай
# db/migrate-remote.sh — он доставит код на сервер и вызовет этот скрипт там.
set -euo pipefail

DB="${1:-ezgumed}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/migrations"

# Путь к psql: Ubuntu-сервер против macOS (где остался только клиент libpq)
for p in /usr/lib/postgresql/18/bin /opt/homebrew/opt/libpq/bin; do
    [[ -d "$p" ]] && export PATH="$p:$PATH"
done

# Если в окружении висят PGUSER/PGPASSWORD прикладной роли (например, после
# source .env), права на CREATE EXTENSION потеряются. Чистим и идём под
# суперпользователем текущего хоста: nga на маке, postgres на сервере.
unset PGPASSWORD PGDATABASE PGSERVICE PGHOST PGPORT
export PGUSER="${PGSUPERUSER:-$(whoami)}"

# md5 на macOS, md5sum на Linux
md5of() {
    if command -v md5 >/dev/null 2>&1; then md5 -q "$1"
    else md5sum "$1" | cut -d' ' -f1; fi
}

psql -d "$DB" -v ON_ERROR_STOP=1 -q <<'SQL'
CREATE TABLE IF NOT EXISTS public.schema_migrations (
    filename    text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    checksum    text NOT NULL
);
SQL

for f in "$DIR"/*.sql; do
    name="$(basename "$f")"
    sum="$(md5of "$f")"
    applied="$(psql -d "$DB" -tAc "SELECT checksum FROM public.schema_migrations WHERE filename='$name'")"

    if [[ -n "$applied" ]]; then
        if [[ "$applied" != "$sum" ]]; then
            echo "  !! $name изменился после применения (было $applied, стало $sum)"
            echo "     Миграции иммутабельны: добавь новый файл, а не правь старый."
            exit 1
        fi
        echo "  ·· $name (уже применён)"
        continue
    fi

    echo "  >> $name"
    psql -d "$DB" -v ON_ERROR_STOP=1 -q -f "$f"
    psql -d "$DB" -q -c \
        "INSERT INTO public.schema_migrations(filename, checksum) VALUES ('$name', '$sum')"
done

echo "Миграции применены."
