#!/bin/bash
# ============================================================
# CRIAR_WEBHOOKS — liga a campainha push->deploy nos repos
# ============================================================
# Cria o webhook (URL secreta + HMAC) em cada repo via API do
# GitHub. Idempotente: se ja existe, so avisa. Reutilizavel no
# proximo VPS (basta os 3 arquivos de config abaixo existirem).
#
# Requisitos no servidor:
#   ~/.github_token        (token com permissao Webhooks R/W)
#   ~/.vps_webhook_rota    (ex.: hook-xxxx)
#   ~/.vps_webhook_secret  (segredo HMAC)
#
# Uso: bash ~/vps-admin/criar_webhooks.sh
# ============================================================
set -e
USUARIO="diogobsbastos"
REPOS=(escola-parque vps-escola-parque-admin sertanejo-lab escola-parque-frontend)

TOKEN=$(cat ~/.github_token)
HOOKURL="https://oracle-vipworks.duckdns.org/$(cat ~/.vps_webhook_rota)/"
SECRET=$(cat ~/.vps_webhook_secret)

for repo in "${REPOS[@]}"; do
  printf "%-30s " "$repo:"
  resp=$(curl -s -X POST \
    -H "Authorization: Bearer $TOKEN" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/repos/$USUARIO/$repo/hooks" \
    -d "{\"config\":{\"url\":\"$HOOKURL\",\"content_type\":\"json\",\"secret\":\"$SECRET\"},\"events\":[\"push\"],\"active\":true}")
  echo "$resp" | python3 -c "
import sys, json
d = json.load(sys.stdin)
if d.get('id'):
    print('OK - webhook criado (id %s)' % d['id'])
elif 'already exists' in str(d.get('errors', '')) or 'Hook already exists' in str(d):
    print('ja existia - ok')
else:
    print('ERRO: %s' % d.get('message', d))
"
done
echo
echo "Conferir entregas: GitHub > repo > Settings > Webhooks > Recent Deliveries"
