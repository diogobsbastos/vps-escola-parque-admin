"""
VPS AUTO-DEPLOY — o vigia do nosso "Vercel caseiro" (com MODO EDUCADO)
=======================================================================
Roda a cada 2 min (systemd timer vpsautodeploy.timer). Para cada projeto com
"auto": true em ~/.vps_git_projetos.json:
  - compara o HEAD do GitHub com a producao;
  - se diferente: git pull (modo pull) ou clone+espalha (modo mapa);
  - reinicia os servicos do projeto.

MODO EDUCADO: se o servico for o PROPRIO PAINEL (vpsadmin) e houver alguem
conectado nele (websocket ativo na porta 8500), o restart e ADIADO — os
arquivos ja ficam aplicados e o restart acontece sozinho quando a aba fechar
(ou apos 30 min, o que vier primeiro). Os demais servicos reiniciam na hora.

Log: journalctl -u vpsautodeploy
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

REG = Path.home() / ".vps_git_projetos.json"
STATE = Path.home() / ".vps_git_state.json"
CFG = Path.home() / ".vps_config.json"
PEND = Path.home() / ".vps_admin_restart_pendente"
HIST = Path.home() / ".vps_git_historico.json"
ADIAR_MAX = 1800  # 30 min: limite do adiamento do restart do painel


def hist_add(repo: str, commit: str, status: str) -> None:
    """Registra a tentativa de deploy (sucesso OU falha) no historico do painel."""
    try:
        hist = json.loads(HIST.read_text()) if HIST.exists() else []
    except Exception:
        hist = []
    hist.append({"repo": repo, "commit": commit,
                 "quando": time.strftime("%Y-%m-%d %H:%M"),
                 "origem": "auto (vigia/webhook)", "status": status})
    try:
        HIST.write_text(json.dumps(hist[-100:], ensure_ascii=False, indent=1))
    except Exception:
        pass


def run(cmd: list[str], t: int = 180) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=t)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def admin_em_uso() -> bool:
    """True se ha navegador conectado no painel (conexao na porta 8500)."""
    rc, out = run(["ss", "-Htn", "state", "established"], 10)
    return rc == 0 and ":8500" in out


def restart(servico: str) -> None:
    run(["sudo", "-n", "/usr/bin/systemctl", "restart", servico], 60)
    time.sleep(1)


def reiniciar_servicos(servicos: list[str]) -> None:
    for s in servicos:
        if s == "vpsadmin" and admin_em_uso():
            PEND.write_text(str(time.time()))
            print("vpsadmin: painel em uso -> restart ADIADO (modo educado)")
            continue
        restart(s)
        print(f"{s}: reiniciado")


def main() -> None:
    try:
        user = json.loads(CFG.read_text()).get("github_user", "diogobsbastos")
    except Exception:
        user = "diogobsbastos"
    try:
        projetos = json.loads(REG.read_text())
    except Exception:
        projetos = {}

    for repo, conf in projetos.items():
        if not conf.get("auto"):
            continue
        rc, out = run(["env", "GIT_TERMINAL_PROMPT=0", "git", "ls-remote",
                       f"https://github.com/{user}/{repo}.git", "HEAD"], 30)
        if rc != 0 or not out:
            print(f"{repo}: GitHub inacessivel")
            continue
        remoto = out.split()[0]

        if conf.get("pull"):
            pasta = conf["pull"]
            _, local = run(["git", "-C", pasta, "rev-parse", "HEAD"], 15)
            if local.strip() == remoto:
                continue
            rc2, out2 = run(["env", "GIT_TERMINAL_PROMPT=0", "git", "-C", pasta,
                             "pull", "--ff-only"])
            if rc2 != 0:
                print(f"{repo}: pull falhou: {out2[-200:]}")
                hist_add(repo, remoto[:10], "❌ pull falhou")
                continue
            if conf.get("build"):
                print(f"{repo}: build começou ({conf['build']})...")
                rc3, out3 = run(["bash", "-c", f"cd {pasta} && " + conf["build"]], 900)
                if rc3 != 0:
                    print(f"{repo}: BUILD falhou, NADA reiniciado: {out3[-200:]}")
                    hist_add(repo, remoto[:10],
                             "❌ build falhou — produção segue na versão anterior")
                    continue
        else:
            try:
                est_l = json.loads(STATE.read_text()).get(repo, {}).get("commit", "")
            except Exception:
                est_l = ""
            if est_l and remoto.startswith(est_l):
                continue
            tmp = f"/tmp/autodeploy-{repo}"
            shutil.rmtree(tmp, ignore_errors=True)
            rc2, out2 = run(["env", "GIT_TERMINAL_PROMPT=0", "git", "clone",
                             "--depth", "1", f"https://github.com/{user}/{repo}.git", tmp])
            if rc2 != 0:
                print(f"{repo}: clone falhou: {out2[-200:]}")
                hist_add(repo, remoto[:10], "❌ clone falhou")
                continue
            for origem, destino in conf.get("mapa", {}).items():
                src, dst = Path(tmp) / origem.rstrip("/"), Path(destino)
                if origem.endswith("/"):
                    for item in src.rglob("*"):
                        if item.is_file() and ".git" not in item.parts:
                            alvo = dst / item.relative_to(src)
                            alvo.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(item, alvo)
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
            shutil.rmtree(tmp, ignore_errors=True)

        try:
            est = json.loads(STATE.read_text()) if STATE.exists() else {}
        except Exception:
            est = {}
        est[repo] = {"commit": remoto[:10],
                     "quando": time.strftime("%Y-%m-%d %H:%M") + " (auto)"}
        STATE.write_text(json.dumps(est, indent=2))
        hist_add(repo, remoto[:10], "✅ ok")
        reiniciar_servicos(conf.get("servicos", []))
        print(f"{repo}: deploy automatico {remoto[:10]} OK")

    # restart pendente do painel (modo educado): executa quando ninguem esta usando
    if PEND.exists():
        try:
            idade = time.time() - float(PEND.read_text().strip() or 0)
        except Exception:
            idade = ADIAR_MAX + 1
        if not admin_em_uso() or idade > ADIAR_MAX:
            try:
                PEND.unlink()
            except Exception:
                pass
            restart("vpsadmin")
            print("vpsadmin: restart pendente executado (painel livre ou prazo esgotado)")
        else:
            print(f"vpsadmin: restart segue adiado ({int(idade)}s; painel em uso)")


if __name__ == "__main__":
    main()
