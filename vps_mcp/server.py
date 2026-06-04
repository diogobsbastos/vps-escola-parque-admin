"""
VPS-MCP — Servidor MCP (nível 1: Operador) do servidor Oracle
==============================================================
Expõe ferramentas seguras do VPS para um agente (Claude) plugar via conector MCP.
Roda em 127.0.0.1:8700, atrás do Nginx numa rota com SEGREDO na URL.

SEGURANÇA (nível 1 — Operador):
- Acesso só via URL secreta (token longo no path do Nginx) + token no header.
- Arquivos: leitura/escrita SOMENTE dentro das pastas dos apps (PASTAS_OK).
- Serviços: restart/status/logs APENAS da whitelist (SERVICOS_OK).
- SEM shell livre. SEM root. Caminhos com '..' são bloqueados.

Transporte: HTTP streamable (compatível com conectores MCP remotos).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ---------- Config de segurança ----------
TOKEN_PATH = Path.home() / ".vps_mcp_token"
PASTAS_OK = [
    Path("/home/ubuntu/escola-parque"),
    Path("/home/ubuntu/vps-admin"),
    Path("/home/ubuntu/llm-gateway"),
    Path("/home/ubuntu/sertanejo-lab"),
    Path("/home/ubuntu/innova-front"),
]
SERVICOS_OK = {
    "escolaparque", "escolaparque-worker", "vpsadmin",
    "nginx", "ollama", "llmgateway", "sertanejolab", "vpsmcp",
    "innovafront", "vpswebhook", "postgresql",
}
ACOES_OK = {"restart", "stop", "start", "status"}

mcp = FastMCP("vps-oracle", host="127.0.0.1", port=8700)


def _token_ok(recebido: str) -> bool:
    try:
        return recebido.strip() == TOKEN_PATH.read_text().strip() and recebido.strip() != ""
    except Exception:
        return False


def _caminho_seguro(caminho: str) -> Path | None:
    """Resolve o caminho e garante que está DENTRO de alguma pasta permitida."""
    try:
        p = Path(caminho).resolve()
    except Exception:
        return None
    if ".." in Path(caminho).parts:
        return None
    for base in PASTAS_OK:
        try:
            p.relative_to(base.resolve())
            return p
        except ValueError:
            continue
    return None


def _run(cmd: list[str], timeout: int = 60) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return f"erro: {e}"


# ==========================================================
# FERRAMENTAS
# ==========================================================

@mcp.tool()
def listar_pastas() -> str:
    """Lista as pastas de apps acessíveis e seus arquivos (nível 1)."""
    out = {}
    for base in PASTAS_OK:
        if base.exists():
            out[str(base)] = sorted(
                [f.name for f in base.iterdir() if not f.name.startswith(".")]
            )[:200]
    return json.dumps(out, ensure_ascii=False, indent=2)


@mcp.tool()
def ler_arquivo(caminho: str) -> str:
    """Lê um arquivo de texto DENTRO das pastas dos apps. Ex.: /home/ubuntu/vps-admin/app.py"""
    p = _caminho_seguro(caminho)
    if not p or not p.is_file():
        return "ERRO: caminho fora das pastas permitidas ou inexistente."
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:200000]
    except Exception as e:  # noqa: BLE001
        return f"erro ao ler: {e}"


@mcp.tool()
def escrever_arquivo(caminho: str, conteudo: str) -> str:
    """Escreve/substitui um arquivo DENTRO das pastas dos apps. Faz backup .bak antes."""
    p = _caminho_seguro(caminho)
    if not p:
        return "ERRO: caminho fora das pastas permitidas."
    try:
        if p.exists():
            bak = p.with_suffix(p.suffix + ".bak")
            bak.write_text(p.read_text(encoding="utf-8", errors="replace"))
        p.write_text(conteudo, encoding="utf-8")
        return f"OK: {len(conteudo)} chars gravados em {p} (backup em {p}.bak)."
    except Exception as e:  # noqa: BLE001
        return f"erro ao escrever: {e}"


@mcp.tool()
def servico(nome: str, acao: str) -> str:
    """Controla um serviço da whitelist. acao: status | restart | stop | start."""
    if nome not in SERVICOS_OK or acao not in ACOES_OK:
        return "ERRO: serviço ou ação fora da whitelist."
    if acao == "status":
        return _run(["systemctl", "is-active", nome]) + " | " + _run(
            ["systemctl", "status", nome, "--no-pager", "-n", "5"], timeout=20
        )
    return _run(["sudo", "-n", "/usr/bin/systemctl", acao, nome], timeout=60) or f"{acao} {nome}: ok"


@mcp.tool()
def logs(nome: str, linhas: int = 60) -> str:
    """Últimas linhas de log de um serviço da whitelist."""
    if nome not in SERVICOS_OK:
        return "ERRO: serviço fora da whitelist."
    return _run(["journalctl", "-u", nome, "-n", str(min(linhas, 300)),
                 "--no-pager", "-o", "short-iso"]) or "(sem logs)"


@mcp.tool()
def recursos() -> str:
    """CPU, RAM, disco e uptime do servidor."""
    return (
        "CPU/mem:\n" + _run(["bash", "-c", "top -bn1 | head -5"]) +
        "\n\nDisco:\n" + _run(["df", "-h", "/"]) +
        "\n\nUptime: " + _run(["uptime", "-p"])
    )


@mcp.tool()
def git(comando: str, pasta: str) -> str:
    """Executa um comando git SEGURO (status|pull|log|diff) numa pasta de app."""
    p = _caminho_seguro(pasta)
    if not p or not p.is_dir():
        return "ERRO: pasta fora das permitidas."
    permitidos = {"status", "pull", "log", "diff", "fetch"}
    sub = comando.split()[0] if comando else ""
    if sub not in permitidos:
        return f"ERRO: só {permitidos} são permitidos no nível 1."
    return _run(["git", "-C", str(p)] + comando.split(), timeout=120)


DB_CRED_PATH = Path.home() / ".innova_db.json"


@mcp.tool()
def sql_local(query: str, banco: str = "innova", como: str = "worker") -> str:
    """SQL no PostgreSQL LOCAL (banco interno do servidor — nosso 'Supabase caseiro').
    como: worker (service-role) | app (frontend). Aceita SELECT/INSERT/DDL."""
    try:
        cred = json.loads(DB_CRED_PATH.read_text())
    except Exception:
        return "ERRO: ~/.innova_db.json não existe (FASE 1 do banco não rodou?)."
    u = cred.get(como) or cred.get("worker") or {}
    env = dict(os.environ, PGPASSWORD=u.get("pass", ""))
    try:
        r = subprocess.run(
            ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-P", "pager=off",
             "-h", cred.get("host", "127.0.0.1"),
             "-p", str(cred.get("port", 5432)),
             "-U", u.get("user", ""), "-d", banco, "-c", query],
            capture_output=True, text=True, timeout=120, env=env)
        out = (r.stdout + r.stderr).strip()
        return out[:100000] or "(sem saída)"
    except Exception as e:  # noqa: BLE001
        return f"erro: {e}"


if __name__ == "__main__":
    # HTTP streamable na porta 8700 (host/port definidos no construtor acima).
    # Nginx faz o proxy + o segredo na URL.
    mcp.run(transport="streamable-http")
