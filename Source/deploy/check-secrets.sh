#!/usr/bin/env bash
# Проверка, что в коммит не уезжают секреты.
#
# До сих пор я делал это вручную перед каждым пушем. Ручная проверка работает
# ровно до первого раза, когда о ней забудут, а токен из истории git убирается
# только перезаписью истории.
#
# Использование:
#   ./deploy/check-secrets.sh            проверить staged-изменения
#   ./deploy/check-secrets.sh --all      проверить всё дерево
#
# Поставить хуком (хуки не коммитятся, поэтому вручную):
#   ln -sf ../../Source/deploy/check-secrets.sh .git/hooks/pre-commit
set -uo pipefail

MODE="${1:-staged}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Шаблоны того, что действительно нельзя публиковать.
# Именно значения, а не имена переменных: имена в .env.example нужны.
PATTERNS=(
    '[0-9]{9,12}:AA[A-Za-z0-9_-]{30,}'                  # токен Telegram
    'AZURE_OPENAI_API_KEY=[A-Za-z0-9]{20,}'             # ключ Azure
    'PGPASSWORD=[A-Za-z0-9]{8,}'                        # пароль Postgres
    'postgresql://[^:]+:[A-Za-z0-9]{8,}@'               # пароль в DSN
    'WEB_SECRET_KEY=[A-Za-z0-9+/]{20,}'                 # ключ подписи сессий
    'DEMO_CLINIC_PASSWORD=[^[:space:]]+'                # пароль демо-аккаунта
    '-----BEGIN [A-Z ]*PRIVATE KEY-----'                # приватные ключи
)

RE="$(IFS='|'; echo "${PATTERNS[*]}")"

# Сам этот скрипт содержит шаблоны и потому всегда «находит себя».
# Исключаем его pathspec'ом, а не отключением правил.
SELF=':(exclude)Source/deploy/check-secrets.sh'

if [[ "$MODE" == "--all" ]]; then
    payload="$(git ls-files -z -- . "$SELF" | xargs -0 grep -InE "$RE" 2>/dev/null)"
else
    payload="$(git diff --cached -U0 -- . "$SELF" | grep -E '^\+' | grep -InE "$RE" 2>/dev/null)"
fi

if [[ -n "$payload" ]]; then
    echo "СТОП: похоже на секрет в изменениях."
    echo "$payload" | sed -E 's/(=|:)[A-Za-z0-9+\/_-]{6,}/\1***/g' | head -20 | sed 's/^/  /'
    echo
    echo "Если это ложная тревога (например, шаблон CHECK-ограничения),"
    echo "поправьте шаблон в deploy/check-secrets.sh, а не отключайте проверку."
    exit 1
fi

# Файлы, которых в индексе быть не должно ни при каких условиях.
FORBIDDEN=(Source/.env Source/deploy/.env.server)
bad=0
for f in "${FORBIDDEN[@]}"; do
    if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
        echo "СТОП: $f отслеживается git. Уберите: git rm --cached $f"
        bad=1
    fi
done
[[ $bad -eq 0 ]] || exit 1

echo "секретов не найдено"
