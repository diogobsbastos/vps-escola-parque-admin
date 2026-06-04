# LLM Gateway — Setup no servidor

> Expõe o Ollama como API com chave: `https://oracle-vipworks.duckdns.org/llm/v1`

## 1. Enviar (PowerShell no PC)

```powershell
scp -i "$HOME\.ssh\ssh-key-2026-06-03.key" -r "C:\Users\DB LIVE STUDIO\Desktop\AUTOMACOES\VPS_ADMIN\llm_gateway" ubuntu@137.131.156.145:~/llm-gateway
```

## 2. Instalar (terminal SSH) — bloco único

```bash
cd ~/llm-gateway && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# serviço systemd (porta 8600, só localhost)
sudo tee /etc/systemd/system/llmgateway.service > /dev/null <<'EOF'
[Unit]
Description=LLM Gateway (API key na frente do Ollama)
After=network.target ollama.service

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/llm-gateway
ExecStart=/home/ubuntu/llm-gateway/.venv/bin/uvicorn gateway:app --host 127.0.0.1 --port 8600
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now llmgateway

# rota /llm/ no Nginx (sem cache, streaming on)
sudo sed -i '/^}$/i\
    location /llm/ {\
        proxy_pass http://127.0.0.1:8600/;\
        proxy_http_version 1.1;\
        proxy_set_header Host $host;\
        proxy_buffering off;\
        proxy_read_timeout 86400;\
    }' /etc/nginx/sites-available/apps
sudo nginx -t && sudo systemctl reload nginx
```

## 3. Registrar o gateway no painel (opcional — pra aparecer em Aplicativos)

Edite `~/vps-admin/app.py` → dict `SERVICOS_BASE` → adicione:
`"llmgateway": "🔑 LLM Gateway (API)",`  → `sudo systemctl restart vpsadmin`

## 4. Usar

1. Painel `/admin/` → aba **🔑 API da LLM** → **Criar nova chave** → copie a `sk-vps-...`.
2. No cliente: base_url `https://oracle-vipworks.duckdns.org/llm/v1` + a chave.
   (O endereço antigo `http://IP/llm/v1` redireciona, mas clientes de API podem falhar no
   redirect de POST — **use sempre o https com domínio**.)

## Notas de segurança
- Ollama continua **fechado** (localhost). Só o gateway o alcança.
- Sem chave válida → 401. Revogar uma chave bloqueia na hora (gateway lê o arquivo a cada request).
- HTTPS: quando configurarmos domínio + Let's Encrypt, vira `https://...` automaticamente.
