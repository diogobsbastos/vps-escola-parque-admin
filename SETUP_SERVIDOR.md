# VPS Admin — Setup no servidor (referência)

> Instalação do painel em `https://oracle-vipworks.duckdns.org/admin` (porta interna 8500).

## 1. Enviar arquivos (PowerShell no PC)

```powershell
scp -i "$HOME\.ssh\ssh-key-2026-06-03.key" -r "C:\Users\DB LIVE STUDIO\Desktop\AUTOMACOES\VPS_ADMIN" ubuntu@137.131.156.145:~/vps-admin
```

## 2. Instalar no servidor (terminal SSH) — bloco único

```bash
# senha do painel (TROQUE SuaSenhaForteAqui!)
echo 'SuaSenhaForteAqui' > ~/.vps_admin_pass && chmod 600 ~/.vps_admin_pass

# venv + dependências
cd ~/vps-admin && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# sudoers: permite ao painel SOMENTE systemctl restart/stop/start (sem senha)
sudo tee /etc/sudoers.d/vpsadmin > /dev/null <<'EOF'
ubuntu ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart *, /usr/bin/systemctl stop *, /usr/bin/systemctl start *
EOF
sudo chmod 440 /etc/sudoers.d/vpsadmin

# leitura de logs sem sudo
sudo usermod -aG systemd-journal ubuntu

# serviço systemd
sudo tee /etc/systemd/system/vpsadmin.service > /dev/null <<'EOF'
[Unit]
Description=VPS Admin (painel Streamlit)
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/vps-admin
ExecStart=/home/ubuntu/vps-admin/.venv/bin/streamlit run app.py --server.port 8500 --server.address 127.0.0.1 --server.headless true --server.baseUrlPath admin
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now vpsadmin

# rota no Nginx (insere o bloco /admin antes do fechamento do server)
sudo sed -i '/^}$/i\
    location /admin/ {\
        proxy_pass http://127.0.0.1:8500/admin/;\
        proxy_http_version 1.1;\
        proxy_set_header Upgrade $http_upgrade;\
        proxy_set_header Connection "upgrade";\
        proxy_set_header Host $host;\
        proxy_read_timeout 86400;\
    }' /etc/nginx/sites-available/apps
sudo nginx -t && sudo systemctl reload nginx
```

## 3. Acessar

`https://oracle-vipworks.duckdns.org/admin/` → senha do passo 2. (A raiz `/` do domínio redireciona pro painel.)

## Notas

- O painel roda em `127.0.0.1:8500` (NÃO exposto direto — só via Nginx).
- Ações limitadas a restart/stop/start de serviços. Sem terminal livre.
- Para registrar um app novo no painel: editar o dict `SERVICOS` no `app.py`.
- Atualizar o painel: scp do `app.py` + `sudo systemctl restart vpsadmin`.
