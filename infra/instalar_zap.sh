#!/bin/bash
# ============================================================
# INSTALAR EVOLUTION API — Zap Push do framework
# Roda do FONTE (Node 22, ARM-safe, sem Docker). Banco no Postgres local.
# ============================================================
set -e
echo "== [1] Banco =="
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

echo "== [2] Codigo =="
[ -d ~/evolution-api ] || git clone --depth 1 https://github.com/EvolutionAPI/evolution-api.git ~/evolution-api
cd ~/evolution-api
echo "== [3] Deps =="; npm install --no-audit --no-fund 2>&1 | tail -3
echo "== [4] .env =="
EVO_API_KEY=$(openssl rand -hex 24)
cp -n .env.example .env || true
python3 - "$EVO_DB_PASS" "$EVO_API_KEY" <<'PY'
import re, sys
senha, chave = sys.argv[1], sys.argv[2]
p = "/home/ubuntu/evolution-api/.env"; txt = open(p).read()
def seta(k,v,t): return re.sub(rf"^{k}=.*$",f"{k}={v}",t,flags=re.M) if re.search(rf"^{k}=",t,re.M) else t+f"\n{k}={v}"
pares = {"SERVER_TYPE":"http","SERVER_PORT":"8084",
 "SERVER_URL":"https://zap.oracle-vipworks.duckdns.org","AUTHENTICATION_API_KEY":chave,
 "DATABASE_ENABLED":"true","DATABASE_PROVIDER":"postgresql",
 "DATABASE_CONNECTION_URI":f"postgresql://evolution_user:{senha}@127.0.0.1:5432/evolution?schema=public",
 "DATABASE_CONNECTION_CLIENT_NAME":"evolution","CACHE_REDIS_ENABLED":"false",
 "CACHE_LOCAL_ENABLED":"true","DEL_INSTANCE":"false","QRCODE_LIMIT":"1902","LANGUAGE":"pt-BR",
 "DATABASE_SAVE_DATA_NEW_MESSAGE":"false","DATABASE_SAVE_MESSAGE_UPDATE":"false",
 "DATABASE_SAVE_DATA_CONTACTS":"false","DATABASE_SAVE_DATA_CHATS":"false",
 "DATABASE_SAVE_DATA_HISTORIC":"false","GROUPS_IGNORE":"true"}
for k,v in pares.items(): txt = seta(k,v,txt)
open(p,"w").write(txt); print("env ok")
PY
chmod 600 .env; echo "$EVO_API_KEY" > ~/.evolution_api_key && chmod 600 ~/.evolution_api_key
echo "== [5] build =="; npm run db:generate 2>&1|tail -2; npm run db:deploy 2>&1|tail -3; npm run build 2>&1|tail -3
echo "== [6] servico =="
sudo tee /etc/systemd/system/evolution.service >/dev/null <<'EOF'
[Unit]
Description=Evolution API (Zap Push)
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
sudo systemctl daemon-reload && sudo systemctl enable --now evolution && sleep 6 && systemctl is-active evolution
echo "== [7] nginx+https =="
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
echo "EVOLUTION NO AR! Manager: https://zap.oracle-vipworks.duckdns.org/manager  ·  key em ~/.evolution_api_key"
