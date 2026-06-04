"""
VPS WEBHOOK - push->deploy em ~5s (estilo Vercel)
==================================================
Receptor minimalista (stdlib pura, ZERO dependencias) na porta interna 8800,
atras do Nginx (rota secreta /hook-XXXX/). Valida X-Hub-Signature-256 (HMAC
do GitHub) e dispara o vigia: sudo -n systemctl start vpsautodeploy.service.
O polling do timer continua como rede de seguranca.

Segredo HMAC: ~/.vps_webhook_secret (gerado no servidor, nunca em chat/repo).
Rota secreta: ~/.vps_webhook_rota (so informativo; quem usa e o Nginx).
Logs: journalctl -u vpswebhook -f
"""
from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORTA = 8800
SECRET_FILE = Path.home() / ".vps_webhook_secret"
MAX_BODY = 1_000_000  # 1 MB (payload de push do GitHub tem poucos KB)


def secret() -> bytes:
    try:
        return SECRET_FILE.read_text().strip().encode()
    except Exception:
        return b""


class Hook(BaseHTTPRequestHandler):
    server_version = "vpswebhook/1.0"

    def _resp(self, code: int, msg: str) -> None:
        corpo = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, fmt: str, *args) -> None:  # log limpo no journal
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def do_GET(self) -> None:  # teste de vida (curl na rota)
        self._resp(200, "vpswebhook vivo")

    def do_POST(self) -> None:
        tam = int(self.headers.get("Content-Length") or 0)
        if not 0 < tam <= MAX_BODY:
            self._resp(400, "payload invalido")
            return
        corpo = self.rfile.read(tam)

        chave = secret()
        if not chave:
            self._resp(500, "segredo nao configurado (~/.vps_webhook_secret)")
            return
        assinatura = self.headers.get("X-Hub-Signature-256", "")
        esperada = "sha256=" + hmac.new(chave, corpo, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(esperada, assinatura):
            print("ASSINATURA INVALIDA - request descartado", flush=True)
            self._resp(401, "assinatura invalida")
            return

        evento = self.headers.get("X-GitHub-Event", "?")
        if evento == "ping":
            self._resp(200, "pong")
            return
        if evento != "push":
            self._resp(200, f"evento '{evento}' ignorado")
            return

        try:
            p = json.loads(corpo)
            repo = p.get("repository", {}).get("name", "?")
            ref = p.get("ref", "?")
            commit = (p.get("after") or "")[:10]
        except Exception:
            repo = ref = commit = "?"
        print(f"PUSH {repo} {ref} {commit} -> disparando vigia", flush=True)

        # --no-block: responde ao GitHub na hora; o deploy roda em paralelo.
        # sudoers ja permite (NOPASSWD: /usr/bin/systemctl start *).
        r = subprocess.run(
            ["sudo", "-n", "/usr/bin/systemctl", "start", "--no-block",
             "vpsautodeploy.service"],
            capture_output=True, text=True, timeout=15)
        if r.returncode != 0:
            print(f"ERRO ao disparar vigia: {(r.stdout + r.stderr).strip()}",
                  flush=True)
            self._resp(500, "falha ao disparar deploy")
            return
        self._resp(200, f"deploy disparado: {repo}@{commit}")


if __name__ == "__main__":
    print(f"vpswebhook ouvindo em 127.0.0.1:{PORTA}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORTA), Hook).serve_forever()
