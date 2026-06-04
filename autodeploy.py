"""
VPS AUTO-DEPLOY — o vigia do nosso "Vercel caseiro"
====================================================
Roda a cada 2 min (systemd timer vpsautodeploy.timer). Para cada projeto com
"auto": true em ~/.vps_git_projetos.json:
  - compara o HEAD do GitHub com a producao;
  - se diferente: git pull (modo pull) ou clone+espalha (modo mapa);
  - reinicia os servicos do projeto (sudoers ja permite systemctl restart).
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


def run(cmd: list[str], t: int = 180) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=t)
        return p.returncode, (p.stdout + p.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return 1, str(e)


def main() -> None:
    try:
        user = json.loads(CFG.read_text()).get("github_user", "diogobsbastos")
    except Exception:
        user = "diogobsbastos"
    try:
        projetos = json.loads(REG.read_text())
    except Exception:
        print("sem registro de projetos; nada a fazer")
        return

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
                continue
        else:
            try:
                est_l = json.loads(STATE.read_text()).get(repo, {}).get("commit", "")
            except Exception:
                est_l = ""
            if remoto.startswith(est_l) and est_l:
                continue
            tmp = f"/tmp/autodeploy-{repo}"
            shutil.rmtree(tmp, ignore_errors=True)
            rc2, out2 = run(["env", "GIT_TERMINAL_PROMPT=0", "git", "clone",
                             "--depth", "1", f"https://github.com/{user}/{repo}.git", tmp])
            if rc2 != 0:
                print(f"{repo}: clone falhou: {out2[-200:]}")
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

        for s in conf.get("servicos", []):
            run(["sudo", "-n", "/usr/bin/systemctl", "restart", s], 60)
            time.sleep(1)
        print(f"{repo}: deploy automatico {remoto[:10]} OK")


if __name__ == "__main__":
    main()
