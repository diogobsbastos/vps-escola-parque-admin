#!/bin/bash
# ============================================================
# INSTALAR EVOLUTION API — Zap Push do framework (Pacote 2/fase WhatsApp)
# Uso: bash /home/ubuntu/vps-admin/instalar_zap.sh
# Roda do FONTE (Node 22, ARM-safe, sem Docker). Banco no Postgres local.
# Ao final: https://zap.oracle-vipworks.duckdns.org/manager (login = API key)
# ============================================================
set -e

echo "== [0] DNS do subdominio =="
ping -c1 zap.oracle-vipworks.duckdns.org | head -1

echo "== [1] Banco proprio no nosso Postgres =="
EVO_DB_PASS=$(openssl rand -hex 16)
sudo -u postgres psql <<SQL
do \$\$ begin
  if not exists (select from pg_roles where rolname='evolution_user') then
    create role evolution_user login password '$EVO_DB_PASS';
  else
    alter role evolution_user with login password '$EVO_DB_PASS';
  end if;
end \$\$;
SQL
sudo -u postgres psql -Atc "select 1 from pg_database where datname='evolution'" | grep -q 1 || \
  sudo -u postgres createdb -O evolution_user evolution
echo "banco evolution OK"

echo "== [2] Codigo-fonte =="
if [ ! -d ~/evolution-api ]; then
  git clone --depth 1 https://github.com/EvolutionAPI/evolution-api.git ~/evolution-api
fi
cd ~/evolution-api

echo "== [3] Dependencias (PACIENCIA: 3-8 min no ARM) =="
npm install --no-audit --no-fund 2>&1 | tail -3

echo "== [4] Configuracao (.env) =="
EVO_API_KEY=$(openssl rand -hex 24)
cp -n .env.example .env || true
python3 - "$EVO_DB_PASS" "$EVO_API_KEY" <<'PY'
import re, sys
senha, chave = sys.argv[1], sys.argv[2]
p = "/home/ubuntu/evolution-api/.env"
txt = open(p).read()
def seta(k, v, txt):
    if re.search(rf"^{k}=", txt, re.M):
        return re.sub(rf"^{k}=.*$", f"{k}={v}", txt, flags=re.M)
    return txt + f"\n{k}={v}"
pares = {
    "SERVER_TYPE": "http",
    "SERVER_PORT": "8084",
    "SERVER_URL": "https://zap.oracle-vipworks.duckdns.org",
    "AUTHENTICATION_API_KEY": chave,
    "DATABASE_ENABLED": "true",
    "DATABASE_PROVIDER": "postgresql",
    "DATABASE_CONNECTION_URI": f"postgresql://evolution_user:{senha}@127.0.0.1:5432/evolution?schema=public",
    "DATABASE_CONNECTION_CLIENT_NAME": "evolution",
    "CACHE_REDIS_ENABLED": "false",
    "CACHE_LOCAL_ENABLED": "true",
    "DEL_INSTANCE": "false",
    "QRCODE_LIMIT": "1902",
    "LANGUAGE": "pt-BR",
}
for k, v in pares.items():
    txt = seta(k, v, txt)
open(p, "w").write(txt)
print("✅ .env configurado")
PY
chmod 600 .env
echo "$EVO_API_KEY" > ~/.evolution_api_key && chmod 600 ~/.evolution_api_key

echo "== [5] Migracoes do banco + build (PACIENCIA: 2-5 min) =="
npm run db:generate 2>&1 | tail -2
npm run db:deploy 2>&1 | tail -3
npm run build 2>&1 | tail -3

echo "== [6] Servico systemd =="
sudo tee /etc/systemd/system/evolution.service >/dev/null <<'EOF'
[Unit]
Description=Evolution API (Zap Push do framework)
After=network.target postgresql.service

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/evolution-api
ExecStart=/usr/bin/npm run start:prod
Restart=always
RestartSec=5
Environment=NODE_ENV=production

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now evolution
sleep 6 && systemctl is-active evolution

echo "== [7] Nginx + HTTPS do subdominio =="
sudo tee /etc/nginx/sites-available/zap >/dev/null <<'EOF'
server {
    listen 80;
    server_name zap.oracle-vipworks.duckdns.org;
    location / {
        proxy_pass http://127.0.0.1:8084;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_read_timeout 86400;
        client_max_body_size 50m;
    }
}
EOF
sudo ln -sf /etc/nginx/sites-available/zap /etc/nginx/sites-enabled/zap
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d zap.oracle-vipworks.duckdns.org --redirect

echo ""
echo "== [8] PROVA DE VIDA =="
curl -s https://zap.oracle-vipworks.duckdns.org/ | head -c 200; echo
echo ""
echo "================================================================"
echo "✅ EVOLUTION NO AR!"
echo "Manager:  https://zap.oracle-vipworks.duckdns.org/manager"
echo "API key:  $EVO_API_KEY"
echo "(guardada em ~/.evolution_api_key)"
echo "Proximo passo: abrir o Manager no navegador, logar com a API key,"
echo "criar a instancia 'sentinela' e escanear o QR com o CHIP RESERVA."
echo "================================================================"
