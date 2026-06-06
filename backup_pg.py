#!/usr/bin/env python3
"""
VPS BACKUP v3 — multi-perfis (a "rotina de backups" do framework)
====================================================================
Perfis em ~/.vps_backup.json (editados pela página 🐘 do painel):
  {"jobs": [{"id","nome","ativo","horario":"HH:MM","dias":[1-7],"bancos":[],
             "destino","manter_dias"}]}
bancos: lista de bancos do perfil — VAZIA = todos (inclusive futuros).
destino: pasta local ("/home/ubuntu/backups_pg") OU remote rclone ("gdrive:Pasta").
Timer roda A CADA MINUTO; cada perfil age exatamente no seu HH:MM.
Manual: backup_pg.py force [id_do_perfil]
Diário de execuções em ~/.vps_backup_log.jsonl (aba 🧾 Logs do painel).
Restaurar: gunzip -c ARQ.sql.gz | sudo -u postgres psql -d BANCO
"""
import gzip
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

HOME = Path.home()
CFG = HOME / ".vps_backup.json"
EST = HOME / ".vps_backup_estado.json"
LOGF = HOME / ".vps_backup_log.jsonl"


def log_evento(job_nome: str, resultado: str, modo: str) -> None:
    try:
        linhas = (LOGF.read_text().splitlines()[-499:]
                  if LOGF.exists() else [])
    except Exception:
        linhas = []
    linhas.append(json.dumps(
        {"quando": time.strftime("%Y-%m-%d %H:%M"), "job": job_nome,
         "resultado": resultado, "modo": modo}, ensure_ascii=False))
    LOGF.write_text("\n".join(linhas) + "\n")


SEGREDOS = [".innova_db.json", ".postgrest_jwt_secret", "postgrest.conf",
            ".vps_webhook_secret", ".vps_webhook_rota", ".vps_config.json",
            ".vps_git_projetos.json", ".vps_git_state.json",
            ".vps_git_historico.json", ".vps_backup.json"]


def carregar(p: Path, padrao):
    try:
        return json.loads(p.read_text())
    except Exception:
        return padrao


def main() -> None:
    jobs = carregar(CFG, {}).get("jobs", [])
    force = len(sys.argv) > 1 and sys.argv[1] == "force"
    alvo = sys.argv[2] if len(sys.argv) > 2 else ""
    agora_hm = time.strftime("%H:%M")
    dow = int(time.strftime("%u"))

    cred = carregar(HOME / ".innova_db.json", {})
    adm = cred.get("admin") or cred.get("worker") or {}
    if not adm:
        print("sem credenciais em ~/.innova_db.json")
        sys.exit(1)
    env = dict(os.environ, PGPASSWORD=adm.get("pass", ""))

    r = subprocess.run(
        ["psql", "-h", "127.0.0.1", "-U", adm["user"], "-d", "postgres", "-Atc",
         "select datname from pg_database where not datistemplate "
         "and datname <> 'postgres'"],
        capture_output=True, text=True, env=env, timeout=30)
    bancos = [b for b in r.stdout.split() if b]

    estado = carregar(EST, {})
    rodou = 0
    for job in jobs:
        if force:
            if alvo and job.get("id") != alvo:
                continue
        else:
            if not job.get("ativo"):
                continue
            alvo_hm = (job.get("horario")
                       or str(job.get("hora", "03")) + ":30")
            if alvo_hm != agora_hm or dow not in job.get("dias", []):
                continue
        rodou += 1
        sel = job.get("bancos") or []
        alvo_dbs = [d for d in bancos if not sel or d in sel]
        dest = str(job.get("destino", "")).strip()
        carimbo = time.strftime("%F_%H%M")
        stage = Path(f"/tmp/bkstage_{job.get('id', 'x')}")
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True)
        ok, msgs = True, []

        for db in alvo_dbs:
            p1 = subprocess.run(["pg_dump", "-h", "127.0.0.1", "-U", adm["user"],
                                 "-d", db], capture_output=True, env=env,
                                timeout=600)
            if p1.returncode != 0:
                ok = False
                msgs.append(f"{db} ERRO")
                continue
            f = stage / f"{db}_{carimbo}.sql.gz"
            with gzip.open(f, "wb") as gz:
                gz.write(p1.stdout)
            msgs.append(f"{db} {max(1, f.stat().st_size // 1024)}KB")

        with tarfile.open(stage / f"configs_{carimbo}.tgz", "w:gz") as t:
            for nome in SEGREDOS:
                p = HOME / nome
                if p.exists():
                    t.add(p, arcname=nome)

        manter = int(job.get("manter_dias", 7))
        if dest.startswith("/"):
            dd = Path(dest)
            dd.mkdir(parents=True, exist_ok=True)
            os.chmod(dd, 0o700)
            for f in stage.iterdir():
                shutil.copy2(f, dd / f.name)
            lim = time.time() - manter * 86400
            for f in dd.iterdir():
                if f.is_file() and f.stat().st_mtime < lim:
                    f.unlink()
            res = f"📁 ok → {dest}"
        elif ":" in dest:
            r2 = subprocess.run(["rclone", "copy", str(stage), dest],
                                capture_output=True, text=True, timeout=900)
            if r2.returncode == 0:
                subprocess.run(["rclone", "delete", dest, "--min-age",
                                f"{manter}d"], capture_output=True, timeout=300)
                res = f"☁️ ok → {dest}"
            else:
                ok = False
                res = "⚠️ rclone: " + (r2.stderr or "?")[-120:]
        else:
            ok = False
            res = "⚠️ destino inválido (use /pasta ou remote:pasta)"

        shutil.rmtree(stage, ignore_errors=True)
        estado[job.get("id", "?")] = {
            "quando": time.strftime("%Y-%m-%d %H:%M"),
            "resultado": ("✅ " if ok else "❌ ") + res + " · " + ", ".join(msgs)}
        log_evento(job.get("nome", job.get("id", "?")),
                   estado[job.get("id", "?")]["resultado"],
                   "manual" if force else "timer")
        print(f"{job.get('nome', job.get('id'))}: "
              f"{estado[job.get('id', '?')]['resultado']}")

    EST.write_text(json.dumps(estado, ensure_ascii=False, indent=1))
    if not rodou:
        print("nenhum perfil no horário (ou todos desligados)")


if __name__ == "__main__":
    main()
