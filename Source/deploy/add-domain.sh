#!/usr/bin/env bash
# Добавить имя в сертификат и в nginx.
#   ./deploy/add-domain.sh api.ishmed.ezgupro.uz www.ishmed.ezgupro.uz
#
# ВАЖНО: перед запуском убедись, что имя резолвится с ОБОИХ авторитативных
# NS зоны. Let's Encrypt проверяет домен из нескольких точек мира, и если
# один из NS отдаёт NXDOMAIN, выпуск падает.
#   dig +norecurse @ns1.hostmaster.uz    A <имя> +short
#   dig +norecurse @revers.hostmaster.uz A <имя> +short
set -euo pipefail

[[ $# -gt 0 ]] || { echo "укажи хотя бы одно имя"; exit 1; }

KEY="$HOME/.ssh/ezgumed_ed25519"
HOST="135.116.56.220"
CERT="ishmed.ezgupro.uz"

echo ">> проверяю резолвинг на обоих NS"
fail=0
for d in "$@"; do
    for ns in ns1.hostmaster.uz revers.hostmaster.uz; do
        ip=$(dig +norecurse "@$ns" A "$d" +short 2>/dev/null | head -1)
        if [[ -z "$ip" ]]; then
            echo "   ПРОБЛЕМА: $ns не знает $d"
            fail=1
        else
            echo "   ok: $ns -> $d = $ip"
        fi
    done
done
[[ $fail -eq 0 ]] || { echo "Выпуск не начинаю: зона не синхронизирована между NS."; exit 1; }

ARGS=(-d "$CERT")
for d in "$@"; do ARGS+=(-d "$d"); done

ssh -i "$KEY" -o StrictHostKeyChecking=no "azureuser@$HOST" \
  "sudo sed -i 's/^\(\s*server_name\s\+ishmed.ezgupro.uz.*\)$/\1/' /etc/nginx/sites-available/ishmed.conf; \
   sudo certbot --nginx --non-interactive --agree-tos -m i@ezgupro.uz --redirect \
     --cert-name $CERT --expand ${ARGS[*]} && sudo certbot certificates | grep -E 'Domains|Expiry'"
