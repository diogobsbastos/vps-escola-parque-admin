"""
LLM GATEWAY — Porteiro com API Key na frente do Ollama
=======================================================
Expoe o Ollama local de forma SEGURA: exige Authorization: Bearer <key>.
Roda em 127.0.0.1:8600, atras do Nginx na rota /llm/.

Fluxo:
  internet → Nginx (/llm/) → este gateway (valida key) → Ollama (localhost:11434)

Arquivos compartilhados com o painel VPS Admin:
  ~/.vps_admin_api_keys.json   -> chaves (o PAINEL escreve; o gateway so le)
  ~/.vps_admin_api_usage.json  -> contagem de uso (o GATEWAY escreve; painel le)

Endpoint OpenAI-compatible: http://IP/llm/v1/chat/completions
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

KEYS_PATH = Path.home() / ".vps_admin_api_keys.json"
USAGE_PATH = Path.home() / ".vps_admin_api_usage.json"
CONFIG_PATH = Path.home() / ".vps_config.json"   # fonte unica (painel escreve)
OLLAMA = "http://localhost:11434"


def keep_24h_ligado() -> bool:
    """Opcao '24h na RAM' do painel (pagina Ollama). Lido a cada request."""
    try:
        return bool(json.loads(CONFIG_PATH.read_text()).get("ollama_24h"))
    except Exception:
        return False

app = FastAPI(title="LLM Gateway", docs_url=None, redoc_url=None)


def chaves_ativas() -> dict[str, dict]:
    """Mapa {key_secreta: {id, modelo}} apenas das chaves ATIVAS (lido a cada request)."""
    try:
        data = json.loads(KEYS_PATH.read_text())
        return {
            k["key"]: {"id": k["id"], "modelo": k.get("modelo")}
            for k in data.get("keys", []) if k.get("ativa")
        }
    except Exception:
        return {}


def registrar_uso(kid: str) -> None:
    """Incrementa contador de uso da chave (gateway e o unico que escreve aqui)."""
    try:
        u = json.loads(USAGE_PATH.read_text()) if USAGE_PATH.exists() else {}
    except Exception:
        u = {}
    cell = u.get(kid, {"usos": 0})
    cell["usos"] = int(cell.get("usos", 0)) + 1
    cell["ultimo_uso"] = time.strftime("%Y-%m-%d %H:%M:%S")
    u[kid] = cell
    try:
        USAGE_PATH.write_text(json.dumps(u))
    except Exception:
        pass


@app.get("/health")
async def health():
    return {"ok": True, "service": "llm-gateway", "ollama": OLLAMA}


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def proxy(path: str, request: Request):
    # 1) valida a API key
    auth = request.headers.get("authorization", "")
    token = auth.replace("Bearer", "").replace("bearer", "").strip()
    ativas = chaves_ativas()
    if not token or token not in ativas:
        raise HTTPException(status_code=401, detail="API key invalida, ausente ou inativa.")
    info = ativas[token]
    registrar_uso(info["id"])

    # 2) ajustes no body: trava de modelo da chave + opcao '24h na RAM' do painel.
    body = await request.body()
    modelo_chave = info.get("modelo")
    if body:
        try:
            data = json.loads(body)
            if isinstance(data, dict) and "model" in data:
                mudou = False
                if modelo_chave:
                    data["model"] = modelo_chave   # a chave so fala com a LLM dela
                    mudou = True
                if keep_24h_ligado() and "keep_alive" not in data:
                    data["keep_alive"] = "24h"     # renova a residencia na RAM
                    mudou = True
                if mudou:
                    body = json.dumps(data).encode()
        except Exception:
            pass  # body nao-JSON: repassa como veio

    # 3) repassa pro Ollama (com streaming, pra respostas token-a-token)
    url = f"{OLLAMA}/{path}"
    client = httpx.AsyncClient(timeout=None)
    fwd_headers = {"content-type": request.headers.get("content-type", "application/json")}
    try:
        req = client.build_request(
            request.method, url, content=body,
            params=dict(request.query_params), headers=fwd_headers,
        )
        r = await client.send(req, stream=True)
    except Exception as e:  # noqa: BLE001
        await client.aclose()
        return JSONResponse({"error": f"falha ao falar com o Ollama: {e}"}, status_code=502)

    async def _close():
        await r.aclose()
        await client.aclose()

    return StreamingResponse(
        r.aiter_raw(),
        status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/json"),
        background=BackgroundTask(_close),
    )
