# 🚚 Migrar o framework — outro VPS e/ou outro GitHub

> Receita completa. No painel: 🌿 Git & Deploys → 🪝 Campainha → 📦 Roteiro.

## 0. GitHub novo (só se mudar de conta)
1. Criar a conta/organização.
2. Subir os repos do framework (`git push` a partir dos clones atuais).
3. Gerar token fine-grained (Settings → Developer settings → Fine-grained tokens):
   repos do framework, permissões **Contents R/W + Webhooks R/W**.
4. No servidor novo: `echo SEU_TOKEN > ~/.github_token && chmod 600 ~/.github_token`

## 1. Painel no VPS novo
Seguir `SETUP_SERVIDOR.md` (clone + venv + systemd + Nginx + certbot).
`webhook.py` e `criar_webhooks.sh` já vêm com o painel via git.

## 2. Identidade
`~/.vps_config.json`: `{"ip": "...", "dominio": "...", "github_user": "CONTA"}`

## 3. Campainha
Colar o kit `INSTALL_WEBHOOK.md` (ou o bloco do painel) no SSH — 1x.

## 4. Religar webhooks
Painel → 🌿 → 🪝 → **🔁 Conectar/atualizar TODOS** (cria via API na conta nova).

## 5. Conferir
Push de teste em qualquer repo → faixa da página 🌿 mostra o PUSH → 📜 histórico registra.
