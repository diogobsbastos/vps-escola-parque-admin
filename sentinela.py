#!/usr/bin/env python3
"""
VPS SENTINELA — monitora, avisa (multi-canal) e se cura 🔔
===========================================================
Timer a cada 2 min (vpssentinela). Checa: serviços caídos (auto-restart),
heartbeat do worker (restart), disco, certificado, backup atrasado, deploy ❌.
Canais (página 🔔): ntfy (próprio/oficial) · 🌐 Web Push (Innova) ·
💬 WhatsApp (Evolution) · ✈️ Telegram · 📧 E-mail.
Anti-spam 6h + aviso de resolvido. Diário: ~/.vps_alertas_log.jsonl.
Teste: sentinela.py teste  ·  1 canal: sentinela.py ping1 '<json>' 'msg'
"""
import json
import shutil
import socket
import ssl
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()
CFG = HOME / ".vps_alertas.json"
EST = HOME / ".vps_sentinela_estado.json"
LOGF = HOME / ".vps_alertas_log.jsonl"
REALERTA_S = 6 * 3600

PADRAO = {
    "ativo": True,
    "auto_restart": True,
    "canais": [],
    "servicos": ["escolaparque", "escolaparque-worker", "innovafront",
                  "sertanejolab", "vpsadmin", "nginx", "llmgateway",
                  "vpsmcp", "vpswebhook", "postgresql", "postgrest"],
    "heartbeat_min": 5,
    "disco_pct": 85,
    "cert_dias": 14,
    "backup_horas": 26,
}


def carregar(p: Path, padrao):
    try:
        return json.loads(p.read_text())
    except Exception:
        return dict(padrao) if isinstance(padrao, dict) else padrao


def run(cmd, t=30):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=t)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def enviar_canal(c: dict, msg: str) -> bool:
    tipo = c.get("tipo")
    try:
        if tipo == "ntfy":
            srv = (c.get("servidor") or "https://ntfy.sh").rstrip("/")
            req = urllib.request.Request(
                srv + "/" + c.get("topico", ""),
                data=msg.encode(),
                headers={"Title": "VPS escola-parque-v3",
                         "Priority": "high", "Tags": "rotating_light"})
            if c.get("usuario"):
                import base64
                tok = base64.b64encode(
                    f"{c.get('usuario')}:{c.get('senha', '')}".encode()
                ).decode()
                req.add_header("Authorization", "Basic " + tok)
            urllib.request.urlopen(req, timeout=15)
            return True
        if tipo == "webpush":
            srv = (c.get("servidor") or "").rstrip("/")
            dados = json.dumps({"secret": c.get("segredo", ""),
                                "title": "VPS escola-parque-v3",
                                "body": msg}).encode()
            req = urllib.request.Request(
                srv + "/api/push/send", data=dados,
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=20)
            return True
        if tipo == "whatsapp":
            srv = (c.get("servidor") or "").rstrip("/")
            dados = json.dumps({"number": str(c.get("numero", "")),
                                "text": msg}).encode()
            req = urllib.request.Request(
                f"{srv}/message/sendText/{c.get('instancia', 'sentinela')}",
                data=dados,
                headers={"Content-Type": "application/json",
                         "apikey": c.get("apikey", "")})
            urllib.request.urlopen(req, timeout=20)
            return True
        if tipo == "telegram":
            dados = json.dumps({"chat_id": str(c.get("chat", "")),
                                "text": msg}).encode()
            req = urllib.request.Request(
                f"https://api.telegram.org/bot{c.get('token', '')}/sendMessage",
                data=dados, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=15)
            return True
        if tipo == "email":
            import smtplib
            from email.mime.text import MIMEText
            m = MIMEText(msg)
            m["Subject"] = "🚨 VPS escola-parque-v3"
            m["From"] = c.get("usuario", "")
            m["To"] = c.get("para") or c.get("usuario", "")
            s = smtplib.SMTP(c.get("smtp_host", "smtp.gmail.com"),
                             int(c.get("smtp_porta", 587)), timeout=20)
            s.starttls()
            s.login(c.get("usuario", ""), c.get("senha_app", ""))
            s.send_message(m)
            s.quit()
            return True
    except Exception:
        return False
    return False


def notificar(cfg: dict, msg: str) -> bool:
    ok = False
    for c in cfg.get("canais", []):
        if c.get("ativo", True):
            ok = enviar_canal(c, msg) or ok
    return ok


def registrar(msg: str, enviado: bool) -> None:
    try:
        linhas = LOGF.read_text().splitlines()[-499:] if LOGF.exists() else []
    except Exception:
        linhas = []
    linhas.append(json.dumps({"quando": time.strftime("%Y-%m-%d %H:%M"),
                              "msg": msg, "enviado": enviado},
                             ensure_ascii=False))
    LOGF.write_text("\n".join(linhas) + "\n")


def alertar(cfg, estado, chave: str, msg: str) -> None:
    agora = time.time()
    ultimo = estado.get(chave, {}).get("ultimo_alerta", 0)
    if agora - ultimo < REALERTA_S:
        estado[chave] = {"ultimo_alerta": ultimo, "ativo": True}
        return
    enviado = notificar(cfg, f"🚨 VPS escola-parque-v3\n{msg}")
    registrar(msg, enviado)
    estado[chave] = {"ultimo_alerta": agora, "ativo": True}


def resolver(cfg, estado, chave: str, msg_ok: str) -> None:
    if estado.get(chave, {}).get("ativo"):
        enviado = notificar(cfg, f"✅ VPS escola-parque-v3\n{msg_ok}")
        registrar(msg_ok, enviado)
    estado[chave] = {"ultimo_alerta": 0, "ativo": False}


def main() -> None:
    cfg = {**PADRAO, **carregar(CFG, {})}
    if not cfg.get("ativo"):
        return
    estado = carregar(EST, {})

    for svc in cfg.get("servicos", []):
        _, st = run(["systemctl", "is-active", svc], 10)
        if st.strip() in ("active", "activating"):
            resolver(cfg, estado, f"svc_{svc}", f"{svc}: voltou a rodar")
            continue
        det = f"{svc}: {st.strip() or 'parado'}"
        if cfg.get("auto_restart"):
            run(["sudo", "-n", "/usr/bin/systemctl", "restart", svc], 60)
            time.sleep(2)
            _, st2 = run(["systemctl", "is-active", svc], 10)
            if st2.strip() == "active":
                alertar(cfg, estado, f"svc_{svc}",
                        f"⚠️ {det} — REINICIEI sozinho e voltou 🟢")
                estado[f"svc_{svc}"]["ativo"] = False
                continue
            det += " — tentei reiniciar e NÃO voltou"
        alertar(cfg, estado, f"svc_{svc}", f"🔴 {det}")

    try:
        cred = json.loads((HOME / ".innova_db.json").read_text())
        adm = cred.get("admin") or cred.get("worker")
        import os
        env = dict(os.environ, PGPASSWORD=adm["pass"])
        r = subprocess.run(
            ["psql", "-h", "127.0.0.1", "-U", adm["user"], "-d", "innova",
             "-Atc", "select value->>'ts' from system_settings "
                     "where key='python_worker_heartbeat'"],
            capture_output=True, text=True, timeout=15, env=env)
        ts = r.stdout.strip()
        if ts:
            idade_min = (datetime.now(timezone.utc)
                         - datetime.fromisoformat(ts)).total_seconds() / 60
            if idade_min > cfg.get("heartbeat_min", 5):
                run(["sudo", "-n", "/usr/bin/systemctl", "restart",
                     "escolaparque-worker"], 60)
                alertar(cfg, estado, "heartbeat",
                        f"💔 Worker sem batimento há {idade_min:.0f} min "
                        f"(processo 'vivo' mas travado) — REINICIEI o worker")
            else:
                resolver(cfg, estado, "heartbeat",
                         "Worker batendo normal de novo")
    except Exception:
        pass

    uso = shutil.disk_usage("/")
    pct = uso.used * 100 // uso.total
    if pct >= cfg.get("disco_pct", 85):
        alertar(cfg, estado, "disco",
                f"💽 Disco em {pct}% ({uso.free // 2**30} GB livres)")
    else:
        resolver(cfg, estado, "disco", f"Disco normalizado ({pct}%)")

    try:
        dominio = json.loads((HOME / ".vps_config.json").read_text()
                             ).get("dominio", "")
        if dominio:
            ctx = ssl.create_default_context()
            with ctx.wrap_socket(socket.create_connection((dominio, 443), 10),
                                 server_hostname=dominio) as s:
                exp = datetime.strptime(s.getpeercert()["notAfter"],
                                        "%b %d %H:%M:%S %Y %Z")
            dias = (exp - datetime.utcnow()).days
            if dias <= cfg.get("cert_dias", 14):
                alertar(cfg, estado, "cert",
                        f"🔒 Certificado de {dominio} vence em {dias} dias!")
            else:
                resolver(cfg, estado, "cert", "Certificado renovado")
    except Exception:
        pass

    try:
        jobs = json.loads((HOME / ".vps_backup.json").read_text()
                          ).get("jobs", [])
        if any(j.get("ativo") for j in jobs):
            ultimo_ok = 0.0
            for ln in (HOME / ".vps_backup_log.jsonl"
                       ).read_text().splitlines():
                ev = json.loads(ln)
                if str(ev.get("resultado", "")).startswith("✅"):
                    ultimo_ok = max(ultimo_ok, time.mktime(
                        time.strptime(ev["quando"], "%Y-%m-%d %H:%M")))
            horas = (time.time() - ultimo_ok) / 3600 if ultimo_ok else 999
            if horas > cfg.get("backup_horas", 26):
                alertar(cfg, estado, "backup",
                        f"💾 Nenhum backup ✅ há {horas:.0f}h — conferir!")
            else:
                resolver(cfg, estado, "backup", "Backups em dia de novo")
    except Exception:
        pass

    try:
        hist = json.loads((HOME / ".vps_git_historico.json").read_text())
        vistos = set(estado.get("deploys_vistos", []))
        for h in hist:
            chave_h = f"{h.get('quando')}|{h.get('repo')}"
            if (str(h.get("status", "")).startswith("❌")
                    and chave_h not in vistos):
                alertar(cfg, estado, f"dep_{chave_h}",
                        f"🔨 Deploy FALHOU: {h.get('repo')} "
                        f"{h.get('commit', '')} — produção segue na anterior")
        estado["deploys_vistos"] = [f"{h.get('quando')}|{h.get('repo')}"
                                    for h in hist][-200:]
    except Exception:
        pass

    EST.write_text(json.dumps(estado, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "ping1":
        try:
            _canal = json.loads(sys.argv[2])
            _ok1 = enviar_canal(_canal, sys.argv[3] if len(sys.argv) > 3
                                else "Ping de teste")
            print("✅ enviado" if _ok1 else "❌ falhou (credenciais?)")
        except Exception as _e1:  # noqa: BLE001
            print(f"❌ erro: {_e1}")
    elif len(sys.argv) > 1 and sys.argv[1] == "teste":
        cfg_t = {**PADRAO, **carregar(CFG, {})}
        canais = [c for c in cfg_t.get("canais", []) if c.get("ativo", True)]
        if not canais:
            print("nenhum canal ligado — cadastre na página 🔔 do painel")
        for c in canais:
            ok = enviar_canal(c, "👋 Teste da Sentinela do VPS — canal "
                                 f"{c.get('nome', c.get('tipo'))} funcionando!")
            print(f"{c.get('nome', c.get('tipo'))}: "
                  + ("✅ enviado" if ok else "❌ falhou (credenciais?)"))
    else:
        main()
