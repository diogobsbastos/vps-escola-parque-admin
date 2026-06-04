"""
VPS ADMIN v2.0 — Central de gestao do servidor (estilo "mini Locaweb")
======================================================================
Menu lateral multipaginas. Roda em http://IP/admin (porta interna 8500).

Seguranca (por design):
- Senha obrigatoria (~/.vps_admin_pass, chmod 600)
- SEM terminal livre: acoes limitadas a whitelist de servicos/acoes
- Restart/Stop via sudoers NOPASSWD especifico (/etc/sudoers.d/vpsadmin)
- Apps novos registrados em ~/.vps_admin_apps.json (sem editar codigo)

Este painel e a BASE replicavel para outras VPS (outros clientes):
basta clonar a pasta + rodar o SETUP_SERVIDOR.md em cada servidor novo.

Autor: Diogo + Claude (Mentor) — 06/2026
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import requests
import streamlit as st

try:
    import psutil
except ImportError:
    psutil = None

# ============================================================
# Config
# ============================================================

st.set_page_config(page_title="VPS Admin", page_icon="🛠️", layout="wide")

SENHA_PATH = Path.home() / ".vps_admin_pass"
USER_PATH = Path.home() / ".vps_admin_user.json"
APPS_PATH = Path.home() / ".vps_admin_apps.json"
OLLAMA_URL = "http://localhost:11434"
NGINX_CONF = Path("/etc/nginx/sites-available/apps")
# -----------------------------------------------------------------
# FONTE ÚNICA DE VERDADE (estilo WordPress "Site URL"):
# ~/.vps_config.json define ip/dominio UMA vez e o painel inteiro deriva.
# Em servidor novo: criar esse arquivo e NADA aqui precisa ser editado.
#   {"ip": "1.2.3.4", "dominio": "meuserver.duckdns.org"}
# -----------------------------------------------------------------
CONFIG_PATH = Path.home() / ".vps_config.json"
try:
    _cfg = json.loads(CONFIG_PATH.read_text())
except Exception:
    _cfg = {}
IP_PUBLICO = _cfg.get("ip", "137.131.156.145")
DOMINIO = _cfg.get("dominio", "oracle-vipworks.duckdns.org")  # DuckDNS (Google: diogobsbastos@gmail.com)
URL_BASE = f"https://{DOMINIO}"                  # HTTPS via Let's Encrypt (renovação automática)

USUARIO_PADRAO = {"nome": "Diogo Brandão", "email": "diogobsbastos@gmail.com"}

# Servicos base (sempre presentes). Apps extras vem do APPS_PATH (json).
SERVICOS_BASE: dict[str, str] = {
    "escolaparque":        "🏫 Escola Parque V3 (app)",
    "escolaparque-worker": "🧠 Backend Central (worker)",
    "vpsadmin":            "🛠️ VPS Admin (este painel)",
    "nginx":               "🚪 Nginx (porteiro/rotas)",
    "ollama":              "🦙 Ollama (LLM local)",
    "llmgateway":          "🔑 LLM Gateway (API com chave)",
    "vpsmcp":              "🔌 VPS-MCP (ponte do Claude)",
    "sertanejolab":        "🎸 Sertanejo Lab (app)",
    "innovafront":         "🚀 Innova Front (Next.js)",
}

ACOES = ("restart", "stop", "start")

# Apps com interface web acessível (serviço -> rota). Infra (nginx/ollama/worker) fica de fora.
ROTAS_APPS: dict[str, str] = {
    "escolaparque": "/escola-parque/",
    "vpsadmin":     "/admin/",
    "sertanejolab": "/sertanejo/",
}


# ============================================================
# Helpers — sistema
# ============================================================

def _run(cmd: list[str], timeout: int = 25) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return 1, f"erro: {e}"


def carregar_apps_extras() -> dict[str, str]:
    try:
        return json.loads(APPS_PATH.read_text())
    except Exception:
        return {}


def salvar_apps_extras(apps: dict[str, str]) -> bool:
    try:
        APPS_PATH.write_text(json.dumps(apps, ensure_ascii=False, indent=2))
        return True
    except Exception:
        return False


def todos_servicos() -> dict[str, str]:
    return {**SERVICOS_BASE, **carregar_apps_extras()}


def status_servico(nome: str) -> str:
    _, out = _run(["systemctl", "is-active", nome])
    return out.splitlines()[0] if out else "?"


def acao_servico(nome: str, acao: str) -> tuple[bool, str]:
    if nome not in todos_servicos() or acao not in ACOES:
        return False, "acao ou servico fora da whitelist"
    rc, out = _run(["sudo", "-n", "/usr/bin/systemctl", acao, nome], timeout=60)
    return rc == 0, out or f"{acao} {nome}: ok"


def logs_servico(nome: str, linhas: int = 80) -> str:
    if nome not in todos_servicos():
        return "servico fora da whitelist"
    _, out = _run(["journalctl", "-u", nome, "-n", str(linhas), "--no-pager", "-o", "short-iso"])
    return out or "(sem logs)"


def ollama_modelos() -> list[dict]:
    try:
        r = requests.get(f"{OLLAMA_URL}/api/tags", timeout=4)
        return r.json().get("models", [])
    except Exception:
        return []


def config_salvar(chave: str, valor) -> None:
    """Grava uma opção na fonte única de verdade (~/.vps_config.json)."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except Exception:
        cfg = {}
    cfg[chave] = valor
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    except Exception:
        pass


def ollama_manter_na_ram(modelo: str, ligar: bool) -> bool:
    """ligar=True: carrega o modelo JÁ e fixa por 24h. ligar=False: descarrega JÁ."""
    try:
        r = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": modelo, "keep_alive": "24h" if ligar else 0},
            timeout=300,
        )
        return r.ok
    except Exception:
        return False


def ollama_show(nome: str) -> dict:
    """Ficha tecnica completa do modelo (API /api/show do Ollama)."""
    try:
        r = requests.post(f"{OLLAMA_URL}/api/show", json={"name": nome}, timeout=10)
        return r.json()
    except Exception:
        return {}


def ollama_tamanho_remoto(modelo_tag: str) -> str:
    """Tamanho REAL do download, consultado no registro oficial (sem baixar nada)."""
    try:
        nome, _, tag = modelo_tag.partition(":")
        tag = tag or "latest"
        r = requests.get(
            f"https://registry.ollama.ai/v2/library/{nome}/manifests/{tag}",
            headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"},
            timeout=10,
        )
        m = r.json()
        total = sum(c.get("size", 0) for c in m.get("layers", []))
        total += (m.get("config", {}) or {}).get("size", 0)
        return f"{total/1e9:.1f} GB" if total > 0 else "?"
    except Exception:
        return "?"


CATALOGO_PATH = Path.home() / ".vps_admin_ollama_catalog.json"

# Fallback se nunca atualizou a lista (populares, com tamanho aproximado)
CATALOGO_POPULAR = [
    {"nome": "qwen2.5:7b", "tamanho": "4.7 GB"},
    {"nome": "qwen2.5:14b", "tamanho": "9.0 GB"},
    {"nome": "qwen2.5vl:7b", "tamanho": "6.0 GB"},
    {"nome": "llama3.2:3b", "tamanho": "2.0 GB"},
    {"nome": "llama3.1:8b", "tamanho": "4.9 GB"},
    {"nome": "gemma3:4b", "tamanho": "3.3 GB"},
    {"nome": "mistral:7b", "tamanho": "4.1 GB"},
    {"nome": "phi4:14b", "tamanho": "9.1 GB"},
    {"nome": "deepseek-r1:7b", "tamanho": "4.7 GB"},
    {"nome": "llava:7b", "tamanho": "4.7 GB"},
    {"nome": "nomic-embed-text:latest", "tamanho": "0.3 GB"},
]


def catalogo_ollama() -> list[dict]:
    try:
        itens = json.loads(CATALOGO_PATH.read_text())
        if itens and isinstance(itens[0], str):
            # cache no formato antigo (so nomes) -> converte; tamanhos vem no proximo 🔄
            itens = [{"nome": f"{n}:latest", "tamanho": "?"} for n in itens]
        if itens and isinstance(itens[0], dict) and "nome" in itens[0]:
            return itens
        return CATALOGO_POPULAR
    except Exception:
        return CATALOGO_POPULAR


def atualizar_catalogo_ollama(barra=None) -> tuple[bool, int]:
    """Busca rapido a lista de modelos da biblioteca oficial (1 request).
    Tamanho exato nao e exposto de forma confiavel pelo site -> mostramos '—'
    no catalogo (os POPULARES tem tamanho curado; instalados tem tamanho real)."""
    import re as _re
    try:
        r = requests.get("https://ollama.com/library", timeout=20)
        nomes = sorted(set(_re.findall(r'href="/library/([a-z0-9\-\.]+)"', r.text)))
        if not nomes:
            return False, 0
        curados = {it["nome"].split(":")[0]: it for it in CATALOGO_POPULAR}
        itens: list[dict] = list(CATALOGO_POPULAR)  # populares com tamanho real no topo
        for n in nomes:
            if n not in curados:
                itens.append({"nome": f"{n}:latest", "tamanho": "—"})
        CATALOGO_PATH.write_text(json.dumps(itens))
        return True, len(itens)
    except Exception:
        return False, 0


def rotas_nginx() -> list[str]:
    try:
        texto = NGINX_CONF.read_text()
    except Exception:
        return []
    rotas = []
    for linha in texto.splitlines():
        s = linha.strip()
        if s.startswith("location") and "{" in s:
            rotas.append(s.split("{")[0].replace("location", "").strip())
    return rotas


def dominios_nginx() -> list[dict]:
    """Varre /etc/nginx/sites-enabled e extrai: dominio, destino e se tem SSL."""
    import glob as _g
    achados: dict[str, dict] = {}
    for f in sorted(_g.glob("/etc/nginx/sites-enabled/*")):
        try:
            txt = Path(f).read_text()
        except Exception:
            continue
        for bloco in txt.split("server {")[1:]:
            nome, alvo, ssl = "", "", False
            for ln in bloco.splitlines():
                ls = ln.strip()
                if ls.startswith("server_name") and not nome:
                    nome = ls.replace("server_name", "").strip(" ;")
                if "listen 443" in ls:
                    ssl = True
                if "proxy_pass" in ls and not alvo:
                    alvo = ls.split("proxy_pass")[1].strip(" ;")
            if not nome or nome == "_":
                continue
            atual = achados.get(nome, {"dominio": nome, "alvo": "", "ssl": False,
                                       "arquivo": Path(f).name})
            atual["ssl"] = atual["ssl"] or ssl
            if alvo and not atual["alvo"]:
                atual["alvo"] = alvo
            achados[nome] = atual
    return list(achados.values())


PORTAS_SERVICOS = {  # porta interna -> servico (traduz destinos do nginx p/ nome de app)
    "8500": "vpsadmin", "8501": "escolaparque", "8502": "sertanejolab",
    "3000": "innovafront", "8600": "llmgateway", "8700": "vpsmcp", "11434": "ollama",
}


def alvo_amigavel(alvo: str) -> str:
    """Converte 'http://127.0.0.1:3000' em '🚀 Innova Front (porta 3000)'."""
    import re as _re2
    m = _re2.search(r":(\d+)", alvo or "")
    svc = PORTAS_SERVICOS.get(m.group(1)) if m else None
    if svc:
        return f"{todos_servicos().get(svc, svc)} · porta {m.group(1)}"
    return alvo or "(rotas internas abaixo)"


@st.cache_data(ttl=600, show_spinner=False)
def cert_validade_cache(dominio: str) -> str | None:
    return cert_validade(dominio)


@st.cache_data(ttl=300, show_spinner=False)
def listar_bibliotecas() -> dict[str, list[dict]]:
    """Bibliotecas instaladas em CADA app (varre os venvs de /home/ubuntu/*/.venv).
    Cache de 5 min — apps novos aparecem sozinhos."""
    import glob
    res: dict[str, list[dict]] = {}
    for venv in sorted(glob.glob("/home/ubuntu/*/.venv")):
        app_nome = Path(venv).parent.name
        rc, out = _run([f"{venv}/bin/pip", "list", "--format=json",
                        "--disable-pip-version-check"], timeout=90)
        try:
            res[app_nome] = json.loads(out) if rc == 0 else []
        except Exception:
            res[app_nome] = []
    return res


# ============================================================
# Helpers — usuario
# ============================================================

def carregar_usuario() -> dict:
    try:
        return {**USUARIO_PADRAO, **json.loads(USER_PATH.read_text())}
    except Exception:
        return dict(USUARIO_PADRAO)


def salvar_usuario(dados: dict) -> bool:
    try:
        USER_PATH.write_text(json.dumps(dados, ensure_ascii=False, indent=2))
        USER_PATH.chmod(0o600)
        return True
    except Exception:
        return False


def _mascarar_email(email: str) -> str:
    try:
        u, dom = email.split("@", 1)
        return f"{u[:3]}{'*' * max(len(u) - 3, 2)}@{dom}"
    except Exception:
        return "***"


API_KEYS_PATH = Path.home() / ".vps_admin_api_keys.json"
API_USAGE_PATH = Path.home() / ".vps_admin_api_usage.json"


def carregar_api_keys() -> list[dict]:
    try:
        return json.loads(API_KEYS_PATH.read_text()).get("keys", [])
    except Exception:
        return []


def salvar_api_keys(keys: list[dict]) -> bool:
    try:
        API_KEYS_PATH.write_text(json.dumps({"keys": keys}, ensure_ascii=False, indent=2))
        API_KEYS_PATH.chmod(0o600)
        return True
    except Exception:
        return False


def carregar_uso_api() -> dict:
    try:
        return json.loads(API_USAGE_PATH.read_text())
    except Exception:
        return {}


def gerar_api_key() -> str:
    import secrets
    return "sk-vps-" + secrets.token_hex(24)


def gateway_online() -> bool:
    try:
        return requests.get("http://localhost:8600/health", timeout=3).ok
    except Exception:
        return False


MCP_TOKEN_PATH = Path.home() / ".vps_mcp_token"


def mcp_token_atual() -> str:
    try:
        return MCP_TOKEN_PATH.read_text().strip()
    except Exception:
        return ""


def mcp_gerar_token() -> str:
    import secrets
    tok = secrets.token_urlsafe(32)
    try:
        MCP_TOKEN_PATH.write_text(tok)
        MCP_TOKEN_PATH.chmod(0o600)
        return tok
    except Exception:
        return ""


def cert_validade(dominio: str) -> str | None:
    """Lê a validade do certificado HTTPS direto do handshake TLS."""
    import socket
    import ssl
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.create_connection((dominio, 443), 5),
                             server_hostname=dominio) as s:
            exp = s.getpeercert().get("notAfter")  # ex: 'Sep  2 03:14:00 2026 GMT'
            return exp
    except Exception:
        return None


def mcp_ping_fluxo() -> list[tuple[bool, str]]:
    """Rastreia o fluxo do MCP etapa por etapa (como um log): serviço → porta → rota → mundo."""
    passos: list[tuple[bool, str]] = []
    _, ativo = _run(["systemctl", "is-active", "vpsmcp"])
    ativo = (ativo or "").strip()
    passos.append((ativo == "active", f"Serviço `vpsmcp` (systemd) → `{ativo or '?'}`"))
    try:
        code = requests.get("http://localhost:8700/mcp", timeout=4).status_code
    except Exception:
        code = None
    passos.append((code == 406,
                   f"Servidor MCP local `127.0.0.1:8700/mcp` → HTTP {code} *(406 = vivo)*"))
    token = mcp_token_atual()
    try:
        rota_ok = bool(token) and f"mcp-{token}" in NGINX_CONF.read_text()
    except Exception:
        rota_ok = False
    passos.append((rota_ok, "Rota no Nginx `/mcp-<token>/` → "
                            + ("encontrada" if rota_ok else "NÃO encontrada")))
    ext = None
    if token:
        try:
            ext = requests.post(
                f"{URL_BASE}/mcp-{token}/mcp",
                headers={"Content-Type": "application/json",
                         "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "method": "initialize",
                      "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                 "clientInfo": {"name": "painel-ping", "version": "1"}},
                      "id": 1},
                timeout=10,
            ).status_code
        except Exception:
            ext = None
    passos.append((ext == 200,
                   f"Ponta a ponta `https://{DOMINIO}/mcp-…/mcp` (POST initialize, "
                   f"igual ao Claude) → HTTP {ext} *(200 = mundo conectado)*"))
    return passos


def ping_api_key(key: str, modelo: str | None) -> tuple[bool, str]:
    """Teste de fogo da chave: passa pelo gateway (valida a key) e faz a LLM responder."""
    try:
        r = requests.post(
            "http://localhost:8600/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": modelo or "qwen2.5:14b",
                "messages": [{"role": "user", "content": "Responda apenas: pong"}],
                "max_tokens": 10,
            },
            timeout=90,
        )
        if r.status_code == 200:
            txt = r.json()["choices"][0]["message"]["content"].strip()
            return True, txt[:60]
        return False, f"HTTP {r.status_code}: {r.text[:120]}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:120]


def mcp_online() -> bool:
    try:
        # FastMCP responde no /mcp; qualquer status < 500 = de pé
        return requests.get("http://localhost:8700/mcp", timeout=3).status_code < 500
    except Exception:
        return False


# ============================================================
# Helpers — Git & Deploys (ponte GitHub -> producao)
# ============================================================

GIT_USER = _cfg.get("github_user", "diogobsbastos")
GIT_STATE_PATH = Path.home() / ".vps_git_state.json"

# Mapa de cada projeto: o que vem do repo -> onde vive em producao.
GIT_PROJETOS: dict[str, dict] = {
    "vps-escola-parque-admin": {
        "rotulo": "🛠️ VPS Admin (painel + LLM Gateway + MCP)",
        "mapa": {
            "app.py": "/home/ubuntu/vps-admin/app.py",
            "requirements.txt": "/home/ubuntu/vps-admin/requirements.txt",
            "autodeploy.py": "/home/ubuntu/vps-admin/autodeploy.py",
            "llm_gateway/": "/home/ubuntu/llm-gateway/",
            "vps_mcp/": "/home/ubuntu/vps-mcp/",
        },
        # vpsadmin por ULTIMO: reinicia o proprio painel
        "servicos": ["llmgateway", "vpsmcp", "vpsadmin"],
    },
    "escola-parque": {
        "rotulo": "🏫 Escola Parque V3 (app + worker)",
        "pull": "/home/ubuntu/escola-parque",   # pasta E um clone -> git pull direto
        "servicos": ["escolaparque", "escolaparque-worker"],
    },
}


GIT_PROJ_PATH = Path.home() / ".vps_git_projetos.json"


def git_projetos_extras() -> dict:
    try:
        return json.loads(GIT_PROJ_PATH.read_text())
    except Exception:
        return {}


def salvar_git_projetos(extras: dict) -> bool:
    try:
        GIT_PROJ_PATH.write_text(json.dumps(extras, ensure_ascii=False, indent=2))
        return True
    except Exception:
        return False


def todos_git_projetos() -> dict:
    return {**GIT_PROJETOS, **git_projetos_extras()}


def git_situ_curta(repo: str, conf: dict) -> str:
    """Resumo 🟢/🟠 do sync GitHub x producao (p/ Dashboard e Aplicativos)."""
    remoto = git_remote_head(repo)
    local = git_estado().get(repo, {}).get("commit", "—")
    if conf.get("pull"):
        _, _h = _run(["git", "-C", conf["pull"], "rev-parse", "--short=10", "HEAD"])
        if _h and "fatal" not in _h.lower():
            local = _h.strip()
    if remoto == "?":
        return "🟡 GitHub?"
    if local == "—":
        return "⚪ sem deploy"
    if remoto == local:
        return "🟢 em dia"
    return "🟠 update disponível!"


def autodeploy_proximo() -> int | None:
    """Segundos ate a proxima ronda do vigia. -1 = vigia TRABALHANDO agora.
    None = timer nao instalado. (Timer monotonico -> ler via list-timers.)"""
    _, ativo = _run(["systemctl", "is-active", "vpsautodeploy.service"], timeout=5)
    if (ativo or "").strip() == "active":
        return -1
    rc, out = _run(["systemctl", "list-timers", "vpsautodeploy.timer",
                    "--no-pager", "--no-legend"], timeout=5)
    out = (out or "").strip()
    if rc != 0 or not out:
        return None
    from datetime import datetime
    try:
        partes = out.split()  # ['Thu','2026-06-04','04:04:48','-03','1min','17s','left',...]
        alvo = datetime.strptime(partes[1] + " " + partes[2], "%Y-%m-%d %H:%M:%S")
        return max(0, int((alvo - datetime.now()).total_seconds()))
    except Exception:
        return None


@st.cache_data(ttl=60, show_spinner=False)
def git_remote_head(repo: str) -> str:
    """Ultimo commit no GitHub (sem clonar). Cache 60s."""
    rc, out = _run(["env", "GIT_TERMINAL_PROMPT=0", "git", "ls-remote",
                    f"https://github.com/{GIT_USER}/{repo}.git", "HEAD"], timeout=20)
    return out.split()[0][:10] if rc == 0 and out and "fatal" not in out else "?"


def git_estado() -> dict:
    try:
        return json.loads(GIT_STATE_PATH.read_text())
    except Exception:
        return {}


GIT_HIST_PATH = Path.home() / ".vps_git_historico.json"


def git_hist_add(repo: str, commit: str, origem: str) -> None:
    """Registra um deploy no histórico (mantém os 100 últimos)."""
    try:
        hist = json.loads(GIT_HIST_PATH.read_text()) if GIT_HIST_PATH.exists() else []
    except Exception:
        hist = []
    hist.append({"repo": repo, "commit": commit,
                 "quando": time.strftime("%Y-%m-%d %H:%M"), "origem": origem})
    try:
        GIT_HIST_PATH.write_text(json.dumps(hist[-100:], ensure_ascii=False, indent=1))
    except Exception:
        pass


def git_hist_ler() -> list:
    try:
        return json.loads(GIT_HIST_PATH.read_text())
    except Exception:
        return []


def webhook_ativo() -> bool:
    """True se o receptor push->deploy (vpswebhook) está no ar."""
    _, out = _run(["systemctl", "is-active", "vpswebhook.service"], timeout=5)
    return (out or "").strip() == "active"


def webhook_ultimo_push() -> str:
    """Último PUSH recebido pelo webhook (via journal). '' se nenhum."""
    rc, out = _run(["journalctl", "-u", "vpswebhook", "-n", "300",
                    "--no-pager", "-o", "short-iso"], timeout=8)
    if rc != 0 or not out:
        return ""
    for ln in reversed(out.splitlines()):
        if "PUSH " in ln:
            try:
                quando = ln.split()[0][:16].replace("T", " ")
                resto = ln.split("PUSH ", 1)[1].split(" -> ")[0]
                return f"`{resto}` · {quando}"
            except Exception:
                return ln[-80:]
    return ""


def _gh_api(metodo: str, rota: str, corpo: dict | None = None) -> tuple[int, object]:
    """Chamada crua na API do GitHub com o token do servidor."""
    import requests
    try:
        tok = (Path.home() / ".github_token").read_text().strip()
    except Exception:
        return 0, {"message": "sem ~/.github_token"}
    try:
        r = requests.request(metodo, "https://api.github.com" + rota,
                             headers={"Authorization": "Bearer " + tok,
                                      "Accept": "application/vnd.github+json"},
                             json=corpo, timeout=12)
        return r.status_code, (r.json() if r.text else {})
    except Exception as e:  # noqa: BLE001
        return 0, {"message": str(e)}


def webhook_url_atual() -> str:
    """URL pública da campainha deste servidor ('' se kit não instalado)."""
    try:
        rota = (Path.home() / ".vps_webhook_rota").read_text().strip()
    except Exception:
        return ""
    dominio = _cfg.get("dominio") or _cfg.get("ip") or ""
    return f"https://{dominio}/{rota}/" if dominio and rota else ""


@st.cache_data(ttl=300, show_spinner=False)
def gh_hook_do_repo(repo: str) -> tuple[int | None, str, int]:
    """(id, url, status_http) do webhook de deploy do repo (url com /hook-)."""
    sc, hooks = _gh_api("GET", f"/repos/{GIT_USER}/{repo}/hooks")
    if sc != 200 or not isinstance(hooks, list):
        return None, "", sc
    for h in hooks:
        u = (h.get("config", {}) or {}).get("url", "")
        if "/hook-" in u:
            return h.get("id"), u, sc
    return None, "", sc


def gh_hook_sincronizar(repo: str) -> str:
    """Cria ou aponta o webhook do repo para a campainha ATUAL."""
    alvo = webhook_url_atual()
    if not alvo:
        return "⚠️ kit do webhook não instalado neste servidor"
    try:
        segredo = (Path.home() / ".vps_webhook_secret").read_text().strip()
    except Exception:
        return "⚠️ sem ~/.vps_webhook_secret"
    cfg_h = {"url": alvo, "content_type": "json", "secret": segredo}
    hid, hurl, _ = gh_hook_do_repo(repo)
    if hid:
        sc, _r = _gh_api("PATCH", f"/repos/{GIT_USER}/{repo}/hooks/{hid}",
                         {"config": cfg_h, "events": ["push"], "active": True})
        ok = sc == 200
        msg = ("🟢 segredo/URL renovados" if hurl == alvo
               else "🔁 apontado pra campainha NOVA")
        return msg if ok else f"erro {sc}"
    sc, _r = _gh_api("POST", f"/repos/{GIT_USER}/{repo}/hooks",
                     {"config": cfg_h, "events": ["push"], "active": True})
    return "🟢 conectado" if sc == 201 else f"erro {sc}"


def gh_hook_desconectar(repo: str) -> str:
    hid, _, _ = gh_hook_do_repo(repo)
    if not hid:
        return "não tinha campainha"
    sc, _r = _gh_api("DELETE", f"/repos/{GIT_USER}/{repo}/hooks/{hid}")
    return "✂️ desconectado" if sc == 204 else f"erro {sc}"


def git_deploy(repo: str, conf: dict) -> tuple[bool, str]:
    """Atualiza producao: modo 'pull' (pasta e clone) ou modo 'mapa' (clona e espalha)."""
    import shutil
    if conf.get("pull"):
        pasta = conf["pull"]
        rc, out = _run(["env", "GIT_TERMINAL_PROMPT=0", "git", "-C", pasta,
                        "pull", "--ff-only"], timeout=180)
        if rc != 0:
            return False, "pull falhou: " + out[-300:]
        if conf.get("build"):
            rc_b, out_b = _run(["bash", "-c", f"cd {pasta} && " + conf["build"]],
                               timeout=900)
            if rc_b != 0:
                return False, ("BUILD falhou — produção segue na versão anterior "
                               "(nada foi reiniciado): " + out_b[-300:])
        _, h = _run(["git", "-C", pasta, "rev-parse", "--short=10", "HEAD"])
        est = git_estado()
        est[repo] = {"commit": (h or "?").strip(), "quando": time.strftime("%Y-%m-%d %H:%M")}
        try:
            GIT_STATE_PATH.write_text(json.dumps(est, indent=2))
        except Exception:
            pass
        git_hist_add(repo, (h or "?").strip(), "painel (↻)")
        return True, (h or "?").strip()
    mapa = conf.get("mapa", {})
    tmp = f"/tmp/deploy-{repo}"
    shutil.rmtree(tmp, ignore_errors=True)
    rc, out = _run(["env", "GIT_TERMINAL_PROMPT=0", "git", "clone", "--depth", "1",
                    f"https://github.com/{GIT_USER}/{repo}.git", tmp], timeout=180)
    if rc != 0:
        return False, "clone falhou: " + out[-300:]
    _, h = _run(["git", "-C", tmp, "rev-parse", "--short=10", "HEAD"])
    erros = []
    for origem, destino in mapa.items():
        src, dst = Path(tmp) / origem.rstrip("/"), Path(destino)
        try:
            if origem.endswith("/"):
                for item in src.rglob("*"):
                    if item.is_file() and ".git" not in item.parts:
                        alvo = dst / item.relative_to(src)
                        alvo.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, alvo)
            else:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        except Exception as e:  # noqa: BLE001
            erros.append(f"{origem}: {e}")
    shutil.rmtree(tmp, ignore_errors=True)
    if erros:
        return False, " | ".join(erros)[:300]
    est = git_estado()
    est[repo] = {"commit": (h or "?").strip(), "quando": time.strftime("%Y-%m-%d %H:%M")}
    try:
        GIT_STATE_PATH.write_text(json.dumps(est, indent=2))
    except Exception:
        pass
    git_hist_add(repo, (h or "?").strip(), "painel (↻)")
    return True, (h or "?").strip()


def checar_senha(digitada: str) -> bool:
    try:
        real = SENHA_PATH.read_text().strip()
    except Exception:
        return False
    return bool(real) and digitada == real


# ---- Sessão persistente via cookie (mantém logado após F5) ----
import hashlib

def _assinatura_sessao() -> str:
    """Hash da senha atual — serve de 'cookie de sessão'. Se a senha mudar, desloga todos."""
    try:
        return hashlib.sha256(("vpsadmin::" + SENHA_PATH.read_text().strip()).encode()).hexdigest()[:32]
    except Exception:
        return ""

try:
    from streamlit_cookies_controller import CookieController
    _cookies = CookieController()
except Exception:
    _cookies = None

COOKIE_NOME = "vpsadmin_sessao"


# ============================================================
# Login
# ============================================================

if "autenticado" not in st.session_state:
    st.session_state["autenticado"] = False

# Login persistente após F5 via PARAMETRO NA URL (confiável atrás de proxy/subpath).
# A URL guarda só um HASH da senha (não a senha). F5 preserva o parametro -> segue logado.
if not st.session_state["autenticado"]:
    try:
        if st.query_params.get("k") == _assinatura_sessao() and _assinatura_sessao():
            st.session_state["autenticado"] = True
    except Exception:
        pass

if not st.session_state["autenticado"]:
    st.markdown("<br><br><br>", unsafe_allow_html=True)
    _esq, centro, _dir = st.columns([1.4, 1.2, 1.4])
    with centro:
        with st.container(border=True):
            st.markdown(
                "<div style='text-align:center; padding: 8px 0 2px 0;'>"
                "<div style='font-size:3em;'>🛠️</div>"
                "<h2 style='margin:0;'>VPS Admin</h2>"
                "<p style='color:#6b7280; font-size:0.9em; margin-top:4px;'>"
                "Central de gestão do servidor<br>"
                f"<code>{IP_PUBLICO}</code> · Oracle Cloud ARM</p>"
                "</div>",
                unsafe_allow_html=True,
            )
            if not SENHA_PATH.exists():
                st.error(
                    "Arquivo de senha nao encontrado. Crie no servidor: "
                    "echo 'SUA_SENHA' > ~/.vps_admin_pass && chmod 600 ~/.vps_admin_pass"
                )
                st.stop()
            with st.form("login_form", border=False):
                senha = st.text_input("Senha", type="password",
                                      placeholder="Digite sua senha de administrador")
                entrar = st.form_submit_button("🔓 Entrar", type="primary",
                                               use_container_width=True)
            if entrar:
                if checar_senha(senha):
                    st.session_state["autenticado"] = True
                    try:
                        st.query_params["k"] = _assinatura_sessao()
                    except Exception:
                        pass
                    st.rerun()
                else:
                    time.sleep(1.5)
                    st.error("Senha incorreta.")
            with st.expander("🔑 Esqueci a senha"):
                _u = carregar_usuario()
                st.markdown(
                    f"Administrador: **{_u.get('nome', '—')}** "
                    f"(`{_mascarar_email(_u.get('email', ''))}`)\n\n"
                    "Redefinicao segura **via SSH** (so quem tem a chave do servidor):\n"
                    "```bash\necho 'NOVA_SENHA' > ~/.vps_admin_pass && chmod 600 ~/.vps_admin_pass\n```\n"
                    "*Recuperacao por e-mail: v3 (requer SMTP).*"
                )
        st.markdown(
            "<p style='text-align:center; color:#9ca3af; font-size:0.78em; margin-top:10px;'>"
            "VPS Admin v2.0 · acesso restrito · ações auditáveis</p>",
            unsafe_allow_html=True,
        )
    st.stop()


# ============================================================
# MENU LATERAL (estilo Maestro)
# ============================================================

with st.sidebar:
    st.markdown("## 🛠️ VPS Admin")
    st.caption(f"🔒 `{DOMINIO}`  \n`{IP_PUBLICO}` · Oracle ARM · Always Free")

    # mini-status no topo do menu
    svcs = todos_servicos()
    ativos = sum(1 for s in svcs if status_servico(s) == "active")
    if psutil:
        _cpu = psutil.cpu_percent(interval=0.2)
        _mem = psutil.virtual_memory().percent
        st.markdown(
            f"<div style='background:#e6f4ec;border-radius:8px;padding:8px 12px;font-size:0.85em;'>"
            f"🟢 <b>{ativos}/{len(svcs)}</b> serviços ativos<br>"
            f"⚙️ CPU {_cpu:.0f}% &nbsp;·&nbsp; 🧠 RAM {_mem:.0f}%</div>",
            unsafe_allow_html=True,
        )
    # Status do MCP — a ponte do Claude com o servidor (sempre visível)
    _mcp_on = mcp_online()
    st.markdown(
        f"<div style='background:{'#e6f4ec' if _mcp_on else '#fbeae7'};border-radius:8px;"
        f"padding:8px 12px;font-size:0.85em;margin-top:6px;'>"
        f"{'🟢 <b>MCP Online</b>' if _mcp_on else '🔴 <b>MCP Offline</b>'} "
        f"<span style='opacity:.7'>· conexão do Claude</span></div>",
        unsafe_allow_html=True,
    )
    st.divider()

    # Menu de botoes — mesmo padrao do Escola Parque (primary = pagina ativa)
    PAGINAS = [
        "📊 Dashboard",
        "🚀 Aplicativos",
        "🌐 Domínios & Rotas",
        "🌿 Git & Deploys",
        "🦙 Ollama (IA local)",
        "🔑 API da LLM",
        "🔌 Acesso MCP (Claude)",
        "💾 Servidor & Limites",
        "👤 Conta",
    ]
    if "pagina" not in st.session_state:
        try:
            _pq = st.query_params.get("p")
        except Exception:
            _pq = None
        st.session_state["pagina"] = _pq if _pq in PAGINAS else PAGINAS[0]
    for _p in PAGINAS:
        if st.button(
            _p,
            type="primary" if st.session_state["pagina"] == _p else "secondary",
            use_container_width=True,
            key=f"nav_{_p}",
        ):
            st.session_state["pagina"] = _p
            try:
                st.query_params["p"] = _p
            except Exception:
                pass
            st.rerun()
    pagina = st.session_state["pagina"]

    st.divider()
    if st.button("🔄 Atualizar", use_container_width=True):
        st.rerun()
    if st.button("🚪 Sair", use_container_width=True):
        st.session_state["autenticado"] = False
        try:
            st.query_params.clear()
        except Exception:
            pass
        st.rerun()


# ============================================================
# PAGINA: Dashboard
# ============================================================

if pagina == "📊 Dashboard":
    st.title("📊 Dashboard")
    if psutil:
        cpu = psutil.cpu_percent(interval=0.4)
        mem = psutil.virtual_memory()
        disco = psutil.disk_usage("/")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("CPU", f"{cpu:.0f}%")
        c2.metric("RAM", f"{mem.percent:.0f}%", f"{mem.used/1e9:.1f} / {mem.total/1e9:.0f} GB")
        c3.metric("Disco /", f"{disco.percent:.0f}%", f"{disco.used/1e9:.1f} / {disco.total/1e9:.0f} GB")
        try:
            carga = ", ".join(f"{x:.2f}" for x in psutil.getloadavg())
        except Exception:
            carga = "—"
        c4.metric("Load (1/5/15m)", carga)

    st.divider()
    st.subheader("🚦 Visao geral dos serviços")
    cols = st.columns(3)
    for i, (nome, rotulo) in enumerate(todos_servicos().items()):
        stt = status_servico(nome)
        cor = {"active": "🟢", "inactive": "⚪", "failed": "🔴"}.get(stt, "🟡")
        with cols[i % 3]:
            with st.container(border=True):
                st.markdown(f"**{cor} {rotulo}**")
                st.caption(f"`{nome}` · {stt}")

    st.divider()
    st.subheader("🌿 Git & Deploys")
    _cols_g = st.columns(3)
    for _i, (_repo, _conf) in enumerate(todos_git_projetos().items()):
        with _cols_g[_i % 3]:
            with st.container(border=True):
                st.markdown(f"**{_conf.get('rotulo', _repo)}**")
                st.caption(f"`{_repo}` · {git_situ_curta(_repo, _conf)}")

    st.divider()
    rotas = rotas_nginx()
    if rotas:
        st.subheader("🌐 Acessos rapidos")
        links = " · ".join(
            f"[{r}]({URL_BASE}{r})" for r in rotas
            if not r.startswith("=") and not r.startswith("/mcp-")
        )
        st.markdown(links)
        st.caption("🔒 A rota do MCP não aparece aqui de propósito — é segredo (veja em Acesso MCP).")


# ============================================================
# PAGINA: Aplicativos
# ============================================================

elif pagina == "🚀 Aplicativos":
    c_titulo, c_novo = st.columns([5, 1.4], vertical_alignment="center")
    with c_titulo:
        st.title("🚀 Aplicativos & Serviços")
    with c_novo:
        if st.button("➕ Novo App", type="primary", use_container_width=True):
            st.session_state["pagina"] = "➕ Novo App"
            st.rerun()
    tab_apps, tab_libs = st.tabs(["🚀 Apps & Serviços", "📚 Bibliotecas"])

    with tab_apps:
        st.caption("Ações restritas à whitelist — sem terminal livre, por segurança.")
        extras = carregar_apps_extras()
        _git_svc: dict[str, str] = {}
        _git_situ: dict[str, str] = {}
        for _r, _c in todos_git_projetos().items():
            _git_situ[_r] = git_situ_curta(_r, _c)
            for _s in _c.get("servicos", []):
                _git_svc[_s] = _r
        for nome, rotulo in todos_servicos().items():
            stt = status_servico(nome)
            cor = {"active": "🟢", "inactive": "⚪", "failed": "🔴"}.get(stt, "🟡")
            with st.container(border=True):
                # Layout FIXO p/ todas as linhas (alinhamento consistente):
                # rótulo | Restart | Stop/Start | Logs | Acessar(verde, à direita)
                c1, c2, c3, c4, c5 = st.columns(
                    [3.6, 1.2, 1.2, 1.0, 1.3], vertical_alignment="center"
                )
                _g = _git_svc.get(nome)
                _gtxt = f" · 🌿 `{_g}` {_git_situ.get(_g, '')}" if _g else ""
                c1.markdown(f"**{cor} {rotulo}**  \n`{nome}` · status: `{stt}`{_gtxt}")
                if c2.button("Restart", key=f"r_{nome}", use_container_width=True):
                    ok, msg = acao_servico(nome, "restart")
                    (st.success if ok else st.error)(msg[:400])
                    time.sleep(1)
                    st.rerun()
                if stt == "active":
                    if c3.button("Stop", key=f"s_{nome}", use_container_width=True):
                        ok, msg = acao_servico(nome, "stop")
                        (st.success if ok else st.error)(msg[:400])
                        time.sleep(1)
                        st.rerun()
                else:
                    if c3.button("Start", key=f"i_{nome}", use_container_width=True):
                        ok, msg = acao_servico(nome, "start")
                        (st.success if ok else st.error)(msg[:400])
                        time.sleep(1)
                        st.rerun()
                mostrar = c4.toggle("Logs", key=f"l_{nome}")
                if nome in ROTAS_APPS:
                    c5.markdown(
                        f'<a href="{URL_BASE}{ROTAS_APPS[nome]}" target="_blank" '
                        f'style="display:inline-block;width:100%;box-sizing:border-box;'
                        f'background:#16a34a;color:#fff;text-decoration:none;'
                        f'padding:.34rem .2rem;border-radius:.5rem;font-weight:600;'
                        f'font-size:.84rem;text-align:center;white-space:nowrap;">'
                        f'↗ Acessar</a>',
                        unsafe_allow_html=True,
                    )
                if mostrar:
                    st.code(logs_servico(nome), language="log")
                if nome in extras:
                    if st.button("🗑️ Remover do painel (nao desinstala)", key=f"rm_{nome}"):
                        extras.pop(nome, None)
                        salvar_apps_extras(extras)
                        st.rerun()

    with tab_libs:
        with st.spinner("Varrendo os ambientes dos apps..."):
            libs = listar_bibliotecas()
        if not libs:
            st.caption("Nenhum venv encontrado em /home/ubuntu/*/.venv.")
        else:
            total = sum(len(v) for v in libs.values())
            cols_resumo = st.columns(len(libs) + 1)
            cols_resumo[0].metric("📚 Total", total)
            for i, (app_nome, pacotes) in enumerate(libs.items(), start=1):
                cols_resumo[i].metric(app_nome, len(pacotes))
            st.caption(
                "Cada app tem seu ambiente ISOLADO (venv) — versões podem diferir entre apps "
                "sem conflito. Clique num app pra ver/filtrar a lista completa. Atualiza a cada 5 min."
            )
            for app_nome, pacotes in libs.items():
                with st.expander(f"📦 **{app_nome}** — {len(pacotes)} bibliotecas"):
                    filtro = st.text_input("🔎 Filtrar por nome", key=f"libf_{app_nome}")
                    dados = (
                        [p for p in pacotes if filtro.lower() in p.get("name", "").lower()]
                        if filtro else pacotes
                    )
                    st.dataframe(dados, use_container_width=True, height=320, hide_index=True)


# ============================================================
# PAGINA: Novo App (gerador de kit de deploy)
# ============================================================

elif pagina == "➕ Novo App":
    if st.button("← Voltar aos Aplicativos"):
        st.session_state["pagina"] = "🚀 Aplicativos"
        st.rerun()
    st.title("➕ Novo App no servidor")
    st.caption(
        "Preencha e o painel gera o KIT DE DEPLOY completo (comandos prontos) "
        "+ registra o app aqui no painel. Padrao da casa: venv proprio + systemd + rota Nginx."
    )

    with st.form("novo_app"):
        c1, c2 = st.columns(2)
        nome = c1.text_input("Nome do serviço (sem espacos, ex.: sertanejolab)")
        porta = c2.number_input("Porta interna", min_value=8502, max_value=8599, value=8502)
        c3, c4 = st.columns(2)
        pasta = c3.text_input("Pasta no servidor", value="~/meu-app")
        principal = c4.text_input("Arquivo principal", value="app.py")
        rota = st.text_input("Rota no Nginx (ex.: /sertanejo)", value="/meu-app")
        rotulo = st.text_input("Rótulo no painel (com emoji!)", value="🎸 Meu App")
        gerar = st.form_submit_button("⚙️ Gerar kit de deploy", type="primary")

    if gerar and nome and rota.startswith("/"):
        pasta_abs = pasta.replace("~", "/home/ubuntu")
        st.success(f"Kit gerado para **{nome}** — siga os 3 passos:")

        st.markdown("**1️⃣ Enviar o projeto (PowerShell no PC):**")
        st.code(
            f'scp -i "$HOME\\.ssh\\ssh-key-2026-06-03.key" -r "C:\\CAMINHO\\DO\\PROJETO" '
            f"ubuntu@{IP_PUBLICO}:{pasta}",
            language="powershell",
        )

        st.markdown("**2️⃣ Instalar no servidor (terminal SSH, bloco único):**")
        st.code(
            f"""cd {pasta} && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
sudo tee /etc/systemd/system/{nome}.service > /dev/null <<'EOF'
[Unit]
Description={rotulo}
After=network.target

[Service]
User=ubuntu
WorkingDirectory={pasta_abs}
ExecStart={pasta_abs}/.venv/bin/streamlit run {principal} --server.port {porta} --server.address 127.0.0.1 --server.headless true --server.baseUrlPath {rota.strip('/')}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now {nome}
sudo sed -i '/^}}$/i\\
    location {rota}/ {{\\
        proxy_pass http://127.0.0.1:{porta}{rota}/;\\
        proxy_http_version 1.1;\\
        proxy_set_header Upgrade $http_upgrade;\\
        proxy_set_header Connection "upgrade";\\
        proxy_set_header Host $host;\\
        proxy_read_timeout 86400;\\
    }}' /etc/nginx/sites-available/apps
sudo nginx -t && sudo systemctl reload nginx""",
            language="bash",
        )

        st.markdown(f"**3️⃣ Testar:** `{URL_BASE}{rota}/`")

        extras = carregar_apps_extras()
        extras[nome] = rotulo
        if salvar_apps_extras(extras):
            st.info(f"✅ **{rotulo}** ja registrado no painel (aba Aplicativos).")
    elif gerar:
        st.error("Preencha o nome e uma rota começando com / .")


# ============================================================
# PAGINA: Rotas Nginx
# ============================================================

elif pagina == "🌐 Domínios & Rotas":
    c_tit_d, c_novo_d = st.columns([4.5, 1.6], vertical_alignment="center")
    with c_tit_d:
        st.title("🌐 Domínios & Rotas")
    with c_novo_d:
        if st.button("➕ Novo domínio", type="primary", use_container_width=True):
            st.session_state["form_dom"] = not st.session_state.get("form_dom", False)

    if st.session_state.get("form_dom"):
        with st.container(border=True):
            st.markdown("**Apontar um domínio novo pra um app deste servidor** "
                        "(ex.: frontend Next na porta 3000)")
            with st.form("novo_dominio", border=False):
                d1, d2, d3 = st.columns([2.2, 1.4, 1], vertical_alignment="bottom")
                dom_novo = d1.text_input("Domínio completo",
                                         placeholder="meuapp.duckdns.org")
                porta_dom = d2.number_input("Porta interna do app", min_value=3000,
                                            max_value=8999, value=3000)
                ok_dom = d3.form_submit_button("Gerar kit 🔧", type="primary",
                                               use_container_width=True)
            if ok_dom and dom_novo.strip():
                _d = dom_novo.strip()
                _slug = _d.split(".")[0]
                st.markdown("**1️⃣ DNS:** crie o subdomínio no provedor "
                            f"(DuckDNS: add domain `{_slug}`) apontando pra `{IP_PUBLICO}`.")
                st.markdown("**2️⃣ SSH (bloco único):**")
                st.code(f"""sudo tee /etc/nginx/sites-available/{_slug} > /dev/null <<'EOF'
server {{
    listen 80;
    server_name {_d};
    location / {{
        proxy_pass http://127.0.0.1:{porta_dom};
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }}
}}
EOF
sudo ln -sf /etc/nginx/sites-available/{_slug} /etc/nginx/sites-enabled/{_slug}
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d {_d} --redirect -m diogobsbastos@gmail.com --agree-tos --no-eff-email""",
                        language="bash")
                st.caption("O domínio aparece na lista abaixo sozinho depois de criado.")

    st.subheader("🌍 Domínios deste servidor")
    for _dm in dominios_nginx():
        with st.container(border=True):
            _val_d = cert_validade_cache(_dm["dominio"]) if _dm["ssl"] else None
            c_dmi, c_dmx = st.columns([5.4, 0.4], vertical_alignment="center")
            c_dmi.markdown(
                f"**{'🔒' if _dm['ssl'] else '⚠️ sem SSL'} "
                f"[{_dm['dominio']}](https://{_dm['dominio']})**  \n"
                f"→ **{alvo_amigavel(_dm['alvo'])}**"
                + (f" <small><span style='color:#9ca3af'>· cert até {_val_d}"
                   f" · conf `{_dm['arquivo']}`</span></small>" if _val_d
                   else f" <small><span style='color:#9ca3af'>· conf `{_dm['arquivo']}`</span></small>"),
                unsafe_allow_html=True,
            )
            if _dm["arquivo"] != "apps":
                with c_dmx.popover("✕", use_container_width=True):
                    st.markdown(f"**Remover o domínio `{_dm['dominio']}`?**")
                    st.caption(
                        "O app continua rodando — só o ENDEREÇO deixa de existir. "
                        "Por segurança o painel não executa isso sozinho: rode no SSH:"
                    )
                    st.code(
                        f"sudo rm -f /etc/nginx/sites-enabled/{_dm['arquivo']} "
                        f"/etc/nginx/sites-available/{_dm['arquivo']}\n"
                        f"sudo nginx -t && sudo systemctl reload nginx\n"
                        f"sudo certbot delete --cert-name {_dm['dominio']} -n",
                        language="bash",
                    )
            else:
                c_dmx.markdown("<span title='Domínio principal — hospeda o painel; "
                               "não removível por aqui'>🏛️</span>", unsafe_allow_html=True)

    st.divider()
    st.subheader("🛣️ Rotas internas do domínio principal")
    with st.container(border=True):
        c_dom, c_duck = st.columns([4.2, 1.3], vertical_alignment="center")
        c_dom.markdown(
            f"**🔒 Domínio & HTTPS:** [`{DOMINIO}`]({URL_BASE}) → `{IP_PUBLICO}`  \n"
            f"DNS grátis **DuckDNS** (conta Google: `diogobsbastos@gmail.com`) · "
            f"certificado **Let's Encrypt** — renovação automática a cada 90 dias (certbot)."
        )
        c_duck.link_button("🦆 DuckDNS", "https://www.duckdns.org",
                           use_container_width=True)
    rotas = rotas_nginx()
    if rotas:
        for r in rotas:
            if r.startswith("/mcp-"):
                st.markdown("- 🔒 `/mcp-…/` → rota SECRETA do MCP (oculta de propósito — ver Acesso MCP)")
                continue
            _rr = "/" if r.startswith("=") else r
            _sufx = " *(página inicial)*" if r.startswith("=") else ""
            st.markdown(f"- `{_rr}`{_sufx} → [{URL_BASE}{_rr}]({URL_BASE}{_rr})")
    else:
        st.warning("Não consegui ler a config (permissão).")
    st.divider()
    st.caption(
        "Para criar rota nova use a aba ➕ Novo App. Edição manual: "
        "`sudo nano /etc/nginx/sites-available/apps` + `sudo nginx -t` + `sudo systemctl reload nginx`."
    )


# ============================================================
# PAGINA: Git & Deploys
# ============================================================

elif pagina == "🌿 Git & Deploys":
    c_t, c_add, c_gh = st.columns([3.6, 1.5, 1.2], vertical_alignment="center")
    with c_t:
        st.title("🌿 Git & Deploys")
    with c_add:
        if st.button("➕ Conectar repo", type="primary", use_container_width=True):
            st.session_state["form_repo"] = not st.session_state.get("form_repo", False)
    with c_gh:
        st.link_button("🐙 GitHub", f"https://github.com/{GIT_USER}?tab=repositories",
                       use_container_width=True)
    if st.session_state.get("form_repo"):
        with st.container(border=True):
            st.markdown("**Conectar um repositório do GitHub a uma pasta do servidor**")
            with st.form("conectar_repo", clear_on_submit=True, border=False):
                f1, f2 = st.columns(2)
                repo_novo = f1.text_input(f"Repo (em github.com/{GIT_USER}/...)",
                                          placeholder="ex.: sertanejo-lab")
                rotulo_novo = f2.text_input("Rótulo no painel (com emoji!)",
                                            placeholder="🎸 Sertanejo Lab")
                f3, f4 = st.columns(2)
                pasta_nova = f3.text_input("Pasta no servidor (deve ser um clone do repo)",
                                           placeholder="/home/ubuntu/sertanejo-lab")
                svc_novos = f4.multiselect("Serviços a reiniciar no deploy",
                                           list(todos_servicos().keys()))
                build_novo = st.text_input(
                    "Comando de build (opcional — apps compilados, ex. Next.js)",
                    placeholder="npm install && npm run build",
                    help="Roda na pasta APÓS o pull e ANTES do restart. Se falhar, "
                         "nada é reiniciado (a produção continua na versão antiga).",
                )
                ok_repo = st.form_submit_button("Conectar 🌿", type="primary")
            if ok_repo and repo_novo.strip() and pasta_nova.strip():
                extras_r = git_projetos_extras()
                extras_r[repo_novo.strip()] = {
                    "rotulo": rotulo_novo.strip() or repo_novo.strip(),
                    "pull": pasta_nova.strip(),
                    "servicos": svc_novos,
                }
                if build_novo.strip():
                    extras_r[repo_novo.strip()]["build"] = build_novo.strip()
                if salvar_git_projetos(extras_r):
                    st.session_state["form_repo"] = False
                    st.rerun()
                else:
                    st.error("Falha ao salvar o registro.")
            elif ok_repo:
                st.error("Preencha pelo menos o repo e a pasta.")
    st.caption(
        "A ponte oficial da casa: **PC (oficina) → GitHub privado (cartório) → "
        "Servidor (produção)**. ↻ Atualizar = puxa o último commit, aplica nas "
        "pastas de produção (sem tocar nos venvs) e reinicia os serviços do projeto. "
        "Histórico e rollback ficam no GitHub."
    )

    @st.fragment(run_every=3)
    def _status_deploy():
        seg = autodeploy_proximo()
        if seg == -1:
            st.markdown("### 🔨 Deploy em andamento")
            st.progress(1.0, text="o vigia está aplicando (pull/build/restart) — "
                                  "esta faixa volta ao normal quando ele terminar")
            return
        hook_on = webhook_ativo()
        ultimo = webhook_ultimo_push()
        with st.container(border=True):
            c_w, c_u, c_r = st.columns([1.9, 2.7, 1.7], vertical_alignment="center")
            c_w.markdown(
                ("⚡ **Webhook** 🟢 ativo  \n<small>push no GitHub → deploy em ~5s</small>")
                if hook_on else
                ("⚡ **Webhook** 🔴 fora do ar  \n<small>deploys só pela ronda — "
                 "conferir serviço `vpswebhook`</small>"),
                unsafe_allow_html=True,
            )
            c_u.markdown(
                ("📨 **Último push recebido**  \n<small>" + ultimo + "</small>")
                if ultimo else
                ("📨 **Último push recebido**  \n<small>nenhum ainda — faça um "
                 "commit e veja a mágica</small>"),
                unsafe_allow_html=True,
            )
            if seg is None:
                c_r.markdown("🕐 **Ronda de segurança**  \n<small>timer não "
                             "instalado</small>", unsafe_allow_html=True)
            else:
                m, s2 = divmod(int(seg), 60)
                c_r.markdown(f"🕐 **Ronda de segurança**  \n<small>próxima em "
                             f"`{m:02d}:{s2:02d}` · rede de segurança do webhook</small>",
                             unsafe_allow_html=True)
    _status_deploy()

    with st.expander("🪝 Campainha — conectar webhooks / migrar de servidor"):
        st.caption(
            "A campainha é **uma só** (rota secreta + segredo HMAC, vivem neste "
            "servidor). Cada repo do GitHub aponta pra ela. **Migrou de servidor "
            "ou domínio?** Instale o kit (`infra/INSTALL_WEBHOOK.md`) no novo e "
            "clique 🔁 abaixo — todos os repos passam a tocar a campainha nova."
        )
        _url_alvo = webhook_url_atual()
        if not _url_alvo:
            st.warning("Kit do webhook não instalado aqui (sem ~/.vps_webhook_rota). "
                       "Receita: infra/INSTALL_WEBHOOK.md no repo do painel.")
        else:
            st.markdown(f"<small>📍 campainha deste servidor: `{_url_alvo}`</small>",
                        unsafe_allow_html=True)
            if st.button("🔁 Conectar/atualizar TODOS os repos para esta campainha",
                         type="primary", use_container_width=True):
                for _r in todos_git_projetos():
                    st.write(f"`{_r}`: {gh_hook_sincronizar(_r)}")
                gh_hook_do_repo.clear()
            st.divider()
            for _r in todos_git_projetos():
                _hid, _hurl, _sc = gh_hook_do_repo(_r)
                if _sc != 200:
                    _situ_h = f"🟡 GitHub: {_sc or 'sem acesso'} (token precisa da permissão Webhooks)"
                elif not _hid:
                    _situ_h = "⚪ sem campainha (push NÃO avisa este servidor)"
                elif _hurl == _url_alvo:
                    _situ_h = "🟢 conectado nesta campainha"
                else:
                    _situ_h = "🟠 aponta pra OUTRA campainha (servidor antigo?)"
                _cA, _cB, _cC = st.columns([3.6, 1.1, 1.3],
                                           vertical_alignment="center")
                _cA.markdown(f"`{_r}` · {_situ_h}")
                if _cB.button("🔗 Conectar", key=f"whc_{_r}",
                              use_container_width=True):
                    st.toast(f"{_r}: {gh_hook_sincronizar(_r)}")
                    gh_hook_do_repo.clear()
                    st.rerun()
                if _cC.button("✂️ Desconectar", key=f"whd_{_r}",
                              use_container_width=True):
                    st.toast(f"{_r}: {gh_hook_desconectar(_r)}")
                    gh_hook_do_repo.clear()
                    st.rerun()

    # popovers ⋯ compactos e uniformes (todos no tamanho MENOR)
    st.markdown("<style>div[data-testid='stPopoverBody']"
                "{width:min(380px,92vw);min-width:0;}"
                "div[data-testid='stPopoverBody'] [data-testid='stVerticalBlock']"
                "{max-width:100%;}"
                "div[data-testid='stPopover'] button svg,"
                "div[data-testid='stPopover'] button [data-testid='stIconMaterial']"
                "{display:none;}"
                "div[data-testid='stPopover'] button"
                "{justify-content:center;text-align:center;"
                "padding:0.15rem 0.3rem;min-height:1.8rem;height:1.8rem;}"
                "div[data-testid='stPopover'] button p"
                "{margin:0;line-height:1;}</style>", unsafe_allow_html=True)

    estado = git_estado()
    _extras_git = git_projetos_extras()
    for repo, conf in todos_git_projetos().items():
        with st.container(border=True):
            remoto = git_remote_head(repo)
            info = estado.get(repo, {})
            local = info.get("commit", "—")
            if conf.get("pull"):
                _, _h = _run(["git", "-C", conf["pull"], "rev-parse", "--short=10", "HEAD"])
                local = (_h or "").strip() if _h and "fatal" not in _h else "—"
            if remoto == "?":
                situ = "🟡 GitHub inacessível (credencial?)"
            elif local == "—":
                situ = "⚪ nunca deployado pelo painel"
            elif remoto == local:
                situ = "🟢 em dia com o GitHub"
            else:
                situ = "🟠 atualização disponível!"
            c1, c0, c2, c3, cx = st.columns([3.2, 0.9, 1.2, 1.2, 0.4],
                                            vertical_alignment="center")
            c1.markdown(
                f"**{conf['rotulo']}**  \n"
                f"`{repo}` · GitHub `{remoto}` · produção `{local}`  \n"
                f"{situ} <small><span style='color:#9ca3af'>· "
                f"{info.get('quando', 'sem registro')}</span></small>",
                unsafe_allow_html=True,
            )
            _auto_atual = bool(conf.get("auto"))
            _auto = c0.toggle("⚙️ auto", value=_auto_atual, key=f"auto_{repo}",
                              help="Auto-deploy: push no GitHub → webhook dispara o vigia "
                                   "na hora (~5s); a ronda de 2 min cobre qualquer "
                                   "falha. Desligado = só deploy manual pelo ↻.")
            if _auto != _auto_atual:
                _ex = git_projetos_extras()
                _ex[repo] = {**conf, "auto": _auto}
                salvar_git_projetos(_ex)
                st.rerun()
            c2.link_button("Ver repo", f"https://github.com/{GIT_USER}/{repo}",
                           use_container_width=True)
            if c3.button("↻ Atualizar", key=f"dep_{repo}", type="primary",
                         use_container_width=True):
                if remoto != "?" and local not in ("—", "") and remoto == local:
                    st.info("✅ Já está em dia com o GitHub — nada a atualizar. "
                            "Commit novo entra sozinho (webhook, ~5s). Precisa "
                            "reaplicar à força? Use ⋯ → ↻ Forçar redeploy.")
                else:
                    st.info("⏳ Puxando do GitHub e aplicando... o painel vai PISCAR "
                            "no fim (reinicia a si mesmo). Dê F5 em ~10s.")
                    ok, msg = git_deploy(repo, conf)
                    if ok:
                        st.success(f"✅ Commit `{msg}` aplicado. Reiniciando: "
                                   + ", ".join(conf["servicos"]))
                        for s in conf["servicos"]:
                            acao_servico(s, "restart")
                            time.sleep(1)
                        st.rerun()
                    else:
                        st.error("Deploy falhou: " + msg)
            with cx.popover("⋯", use_container_width=True):
                st.markdown(f"**⚙️ Configurar `{repo}`**")
                with st.form(f"edit_{repo}", border=False):
                    e_rot = st.text_input("Rótulo", value=conf.get("rotulo", repo))
                    e_pull = st.text_input(
                        "Pasta (clone) no servidor",
                        value=conf.get("pull", ""),
                        help="Vazio = mantém o modo atual (ex.: mapa do VPS Admin).",
                    )
                    e_build = st.text_input(
                        "Comando de build (opcional)",
                        value=conf.get("build", ""),
                        placeholder="npm install && npm run build",
                        help="Roda após o pull, antes do restart. Build falhou = "
                             "nada reinicia (produção segue na versão anterior).",
                    )
                    e_svc = st.multiselect(
                        "Serviços a reiniciar",
                        list(todos_servicos().keys()),
                        default=[x for x in conf.get("servicos", [])
                                 if x in todos_servicos()],
                    )
                    sv_ed = st.form_submit_button("💾 Salvar", type="primary",
                                                  use_container_width=True)
                if sv_ed:
                    _ex_ed = git_projetos_extras()
                    novo_conf = {**conf, "rotulo": e_rot.strip() or repo,
                                 "servicos": e_svc}
                    if e_pull.strip():
                        novo_conf["pull"] = e_pull.strip()
                    if e_build.strip():
                        novo_conf["build"] = e_build.strip()
                    else:
                        novo_conf.pop("build", None)
                    _ex_ed[repo] = novo_conf
                    salvar_git_projetos(_ex_ed)
                    st.rerun()
                st.divider()
                if st.button("↻ Forçar redeploy", key=f"force_{repo}",
                             use_container_width=True,
                             help="Reaplica o commit atual do GitHub mesmo já "
                                  "estando em dia (reinstala arquivos + restart)."):
                    ok_f, msg_f = git_deploy(repo, conf)
                    if ok_f:
                        for s in conf["servicos"]:
                            acao_servico(s, "restart")
                            time.sleep(1)
                        st.rerun()
                    else:
                        st.error("Forçar redeploy falhou: " + msg_f)
                if repo in _extras_git and repo not in GIT_PROJETOS:
                    st.divider()
                    st.caption("Remover do painel — o app continua rodando; "
                               "não mexe no GitHub nem nos arquivos.")
                    if st.button("✕ Remover do painel", key=f"rmconf_{repo}",
                                 use_container_width=True):
                        _extras_git.pop(repo, None)
                        salvar_git_projetos(_extras_git)
                        st.rerun()
    hist = git_hist_ler()
    if hist:
        with st.expander(f"📜 Histórico de deploys ({len(hist)})"):
            for ev in reversed(hist[-30:]):
                st.markdown(
                    f"<small>`{ev.get('quando', '?')}` · **{ev.get('repo', '?')}** · "
                    f"`{ev.get('commit', '?')}` · {ev.get('origem', '?')}</small>",
                    unsafe_allow_html=True,
                )
    st.divider()
    st.caption(
        "⚡ Fluxo da casa: commit → GitHub toca a campainha (webhook) → vigia aplica "
        "em ~5s. A ronda de 2 min é rede de segurança. O Claude opera esta ponte via MCP."
    )


# ============================================================
# PAGINA: Ollama
# ============================================================

elif pagina == "🦙 Ollama (IA local)":
    st.title("🦙 Ollama — LLM local")

    # ---- Modo 24h na RAM (modelo residente) ----
    _24h_atual = bool(_cfg.get("ollama_24h"))
    with st.container(border=True):
        c_tg, c_tx = st.columns([1.3, 4.2], vertical_alignment="center")
        lig24 = c_tg.toggle("🔥 **24h na RAM**", value=_24h_atual, key="tg_24h")
        c_tx.markdown(
            "**Ligado:** o modelo fica **residente na memória** → resposta imediata, sem o "
            "\"modelo carregando\" (ocupa ~o tamanho do modelo em RAM — temos folga: 24 GB).  \n"
            "**Desligado:** o Ollama descarrega após ~5 min ocioso → economiza RAM, mas o "
            "1º pedido depois da pausa leva 30-60s recarregando do disco. "
            "*Cada uso pela API renova as 24h.*"
        )
    if lig24 != _24h_atual:
        config_salvar("ollama_24h", lig24)
        _alvos = [m.get("name", "") for m in ollama_modelos() if m.get("name")]
        with st.spinner(("Carregando modelo(s) na RAM (até 1 min)..." if lig24
                         else "Descarregando modelo(s) da RAM...")):
            _oks = [ollama_manter_na_ram(_m, lig24) for _m in _alvos]
        if all(_oks):
            st.success("🔥 Modelo(s) residentes na RAM por 24h — resposta imediata."
                       if lig24 else "💤 RAM liberada — modelos carregam sob demanda.")
        else:
            st.warning("Config salva, mas algum modelo não respondeu — confira o serviço ollama.")
        time.sleep(1.2)
        st.rerun()

    modelos = ollama_modelos()
    if modelos:
        st.subheader("Modelos instalados")
        for m in modelos:
            nome_m = m.get("name", "")
            with st.container(border=True):
                c1, c2, c3 = st.columns([4.5, 1.3, 1.3])
                c1.markdown(f"**`{nome_m}`** · {m.get('size', 0)/1e9:.1f} GB")
                specs_on = c2.toggle("📋 Specs", key=f"olsp_{nome_m}")
                if c3.button("🗑️ Remover", key=f"olrm_{nome_m}", use_container_width=True):
                    with st.spinner("Removendo..."):
                        rc, out = _run(["ollama", "rm", nome_m], timeout=120)
                    (st.success if rc == 0 else st.error)(out[:300] or "Removido.")
                    time.sleep(1)
                    st.rerun()

                if specs_on:
                    info = ollama_show(nome_m)
                    det = info.get("details", {}) or {}
                    mi = info.get("model_info", {}) or {}
                    ctx = next((v for k, v in mi.items() if k.endswith("context_length")), "?")
                    emb = next((v for k, v in mi.items() if k.endswith("embedding_length")), "?")

                    st.markdown("##### 🧬 Especificações do modelo")
                    e1, e2, e3, e4 = st.columns(4)
                    e1.metric("Família", str(det.get("family", "?")))
                    e2.metric("Parâmetros", str(det.get("parameter_size", "?")))
                    e3.metric("Quantização", str(det.get("quantization_level", "?")))
                    e4.metric("Contexto máx.", f"{ctx:,}".replace(",", ".") if isinstance(ctx, int) else str(ctx))
                    st.caption(
                        f"Formato: `{det.get('format', '?')}` · Embedding: `{emb}` · "
                        f"⚠️ Limites desta máquina: CPU ARM (sem GPU) ≈ 2-5 tokens/s neste porte; "
                        f"1 requisição por vez (fila); RAM ocupada ao usar ≈ tamanho do modelo + contexto."
                    )

                    st.markdown("##### 📡 Endereços de acesso")
                    st.markdown(
                        f"""
| De onde | Endereço | Uso |
|---|---|---|
| **Dentro do servidor** (apps deste VPS) | `http://localhost:11434` | É o que o LiteLLM/worker usam |
| **API estilo OpenAI** (compatível) | `http://localhost:11434/v1` | base_url p/ LiteLLM/SDKs |
| **Rede interna Oracle** (outra VM da VCN) | `http://10.0.0.237:11434` | entre máquinas suas |
| **Do seu PC (seguro)** | túnel SSH ⤵️ | recomendado |
"""
                    )
                    st.markdown("**Acessar do seu PC via túnel SSH** (abre e deixa aberto):")
                    st.code(
                        'ssh -i "$HOME\\.ssh\\ssh-key-2026-06-03.key" -N -L 11434:localhost:11434 '
                        f'ubuntu@{IP_PUBLICO}',
                        language="powershell",
                    )
                    st.caption(
                        "Com o túnel ativo, seu PC enxerga este Ollama em `http://localhost:11434` "
                        "como se fosse local. 🔒 NÃO abrimos a porta 11434 pra internet de propósito: "
                        "o Ollama não tem senha — porta pública = qualquer um usando sua máquina."
                    )
                    st.markdown("**Teste rápido (dentro do servidor):**")
                    st.code(
                        f"curl http://localhost:11434/api/generate -d "
                        f"'{{\"model\": \"{nome_m}\", \"prompt\": \"Diga OK\", \"stream\": false}}'",
                        language="bash",
                    )
    else:
        st.warning("Ollama sem resposta em localhost:11434 (serviço parado?).")

    st.divider()
    c_tit, c_atu = st.columns([4, 1.6], vertical_alignment="center")
    with c_tit:
        st.subheader("⬇️ Baixar modelo novo")
    with c_atu:
        if st.button("🔄 Atualizar Lista", use_container_width=True):
            with st.spinner("Buscando lista de modelos (ollama.com)..."):
                ok, qtd = atualizar_catalogo_ollama()
            if ok:
                st.success(f"Catálogo atualizado: {qtd} modelos.")
                time.sleep(1)
                st.rerun()
            else:
                st.error("Falha ao buscar o catálogo (rede?). Usando lista local.")

    catalogo = catalogo_ollama()
    st.caption(
        f"{len(catalogo)} modelos no catálogo · digite abaixo pra FILTRAR · "
        "populares têm tamanho real; demais mostram '—' (o tamanho exato confirma no download, "
        "e os instalados acima já exibem o tamanho real) · dica desta máquina (CPU ARM): até ~10 GB"
    )

    opcoes = [
        f"{it['nome']}   —   {it['tamanho']}"
        if it.get("tamanho") and it["tamanho"] not in ("—", "?")
        else it["nome"]
        for it in catalogo
    ]
    escolha = st.selectbox(
        "🔎 Buscar modelo (nome + tamanho juntos — digite pra filtrar)",
        opcoes,
    )
    modelo_final = escolha.split()[0] if escolha else ""

    # Consulta o tamanho REAL no registro oficial ao selecionar (com cache de sessao)
    if modelo_final:
        if st.session_state.get("_tam_nome") != modelo_final:
            with st.spinner("Consultando tamanho no registro oficial..."):
                st.session_state["_tam_nome"] = modelo_final
                st.session_state["_tam_val"] = ollama_tamanho_remoto(modelo_final)
        tam_real = st.session_state.get("_tam_val", "?")
        if tam_real != "?":
            gb = float(tam_real.split()[0])
            params_b = gb / 0.6  # Q4: ~0,6 GB por bilhao de parametros
            cor_tam = "🟢" if gb <= 10 else "⚠️"
            st.markdown(
                f"📦 **Tamanho do download:** {tam_real} {cor_tam} &nbsp;·&nbsp; "
                f"🧠 **≈ {params_b:.0f}B parâmetros** *(estimado p/ quantização Q4)*"
            )
            if gb > 10:
                st.caption("⚠️ Acima de ~10 GB fica pesado nesta máquina (CPU ARM, 24 GB RAM compartilhada com os apps).")
        else:
            st.caption("📦 Tamanho não disponível no registro pra esta variante.")

    if modelo_final and st.button(f"⬇️ Baixar {modelo_final}", type="primary"):
        with st.spinner(f"Baixando {modelo_final} — modelos grandes levam minutos..."):
            rc, out = _run(["ollama", "pull", modelo_final], timeout=3600)
        (st.success if rc == 0 else st.error)((out or "Concluído.")[-500:])
        if rc == 0:
            time.sleep(1)
            st.rerun()


# ============================================================
# PAGINA: API da LLM (console de chaves, estilo Gemini)
# ============================================================

elif pagina == "🔑 API da LLM":
    on = gateway_online()
    c_tit, c_status, c_ex = st.columns([3.2, 1.6, 1.2], vertical_alignment="center")
    with c_tit:
        st.title("🔑 API da LLM")
    with c_status:
        st.markdown("🟢 **Gateway Online**" if on else "🔴 **Gateway Offline**")
    with c_ex:
        with st.popover("📋 Exemplos", use_container_width=True):
            st.caption("Endpoint OpenAI-compatible. Modelo = um dos instalados (aba Ollama).")
            st.code(
                f'''# Python (openai sdk)
from openai import OpenAI
client = OpenAI(base_url="{URL_BASE}/llm/v1", api_key="SUA_CHAVE")
r = client.chat.completions.create(
    model="qwen2.5:14b",
    messages=[{{"role": "user", "content": "Olá!"}}],
)
print(r.choices[0].message.content)''',
                language="python",
            )
            st.code(
                f'''curl {URL_BASE}/llm/v1/chat/completions \\
  -H "Authorization: Bearer SUA_CHAVE" \\
  -H "Content-Type: application/json" \\
  -d '{{"model":"qwen2.5:14b","messages":[{{"role":"user","content":"Oi"}}]}}' ''',
                language="bash",
            )

    st.code(f"{URL_BASE}/llm/v1", language="text")
    _mods = ", ".join(f"`{m.get('name','')}`" for m in ollama_modelos()) or "*nenhum modelo instalado*"
    st.caption(
        f"📡 Endereço da API — entregue base_url acima + uma chave ao cliente/projeto.  \n"
        f"🦙 **LLMs disponíveis neste servidor (Ollama):** {_mods} — cada chave é amarrada a uma delas."
    )
    if not on:
        st.warning(
            "O Gateway não respondeu — chaves não funcionarão. "
            "Rode: `sudo systemctl restart llmgateway` (setup: `llm_gateway/SETUP.md`)."
        )

    st.divider()
    c_t, c_b = st.columns([5, 1.5], vertical_alignment="center")
    with c_t:
        st.subheader("🗝️ Chaves cadastradas")
    with c_b:
        if st.button("➕ Criar chave", type="primary", use_container_width=True):
            st.session_state["form_key_aberto"] = not st.session_state.get("form_key_aberto", False)

    if st.session_state.get("form_key_aberto"):
        with st.container(border=True):
            instalados = [m.get("name", "") for m in ollama_modelos() if m.get("name")]
            with st.form("nova_key", clear_on_submit=True, border=False):
                c1, c2, c3 = st.columns([2.6, 1.8, 1], vertical_alignment="bottom")
                nome_key = c1.text_input("Nome / cliente (ex.: 'Sertanejo Lab', 'Cliente João')")
                modelo_key = c2.selectbox(
                    "🦙 LLM ativa da chave",
                    instalados or ["(nenhum modelo instalado)"],
                    help="A chave fica AMARRADA a este modelo: o gateway força ele em toda "
                         "requisição, mesmo que o cliente peça outro.",
                )
                criar = c3.form_submit_button("Gerar 🔑", type="primary", use_container_width=True)
            if criar and not instalados:
                st.error("Nenhum modelo instalado no Ollama — baixe um na aba 🦙 Ollama primeiro.")
            elif criar and nome_key.strip():
                keys = carregar_api_keys()
                nova = {
                    "id": f"key_{int(time.time())}",
                    "nome": nome_key.strip(),
                    "key": gerar_api_key(),
                    "modelo": modelo_key,
                    "criada_em": time.strftime("%Y-%m-%d %H:%M"),
                    "ativa": True,
                }
                keys.append(nova)
                if salvar_api_keys(keys):
                    st.session_state["chave_recem_criada"] = nova["key"]
                    st.session_state["form_key_aberto"] = False
                    st.rerun()
                else:
                    st.error("Falha ao salvar a chave.")
            elif criar:
                st.error("Dê um nome pra chave.")

    if st.session_state.get("chave_recem_criada"):
        st.success("Chave criada! **Copie AGORA** — ela também fica no 👁️ Ver, mas guarde em local seguro.")
        st.code(st.session_state["chave_recem_criada"], language="text")
        if st.button("✅ Copiei, pode esconder"):
            st.session_state.pop("chave_recem_criada", None)
            st.rerun()
    keys = carregar_api_keys()
    uso = carregar_uso_api()
    if not keys:
        st.caption("Nenhuma chave ainda. Crie a primeira acima.")
    for k in keys:
        kid = k["id"]
        u = uso.get(kid, {})
        ativa = k.get("ativa", True)
        with st.container(border=True):
            c1, c2, cp, c3, c4 = st.columns([3.5, 1.2, 0.5, 1.1, 1.1])
            estado = "🟢 Ativa" if ativa else "🔴 Revogada"
            c1.markdown(
                f"**{k.get('nome','—')}** · {estado} · 🦙 `{k.get('modelo', 'qualquer')}`  \n"
                f"`{k['key'][:14]}…{k['key'][-4:]}` · criada {k.get('criada_em','?')}"
            )
            c2.metric("Usos", u.get("usos", 0))
            if cp.button("⚡", key=f"ping_{kid}", use_container_width=True,
                         help="Ping — testa a chave de ponta a ponta (gateway → LLM)"):
                with st.spinner("Pingando a LLM (1º uso pode demorar — modelo carregando)..."):
                    ok_p, msg_p = ping_api_key(k["key"], k.get("modelo"))
                if ok_p:
                    st.success(f"⚡ Chave OK — `{k.get('modelo','?')}` respondeu: “{msg_p}”")
                else:
                    st.error(f"⚡ Falhou: {msg_p}")
            with c3.popover("👁️ Ver", use_container_width=True):
                st.markdown(f"**{k.get('nome','—')}** · 🦙 LLM: `{k.get('modelo', 'qualquer')}`")
                st.code(k["key"], language="text")
                st.caption(f"Último uso: {u.get('ultimo_uso', '—')} · criada {k.get('criada_em','?')}")
            if ativa:
                if c4.button("Revogar", key=f"rev_{kid}", use_container_width=True):
                    k["ativa"] = False
                    salvar_api_keys(keys)
                    st.rerun()
            else:
                if c4.button("Reativar", key=f"rea_{kid}", use_container_width=True):
                    k["ativa"] = True
                    salvar_api_keys(keys)
                    st.rerun()
            if not ativa:
                if st.button("🗑️ Excluir definitivamente", key=f"del_{kid}"):
                    salvar_api_keys([x for x in keys if x["id"] != kid])
                    st.rerun()


# ============================================================
# PAGINA: Acesso MCP (Claude)
# ============================================================

elif pagina == "🔌 Acesso MCP (Claude)":
    on = mcp_online()
    c_t, c_s, c_p = st.columns([4, 1.6, 0.5], vertical_alignment="center")
    with c_t:
        st.title("🔌 Acesso MCP")
    with c_s:
        st.markdown("🟢 **Servidor MCP Online**" if on else "🔴 **MCP Offline**")
    ping_mcp = c_p.button("⚡", key="ping_mcp", use_container_width=True,
                          help="Ping — rastreia o fluxo interno do MCP (serviço → porta → rota → mundo)")
    if ping_mcp:
        with st.spinner("Rastreando o fluxo do MCP..."):
            _passos = mcp_ping_fluxo()
        with st.container(border=True):
            st.markdown("**📋 Log do fluxo:**")
            for _ok, _txt in _passos:
                st.markdown(("✅ " if _ok else "❌ ") + _txt)
            if all(p[0] for p in _passos):
                st.success("⚡ Fluxo 100% — o Claude consegue chegar até aqui.")
            else:
                st.error("Fluxo interrompido na primeira etapa com ❌ — comece a investigar por ela.")
    st.caption(
        "Dê ao Claude (ou outro agente) acesso DIRETO e seguro ao seu servidor via MCP. "
        "Ele poderá ler/editar arquivos dos apps, reiniciar serviços e ver logs — sem você "
        "ficar fazendo scp na mão. **Nível 1 (Operador):** sem shell livre, sem root, só as pastas dos apps."
    )

    token = mcp_token_atual()
    st.divider()
    st.subheader("🔑 Token de acesso")
    if not token:
        st.warning("Nenhum token ativo. Gere um pra liberar a conexão.")
    else:
        c_msg, c_ver = st.columns([4.6, 1.2], vertical_alignment="center")
        c_msg.success("Token ativo — cole a URL no conector do Claude.")
        with c_ver.popover("👁️ Ver URL", use_container_width=True):
            st.code(f"{URL_BASE}/mcp-{token}/mcp", language="text")
            st.caption("⚠️ Quem tiver esta URL controla os apps do servidor. Trate como senha.")

    c1, c2 = st.columns(2)
    if c1.button("🔄 Gerar/Renovar token", type="primary", use_container_width=True):
        novo = mcp_gerar_token()
        if novo:
            st.session_state["mcp_novo"] = novo
            st.rerun()
        else:
            st.error("Falha ao gravar o token.")
    if token and c2.button("🗑️ Revogar acesso", use_container_width=True):
        try:
            MCP_TOKEN_PATH.unlink()
        except Exception:
            pass
        st.rerun()

    if st.session_state.get("mcp_novo"):
        st.info("Novo token gerado! URL de conexão (copie e cole no Claude):")
        st.code(f"{URL_BASE}/mcp-{st.session_state['mcp_novo']}/mcp", language="text")
        st.caption("⚠️ Renovar o token INVALIDA a URL antiga. Atualize no conector se já estava conectado.")
        if st.button("✅ Copiei"):
            st.session_state.pop("mcp_novo", None)
            st.rerun()

    st.divider()
    st.subheader("🧰 Ferramentas liberadas (Nível 1 — Operador)")
    st.markdown(
        "- 📂 **listar_pastas / ler_arquivo / escrever_arquivo** — só dentro das pastas dos apps (faz backup .bak)\n"
        "- 🚦 **servico** (status/restart/stop/start) e **logs** — só serviços da whitelist\n"
        "- 📊 **recursos** — CPU/RAM/disco/uptime\n"
        "- 🌿 **git** (status/pull/log/diff/fetch) — nas pastas dos apps\n\n"
        "🔒 *Sem shell livre, sem root, sem sair das pastas permitidas. Para subir de nível, é decisão consciente.*"
    )

    st.divider()
    with st.expander("📋 Como conectar no app do Claude"):
        st.markdown(
            "1. Gere o token acima e copie a **URL de conexão**.\n"
            "2. No app do Claude: **Configurações → Conectores → Adicionar conector personalizado**.\n"
            "3. Cole a URL. Pronto — nas conversas, o Claude ganha as ferramentas do seu VPS.\n\n"
            "Setup do serviço no servidor: `vps_mcp/SETUP.md`."
        )


# ============================================================
# PAGINA: Disco & Sistema
# ============================================================

elif pagina == "💾 Servidor & Limites":
    st.title("💾 Servidor & Limites (Always Free)")

    # ---- Identidade do Servidor (fonte única de verdade — estilo WordPress "Site URL") ----
    st.subheader("🌍 Identidade do Servidor")
    with st.container(border=True):
        _val = cert_validade(DOMINIO)
        c_id, c_mig = st.columns([4, 1.4], vertical_alignment="center")
        c_id.markdown(
            f"**Domínio:** [`{DOMINIO}`]({URL_BASE}) · **IP:** `{IP_PUBLICO}`  \n"
            f"🔒 **HTTPS Let's Encrypt** — "
            + (f"certificado válido até `{_val}`" if _val else "⚠️ não consegui ler o certificado")
            + " · renovação automática (certbot)  \n"
            f"🦆 DNS: **DuckDNS** (Google `diogobsbastos@gmail.com`) · "
            f"📄 Fonte única: `~/.vps_config.json` — mudou lá, o painel INTEIRO se adapta."
        )
        if c_mig.button("🔁 Migrar domínio", use_container_width=True,
                        help="Troca o domínio/HTTPS do servidor: grava a nova config e "
                             "gera o kit de comandos (certbot + Nginx)."):
            st.session_state["form_migrar"] = not st.session_state.get("form_migrar", False)

    # ---- Página inicial do domínio (pra onde a raiz / redireciona) ----
    KIT_ROTA_RAIZ = """sudo tee /usr/local/bin/vps_rota_raiz.sh > /dev/null <<'EOF'
#!/bin/bash
set -e
ROTA="$1"
echo "$ROTA" | grep -Eq '^/[a-zA-Z0-9/_-]*$' || { echo "rota invalida"; exit 1; }
CONF=/etc/nginx/sites-available/apps
if grep -q "location = /" "$CONF"; then
  sed -i "s|location = / { return 302 [^;]*; }|location = / { return 302 $ROTA; }|" "$CONF"
else
  sed -i "/listen 443 ssl/a\\    location = / { return 302 $ROTA; }" "$CONF"
fi
nginx -t && systemctl reload nginx
EOF
sudo chmod 755 /usr/local/bin/vps_rota_raiz.sh
echo 'ubuntu ALL=(ALL) NOPASSWD: /usr/local/bin/vps_rota_raiz.sh' | sudo tee /etc/sudoers.d/vpsadmin-rota > /dev/null
sudo chmod 440 /etc/sudoers.d/vpsadmin-rota"""
    with st.container(border=True):
        c_h, c_sel, c_ok = st.columns([2.7, 1.8, 1], vertical_alignment="bottom")
        c_h.markdown(
            "**🏠 Página inicial do domínio**  \n"
            f"Quem abre `{DOMINIO}/` cai em qual app?"
        )
        _rotas_disp = sorted(set(ROTAS_APPS.values()))
        _raiz_atual = _cfg.get("rota_raiz", "/escola-parque/")
        _idx_raiz = _rotas_disp.index(_raiz_atual) if _raiz_atual in _rotas_disp else 0
        rota_home = c_sel.selectbox("Rota padrão da raiz", _rotas_disp, index=_idx_raiz)
        if c_ok.button("Salvar 🏠", type="primary", use_container_width=True):
            rc_h, out_h = _run(["sudo", "-n", "/usr/local/bin/vps_rota_raiz.sh", rota_home],
                               timeout=30)
            if rc_h == 0:
                config_salvar("rota_raiz", rota_home)
                st.success(f"✅ Raiz `/` agora abre **{rota_home}** — testa: {URL_BASE}/")
            else:
                st.error("Helper não instalado ainda (ou falhou). Instala com o kit abaixo "
                         "(uma vez só, no SSH): " + (out_h or "")[:200])
                st.code(KIT_ROTA_RAIZ, language="bash")

    if st.session_state.get("form_migrar"):
        with st.container(border=True):
            with st.form("migrar_https", border=False):
                m1, m2, m3 = st.columns([2.4, 1.6, 1], vertical_alignment="bottom")
                novo_dom = m1.text_input("Novo domínio (DNS já apontando pro IP)", value=DOMINIO)
                novo_ip = m2.text_input("IP público", value=IP_PUBLICO)
                gerar_mig = m3.form_submit_button("Gerar kit 🔧", type="primary",
                                                  use_container_width=True)
            if gerar_mig and novo_dom.strip():
                _nd, _ni = novo_dom.strip(), novo_ip.strip()
                try:
                    CONFIG_PATH.write_text(json.dumps({"ip": _ni, "dominio": _nd}, indent=2))
                    st.success(f"`~/.vps_config.json` gravado → painel passa a usar "
                               f"**https://{_nd}** após o passo 3 do kit.")
                except Exception as e:  # noqa: BLE001
                    st.error(f"Falha ao gravar a config: {e}")
                st.markdown("**KIT DE MIGRAÇÃO — rode no terminal SSH (bloco único):**")
                st.code(
                    f"""# 1) Nginx atende pelo novo nome
sudo sed -i 's/server_name[^;]*;/server_name {_nd};/' /etc/nginx/sites-available/apps
sudo nginx -t && sudo systemctl reload nginx

# 2) Certificado HTTPS do novo dominio (renovacao automatica inclusa)
sudo certbot --nginx -d {_nd} --redirect -m diogobsbastos@gmail.com --agree-tos --no-eff-email

# 3) Painel rele a config
sudo systemctl restart vpsadmin

# 4) Teste
curl -s -o /dev/null -w "%{{http_code}}\\n" https://{_nd}/admin/""",
                    language="bash",
                )
                st.caption("⚠️ Depois da migração: atualizar a URL do conector MCP no Claude "
                           "e o base_url dos clientes da API da LLM (o domínio antigo para de valer).")

    st.divider()

    # ---- Specs da maquina ----
    st.subheader("🖥️ Especificações desta instância")
    disco = psutil.disk_usage("/") if psutil else None
    if psutil:
        n_ocpu = psutil.cpu_count(logical=True) or 4
        ram_total = psutil.virtual_memory().total / 1e9
        _, arch = _run(["uname", "-m"])
        s1, s2, s3, s4 = st.columns(4)
        s1.metric("OCPUs (vCPU)", n_ocpu)
        s2.metric("RAM total", f"{ram_total:.0f} GB")
        s3.metric("Disco /", f"{disco.total/1e9:.0f} GB")
        s4.metric("Arquitetura", (arch or "aarch64"))
        st.caption(
            f"Shape **VM.Standard.A1.Flex** (Ampere ARM) · Brazil East (São Paulo) · "
            f"IP `{IP_PUBLICO}` · conta **Always Free**"
        )

    st.divider()
    st.subheader("📊 Uso em tempo real")
    if psutil:
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("CPU", f"{cpu:.0f}%"); r1.progress(min(cpu/100, 1.0))
        r2.metric("RAM", f"{mem.percent:.0f}%", f"{mem.used/1e9:.1f}/{mem.total/1e9:.0f} GB")
        r2.progress(min(mem.percent/100, 1.0))
        r3.metric("Disco", f"{disco.percent:.0f}%", f"{disco.used/1e9:.1f}/{disco.total/1e9:.0f} GB")
        r3.progress(min(disco.percent/100, 1.0))
        try:
            carga = ", ".join(f"{x:.2f}" for x in psutil.getloadavg())
        except Exception:
            carga = "—"
        _, uptime = _run(["uptime", "-p"])
        r4.metric("Load 1/5/15m", carga); r4.caption(f"⏱️ {uptime}")

    st.divider()
    st.subheader("💰 Cota gratuita × cobrança")
    st.caption(
        "A Oracle cobra se você ULTRAPASSAR estes limites mensais. Consumo ESTIMADO desta "
        "instância 24/7 — pra você ver a folga ANTES de criar mais recursos."
    )
    st.caption("🟢 Folga · 🟡 No teto do gratuito (continua R$ 0) · 🔴 Ultrapassou (gera cobrança)")

    AJUDA_OCPU = (
        "Pense num plano pré-pago de CPU: você ganha 3.000 'horas-de-CPU' grátis por mês. "
        "Sua máquina tem 4 CPUs, então cada hora ligada gasta 4 horas do bolo. "
        "4 CPUs × ~720h do mês = ~2.880h. Está DENTRO do limite = R$ 0. "
        "Só cobraria se passasse de 3.000 (ex.: ligando uma 2ª máquina ARM 24/7)."
    )
    AJUDA_RAM = (
        "Mesma lógica, mas pra memória: 18.000 'GB-horas' grátis por mês. "
        "Seus 24 GB ligados o mês todo = ~17.300 GB-h. Dentro do limite = R$ 0. "
        "É o teto esperado de quem usa a máquina máxima do gratuito — está tudo certo."
    )

    import calendar as _cal
    from datetime import datetime as _dt
    _h = _cal.monthrange(_dt.utcnow().year, _dt.utcnow().month)[1] * 24
    n_ocpu = (psutil.cpu_count(logical=True) or 4) if psutil else 4
    ram_gb = round((psutil.virtual_memory().total / 1e9) if psutil else 24)
    ocpu_h, gb_h, LIM_O, LIM_G = n_ocpu * _h, ram_gb * _h, 3000, 18000
    egress_gb = (psutil.net_io_counters().bytes_sent / 1e9) if psutil else 0.0

    def _lim(nome, usado, limite, un, ajuda=None):
        pct_real = (usado / limite) if limite else 0
        if pct_real > 1.0:
            tag, cor = "🔴 Ultrapassou (cobrança)", "#fbeae7"
        elif pct_real >= 0.8:
            tag, cor = "🟡 No teto do gratuito (R$ 0)", "#fdf3df"
        else:
            tag, cor = "🟢 Folga", "#e6f4ec"
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 1.4, 1.6])
            c1.markdown(f"**{nome}**"); c1.progress(min(pct_real, 1.0))
            c2.metric("Usado (est.)", f"{usado:,.0f} {un}".replace(",", "."), help=ajuda)
            c3.markdown(
                f"<div style='background:{cor};border-radius:8px;padding:6px 10px;text-align:center;font-size:0.85em;'>"
                f"{tag}<br>{pct_real*100:.0f}% de {limite:,} {un}</div>".replace(",", "."),
                unsafe_allow_html=True,
            )

    _lim("Compute ARM — OCPU-horas/mês ❓", ocpu_h, LIM_O, "h", ajuda=AJUDA_OCPU)
    _lim("Compute ARM — GB-horas/mês (RAM) ❓", gb_h, LIM_G, "h", ajuda=AJUDA_RAM)
    _lim("Block Storage (disco usado)", (disco.used/1e9) if psutil else 47, 200, "GB")
    _lim("Tráfego de saída (desde o boot)", egress_gb, 10000, "GB")

    st.warning(
        f"⚠️ **Leitura crítica:** esta instância (4 OCPU / 24 GB, 24/7) já consome "
        f"~{ocpu_h/LIM_O*100:.0f}% da cota gratuita de **compute ARM** sozinha. "
        "Criar uma SEGUNDA instância ARM ligada o tempo todo **passa do limite e gera cobrança**. "
        "Instâncias desligadas não contam horas. Disco (200 GB) e tráfego (10 TB) têm muita folga."
    )

    st.divider()
    st.subheader("📁 Maiores pastas (home)")
    rc, out = _run(["bash", "-c", "du -sh /home/ubuntu/*/ 2>/dev/null | sort -rh | head -10"], timeout=60)
    st.code(out or "—")
    st.caption("Cota de disco: 200 GB no total (boot volume ~48 GB; resto disponível p/ expandir/anexar, grátis).")


# ============================================================
# PAGINA: Conta
# ============================================================

elif pagina == "👤 Conta":
    st.title("👤 Conta do administrador")
    usuario = carregar_usuario()
    col_dados, col_senha = st.columns(2)

    with col_dados:
        with st.container(border=True):
            st.markdown("**Dados cadastrados**")
            novo_nome = st.text_input("Nome", value=usuario.get("nome", ""))
            novo_email = st.text_input("E-mail (recuperação/contato)", value=usuario.get("email", ""))
            if st.button("💾 Salvar dados", use_container_width=True):
                if salvar_usuario({"nome": novo_nome.strip(), "email": novo_email.strip()}):
                    st.success("Dados salvos.")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("Falha ao salvar (permissões?).")

    with col_senha:
        with st.container(border=True):
            st.markdown("**Trocar senha do painel**")
            s_atual = st.text_input("Senha atual", type="password", key="pw_atual")
            s_nova = st.text_input("Nova senha (mín. 8 caracteres)", type="password", key="pw_nova")
            s_conf = st.text_input("Confirmar nova senha", type="password", key="pw_conf")
            if st.button("🔒 Trocar senha", type="primary", use_container_width=True):
                if not checar_senha(s_atual):
                    st.error("Senha atual incorreta.")
                elif len(s_nova) < 8:
                    st.error("A nova senha precisa ter pelo menos 8 caracteres.")
                elif s_nova != s_conf:
                    st.error("A confirmação não confere.")
                else:
                    try:
                        SENHA_PATH.write_text(s_nova)
                        SENHA_PATH.chmod(0o600)
                        st.success("Senha trocada! Use a nova no próximo login.")
                    except Exception as e:  # noqa: BLE001
                        st.error(f"Falha ao trocar senha: {e}")

st.sidebar.caption("VPS Admin v2.3-educado · base replicável p/ futuras VPS")
