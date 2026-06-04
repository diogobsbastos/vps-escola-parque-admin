# 🪝 WEBHOOK estilo Vercel — kit de instalação (1 colada no SSH)

> `webhook.py` JÁ está no servidor (`/home/ubuntu/vps-admin/webhook.py`, escrito via MCP, testado na sandbox).
> Falta só o que exige sudo: segredos, serviço, rota Nginx. Cole o bloco abaixo INTEIRO no SSH.
> Conecte: `ssh -i "$HOME\.ssh\ssh-key-2026-06-03.key" ubuntu@137.131.156.145`

## PASSO 1 — Bloco único no servidor

```bash
set -e
# 1) Segredos (gerados AQUI, nunca em chat/repo)
test -s ~/.vps_webhook_secret || { openssl rand -hex 24 > ~/.vps_webhook_secret; chmod 600 ~/.vps_webhook_secret; }
test -s ~/.vps_webhook_rota   || { echo "hook-$(openssl rand -hex 8)" > ~/.vps_webhook_rota; chmod 600 ~/.vps_webhook_rota; }

# 2) Serviço systemd (stdlib pura: python3 do sistema, sem venv)
sudo tee /etc/systemd/system/vpswebhook.service >/dev/null <<'EOF'
[Unit]
Description=VPS Webhook (push->deploy estilo Vercel)
After=network.target

[Service]
User=ubuntu
ExecStart=/usr/bin/python3 /home/ubuntu/vps-admin/webhook.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now vpswebhook

# 3) Rota Nginx — edita sites-AVAILABLE (NUNCA sites-enabled!), insere no server 443
HOOKPATH=$(cat ~/.vps_webhook_rota)
sudo HOOKPATH="$HOOKPATH" python3 - <<'PYEOF'
import os
p = "/etc/nginx/sites-available/apps"
rota = os.environ["HOOKPATH"]
conf = open(p).read()
if f"/{rota}/" in conf:
    print("rota ja existe, nada a fazer")
else:
    out, feito = [], False
    for ln in conf.splitlines(keepends=True):
        out.append(ln)
        if not feito and "listen 443" in ln:
            out.append(f"    location /{rota}/ {{ proxy_pass http://127.0.0.1:8800/; }}\n")
            feito = True
    assert feito, "listen 443 nao encontrado em sites-available/apps!"
    open(p, "w").write("".join(out))
    print("rota inserida no server 443")
PYEOF
sudo nginx -t && sudo systemctl reload nginx

# 4) Conferências da pauta (timer 30s + produção em dia — itens 2 e 3 do handover)
systemctl list-timers vpsautodeploy.timer --no-pager
cat ~/.vps_git_state.json; echo

# 5) Teste de vida + dados p/ configurar o GitHub (aparecem SÓ no seu terminal)
sleep 1; curl -s "https://oracle-vipworks.duckdns.org/$HOOKPATH/"; echo; echo
echo "== CONFIGURAR NOS 4 REPOS (Settings > Webhooks > Add webhook) =="
echo "Payload URL : https://oracle-vipworks.duckdns.org/$HOOKPATH/"
echo "Content type: application/json"
echo "Secret      : $(cat ~/.vps_webhook_secret)"
echo "Events      : Just the push event"
```

✅ Esperado: `vpswebhook vivo` no teste de vida + timer mostrando 30s (se ainda estiver 120s, me avise).

## PASSO 2 — GitHub (4 repos)

Em cada repo (`escola-parque`, `vps-escola-parque-admin`, `sertanejo-lab`, `escola-parque-frontend`):
**Settings → Webhooks → Add webhook** → colar Payload URL / Content type / Secret do passo 1 → "Just the push event" → Add.
O GitHub manda um **ping** na hora: tem que aparecer ✅ verde em "Recent Deliveries" (resposta `pong`).

## PASSO 3 — Teste fim-a-fim

Push em qualquer repo → em ~5s o vigia dispara. Conferir: `journalctl -u vpswebhook -f` (linha `PUSH repo ... -> disparando vigia`) e `journalctl -u vpsautodeploy -n 20`.

## Como funciona

GitHub → POST `https://oracle-vipworks.duckdns.org/hook-XXXX/` (rota secreta) → Nginx → `127.0.0.1:8800` (`webhook.py`, stdlib pura) → valida **X-Hub-Signature-256** (HMAC) → `sudo -n systemctl start --no-block vpsautodeploy.service` → vigia faz pull/build/restart. Polling do timer continua como rede de segurança.

Segurança: 2 camadas (rota secreta + HMAC); assinatura inválida = 401 descartado; porta 8800 só em localhost; segredos só no servidor (chmod 600).

⚠️ Pendência de higiene Git: `webhook.py` foi escrito direto via MCP (sandbox desta sessão sem o token GitHub — pasta AUTOMACOES não montada). Na próxima sessão com AUTOMACOES montada: commitar `webhook.py` + este kit no repo `vps-escola-parque-admin` (infra/).
