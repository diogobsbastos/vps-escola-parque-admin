# Infra do framework VPS Admin — instalacao em servidor NOVO

> Objetivo: replicar este painel/ponte em qualquer VPS Ubuntu em ~30 min.
> Pre-requisitos: dominio (DuckDNS serve) + token GitHub em ~/.github_token.

## 1. Identidade (fonte unica)
```bash
echo '{"ip": "SEU_IP", "dominio": "seu.duckdns.org", "github_user": "diogobsbastos"}' > ~/.vps_config.json
```

## 2. Git
```bash
chmod 600 ~/.github_token
git config --global user.name "SEU NOME" && git config --global user.email "seu@email"
git config --global credential.helper store
echo "https://x-access-token:$(cat ~/.github_token)@github.com" > ~/.git-credentials && chmod 600 ~/.git-credentials
```

## 3. Painel (este repo)
Seguir SETUP_SERVIDOR.md (venv, senha, sudoers do systemctl, servico vpsadmin, rota nginx).

## 4. HTTPS
DuckDNS apontando pro IP + `sudo certbot --nginx -d SEU_DOMINIO --redirect`.
⚠️ NUNCA usar `sed -i` em /etc/nginx/sites-enabled (mata o symlink) — editar sites-available.

## 5. Auto-deploy (vigia)
```bash
sudo cp infra/vpsautodeploy.service infra/vpsautodeploy.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now vpsautodeploy.timer
```

## 6. Pagina inicial configuravel
```bash
sudo cp infra/vps_rota_raiz.sh /usr/local/bin/ && sudo chmod 755 /usr/local/bin/vps_rota_raiz.sh
echo 'ubuntu ALL=(ALL) NOPASSWD: /usr/local/bin/vps_rota_raiz.sh' | sudo tee /etc/sudoers.d/vpsadmin-rota
sudo chmod 440 /etc/sudoers.d/vpsadmin-rota
```

## 7. MCP (ponte do Claude) e LLM Gateway
Seguir vps_mcp/SETUP.md (⚠️ rota nginx exige Host 127.0.0.1:8700 e Origin '')
e llm_gateway/SETUP.md.

## 8. Apps
Cada app: clone do GitHub + venv + systemd + rota nginx (kit no painel ➕ Novo App)
e conectar na pagina 🌿 Git & Deploys (➕ Conectar repo).
