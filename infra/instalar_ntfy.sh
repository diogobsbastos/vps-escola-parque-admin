#!/bin/bash
# ============================================================
# INSTALAR NTFY — push de marca propria (Pacote 5)
# Uso: bash /home/ubuntu/vps-admin/instalar_ntfy.sh
# (o binario ja foi instalado; este script faz config -> servico
#  -> usuario -> nginx -> https -> teste)
# ============================================================
set -e

echo "== [0] DNS =="
ping -c1 ntfy.oracle-vipworks.duckdns.org | head -1 || { echo "DNS nao resolve — plano B (avisar o Claude)"; exit 1; }

echo "== [2] Config =="
sudo mkdir -p /etc/ntfy /var/lib/ntfy && sudo chown ubuntu /var/lib/ntfy
sudo tee /etc/ntfy/server.yml >/dev/null <<'EOF'
base-url: "https://ntfy.oracle-vipworks.duckdns.org"
listen-http: "127.0.0.1:2586"
behind-proxy: true
auth-file: "/var/lib/ntfy/user.db"
auth-default-access: "deny-all"
upstream-base-url: "https://ntfy.sh"
cache-file: "/var/lib/ntfy/cache.db"
attachment-cache-dir: "/var/lib/ntfy/attachments"
EOF

echo "== [3] Servico =="
sudo tee /etc/systemd/system/ntfy.service >/dev/null <<'EOF'
[Unit]
Description=ntfy (push de marca propria)
After=network.target

[Service]
User=ubuntu
ExecStart=/usr/local/bin/ntfy serve
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now ntfy
sleep 1 && systemctl is-active ntfy

echo "== [4] Usuario 'diogo' (DIGITE UMA SENHA FORTE QUANDO PEDIR) =="
ntfy user add diogo || echo "(usuario ja existia — ok)"
ntfy access diogo "vps-*" rw
ntfy access diogo "escola-*" rw

echo "== [5] Nginx + HTTPS =="
sudo tee /etc/nginx/sites-available/ntfy >/dev/null <<'EOF'
server {
    listen 80;
    server_name ntfy.oracle-vipworks.duckdns.org;
    location / {
        proxy_pass http://127.0.0.1:2586;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
        client_max_body_size 20m;
    }
}
EOF
sudo ln -sf /etc/nginx/sites-available/ntfy /etc/nginx/sites-enabled/ntfy
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d ntfy.oracle-vipworks.duckdns.org --redirect

echo ""
echo "== [6] TESTE — digite a senha que voce criou no passo 4 =="
read -s -p "Senha do usuario diogo: " NTFY_PASS; echo
curl -s -u "diogo:$NTFY_PASS" -d "🎉 Primeiro push do MEU servidor de notificações!" https://ntfy.oracle-vipworks.duckdns.org/vps-alertas && echo "" && echo "✅ push enviado!"
echo ""
echo "== PRONTO! No iPhone (app ntfy): Use another server ->"
echo "   servidor: https://ntfy.oracle-vipworks.duckdns.org"
echo "   topico:   vps-alertas   · usuario: diogo + sua senha"
