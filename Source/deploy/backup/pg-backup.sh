#!/usr/bin/env bash
# Ночной дамп базы ezgumed. Хранится 7 копий, дальше ротация.
# Живёт на том же диске: это защита от логической ошибки (снёс таблицу),
# а не от потери диска. Второй контур — Azure Backup на уровне VM.
set -euo pipefail

DIR=/var/backups/ezgumed
KEEP=7
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
FILE="$DIR/ezgumed-$STAMP.dump"

mkdir -p "$DIR"
pg_dump -d ezgumed -Fc -Z6 -f "$FILE"

SIZE=$(du -h "$FILE" | cut -f1)
logger -t ezgumed-backup "дамп готов: $FILE ($SIZE)"

ls -1t "$DIR"/ezgumed-*.dump 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    rm -f "$old"
    logger -t ezgumed-backup "удалён старый дамп: $old"
done
