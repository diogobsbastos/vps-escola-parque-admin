# VPS-MCP — Setup no servidor (nível 1: Operador)

> Dá ao Claude acesso direto e seguro ao servidor via MCP. Rota com SEGREDO na URL.

## 1. Enviar (PowerShell no PC)

```powershell
scp -i "$HOME\.ssh\ssh-key-2026-06-03.key" -r "C:\Users\DB LIVE STUDIO\Desktop\AUTOMACOES\VPS_ADMIN\vps_mcp" ubuntu@137.131.156.145:~/vps-mcp
```

## 2. Instalar (terminal SSH) — bloco único

```bash
cd ~/vps-mcp && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# serviço systemd (porta 8700, só localhost)
sudo tee /etc/systemd/system/vpsmcp.service > /dev/null <<'EOF'
[Unit]
Description=VPS-MCP (servidor MCP nivel 1)
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/vps-mcp
ExecStart=/home/ubuntu/vps-mcp/.venv/bin/python server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now vpsmcp
```

## 3. Rota no Nginx com o TOKEN na URL

O token fica em `~/.vps_mcp_token` (gerado pelo painel → aba 🔌 Acesso MCP).
Pegue o token e crie a rota:

```bash
TOK=$(cat ~/.vps_mcp_token)
sudo sed -i "/^}$/i\\
    location /mcp-$TOK/ {\\
        proxy_pass http://127.0.0.1:8700/;\\
        proxy_http_version 1.1;\\
        proxy_set_header Host 127.0.0.1:8700;\\
        proxy_set_header Origin '';\\
        proxy_set_header Connection '';\\
        proxy_buffering off;\\
        proxy_read_timeout 86400;\\
    }" /etc/nginx/sites-available/apps
sudo nginx -t && sudo systemctl reload nginx
```

> ⚠️ **Lição (04/06/2026):** o `Host` repassado tem que ser `127.0.0.1:8700`, NÃO `$host`.
> O servidor MCP tem proteção anti-DNS-rebinding e devolve **421 Misdirected Request**
> se o Host não for o dele. O segredo do perímetro já é o token na URL.

> ⚠️ Ao **renovar o token** no painel, refaça a rota do Nginx com o token novo
> (remova a linha antiga do `location /mcp-...` e rode o bloco acima de novo).

## 4. Conectar no Claude

App do Claude → Configurações → Conectores → Adicionar conector personalizado →
cole `https://oracle-vipworks.duckdns.org/mcp-SEUTOKEN/mcp`.
⚠️ O Claude EXIGE `https://` — usar sempre o domínio, nunca o IP puro.

## Segurança (nível 1)
- Arquivos: só dentro de `/home/ubuntu/{escola-parque,vps-admin,llm-gateway,sertanejo-lab}`.
- Serviços: só restart/stop/start/status/logs da whitelist.
- git: só status/pull/log/diff/fetch. Sem shell livre. Sem root.
