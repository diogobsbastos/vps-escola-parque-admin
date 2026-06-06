# ============================================================
# provision_novo_app.py — página "➕ Novo App" (Pacote C)
# ============================================================
# Instalador 1-clique do painel: clona o repo, cria venv e
# chama o braço root auditado (/usr/local/bin/vps_provision,
# sudoers restrito) p/ systemd + rota Nginx com rollback.
# Também registra o app no painel, no vigia (auto-deploy) e
# cria o webhook do GitHub via API (token do servidor).
#
# Chamado pelo app.py:  provision_novo_app.render(URL_BASE)
# ============================================================
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import streamlit as st

HOME = Path.home()
APPS_PATH = HOME / ".vps_admin_apps.json"        # nome -> rótulo (aba Aplicativos)
ROTAS_PATH = HOME / ".vps_rotas_extras.json"     # nome -> rota (botão Acessar)
GIT_PROJ_PATH = HOME / ".vps_git_projetos.json"  # mapa do vigia (auto-deploy)
PLANO_PATH = HOME / ".vps_provision_plano.json"
PROVISION = "/usr/local/bin/vps_provision"

RX_NOME = re.compile(r"^[a-z][a-z0-9-]{2,29}$")
RX_REPO = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+?)(\.git)?/?$")


def _jread(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _jwrite(p: Path, d: dict) -> None:
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2))


def _run(cmd: list, timeout: int = 120, cwd=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, cwd=cwd)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)


def _provision(args: list, timeout: int = 120):
    return _run(["sudo", "-n", PROVISION] + args, timeout=timeout)


def _listar_gerenciados() -> dict:
    rc, out = _provision(["listar"], timeout=30)
    if rc != 0:
        return {}
    try:
        return json.loads(out)
    except Exception:
        return {}


# portas-base já usadas por apps fixos no range 85xx (evita sugerir colisão)
PORTAS_RESERVADAS = {8500, 8501, 8502}


def _sugerir_porta(gerenciados: dict) -> int:
    usadas = {a.get("porta") for a in gerenciados.values() if a.get("porta")}
    usadas |= PORTAS_RESERVADAS
    p = 8510
    while p in usadas and p < 8599:
        p += 1
    return p


def _token() -> str:
    try:
        return (HOME / ".github_token").read_text().strip()
    except Exception:
        return ""


def _clonar(repo: str, pasta: str):
    rc, out = _run(["git", "clone", repo, pasta], timeout=600)
    if rc == 0:
        return True, "clone ok"
    tok = _token()
    if not tok:
        return False, f"clone falhou e não há ~/.github_token: {out[-300:]}"
    com_tok = repo.replace("https://", f"https://x-access-token:{tok}@")
    rc, out = _run(["git", "clone", com_tok, pasta], timeout=600)
    if rc != 0:
        return False, f"clone falhou mesmo com token: {out[-300:]}"
    # remote limpo + credencial salva p/ pulls futuros do vigia
    _run(["git", "-C", pasta, "remote", "set-url", "origin", repo])
    cred = HOME / ".git-credentials"
    linha = f"https://x-access-token:{tok}@github.com"
    txt = cred.read_text() if cred.exists() else ""
    if linha not in txt:
        cred.write_text((txt + "\n" + linha).strip() + "\n")
        cred.chmod(0o600)
    _run(["git", "config", "--global", "credential.helper", "store"])
    return True, "clone ok (repo privado via token; credencial salva p/ o auto-deploy)"


def _criar_webhook(repo: str, url_base: str) -> str:
    m = RX_REPO.match(repo)
    tok = _token()
    try:
        rota_hook = (HOME / ".vps_webhook_rota").read_text().strip()
        segredo = (HOME / ".vps_webhook_secret").read_text().strip()
    except Exception:
        return "⚠️ webhook: sem ~/.vps_webhook_rota/secret — configure manual no GitHub"
    if not (m and tok):
        return "⚠️ webhook: sem ~/.github_token — configure manual no GitHub"
    api = f"https://api.github.com/repos/{m.group(1)}/{m.group(2)}/hooks"
    corpo = json.dumps({
        "config": {"url": f"{url_base}/{rota_hook}/",
                   "content_type": "json", "secret": segredo},
        "events": ["push"], "active": True,
    }).encode()
    req = urllib.request.Request(api, data=corpo, method="POST", headers={
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.loads(r.read())
        return f"🪝 webhook push→deploy criado no GitHub (id {d.get('id')})"
    except urllib.error.HTTPError as e:
        det = e.read().decode()[:200]
        if "already exists" in det:
            return "🪝 webhook já existia no repo — ok"
        return f"⚠️ webhook: HTTP {e.code} {det}"
    except Exception as e:
        return f"⚠️ webhook: {e}"


def _instalar(url_base: str, nome: str, rotulo: str, repo: str,
              pasta_in: str, principal: str, porta: int, rota_in: str) -> None:
    if not RX_NOME.match(nome):
        st.error("Nome inválido: minúsculas, números e '-', 3-30 chars, começa com letra.")
        return
    if repo and not RX_REPO.match(repo):
        st.error("Repo deve ser no formato https://github.com/usuario/repo")
        return
    pasta = (pasta_in or f"~/{nome}").replace("~", "/home/ubuntu")
    rota = rota_in.strip() or f"/{nome}"
    if not rota.startswith("/"):
        rota = "/" + rota
    rotulo = rotulo or f"🚀 {nome}"

    with st.status(f"Instalando **{nome}**…", expanded=True) as box:
        # 1/5 código
        if repo and not Path(pasta).exists():
            st.write("📥 1/5 Clonando o repo…")
            ok, msg = _clonar(repo, pasta)
            st.write(("✅ " if ok else "❌ ") + msg)
            if not ok:
                box.update(label="Falhou no clone", state="error")
                return
        elif Path(pasta).exists():
            st.write(f"📂 1/5 Pasta `{pasta}` já existe — usando como está.")
        else:
            st.write(f"❌ Pasta `{pasta}` não existe e nenhum repo foi informado.")
            box.update(label="Sem código p/ instalar", state="error")
            return

        # 2/5 venv
        venv = Path(pasta) / ".venv"
        if venv.exists():
            st.write("🐍 2/5 venv já existe — mantendo.")
        else:
            st.write("🐍 2/5 Criando venv…")
            rc, out = _run(["python3", "-m", "venv", str(venv)], timeout=180)
            if rc != 0:
                st.code(out[-400:])
                box.update(label="Falhou no venv", state="error")
                return

        # 3/5 dependências
        pip = str(venv / "bin" / "pip")
        req = Path(pasta) / "requirements.txt"
        if req.exists():
            st.write("📦 3/5 `pip install -r requirements.txt` (pode demorar uns minutos)…")
            rc, out = _run([pip, "install", "-r", str(req)], timeout=900)
            if rc != 0:
                st.code(out[-600:])
                box.update(label="Falhou no pip install", state="error")
                return
        else:
            st.write("📦 3/5 Sem requirements.txt — garantindo só o streamlit.")
        if not (venv / "bin" / "streamlit").exists():
            rc, out = _run([pip, "install", "streamlit"], timeout=600)
            if rc != 0:
                st.code(out[-400:])
                box.update(label="Falhou ao instalar streamlit", state="error")
                return

        # 4/5 braço root (atômico, com rollback)
        st.write("🔧 4/5 Provisionando systemd + rota Nginx (rollback automático)…")
        _jwrite(PLANO_PATH, {
            "nome": nome, "rotulo": rotulo, "template": "streamlit",
            "pasta": pasta, "principal": principal, "porta": porta, "rota": rota,
        })
        rc, out = _provision(["criar", str(PLANO_PATH)], timeout=180)
        st.code(out[-900:] or "(sem saída)")
        if rc != 0:
            box.update(label="Provisionador recusou — nada foi quebrado (rollback)",
                       state="error")
            return

        # 5/5 registros no painel + vigia + webhook GitHub
        st.write("📝 5/5 Registrando no painel e ligando o auto-deploy…")
        apps = _jread(APPS_PATH)
        apps[nome] = rotulo
        _jwrite(APPS_PATH, apps)
        rotas = _jread(ROTAS_PATH)
        rotas[nome] = rota + "/"
        _jwrite(ROTAS_PATH, rotas)
        if repo:
            chave = RX_REPO.match(repo).group(2)
            gp = _jread(GIT_PROJ_PATH)
            gp[chave] = {"rotulo": rotulo, "pull": pasta, "servicos": [nome]}
            _jwrite(GIT_PROJ_PATH, gp)
            st.write(_criar_webhook(repo, url_base))
        box.update(label=f"✅ {rotulo} instalado!", state="complete")

    st.success(f"🎉 No ar: {url_base}{rota}/ — push no repo já dispara deploy automático.")
    st.balloons()


def _secao_remover(url_base: str, ger: dict) -> None:
    st.divider()
    st.subheader("🗂️ Apps instalados por aqui")
    if not ger:
        st.caption("Nenhum app gerenciado pelo instalador ainda.")
        return
    st.caption("Remover desfaz serviço + rota Nginx. A pasta do app FICA no servidor.")
    for nome, info in sorted(ger.items()):
        cor = {"active": "🟢", "inactive": "⚪", "failed": "🔴"}.get(
            info.get("status"), "🟡")
        with st.container(border=True):
            c1, c2, c3 = st.columns([4.2, 1.6, 1.4], vertical_alignment="center")
            c1.markdown(
                f"**{cor} {info.get('rotulo', nome)}**  \n"
                f"`{nome}` · porta `{info.get('porta')}` · "
                f"[{info.get('rota')}/]({url_base}{info.get('rota')}/)"
            )
            confirma = c2.checkbox("confirmo remover", key=f"conf_rm_{nome}")
            if c3.button("🗑️ Remover", key=f"rm_{nome}",
                         use_container_width=True, disabled=not confirma):
                rc, out = _provision(["remover", nome], timeout=60)
                st.code(out[-400:])
                if rc == 0:
                    for p in (APPS_PATH, ROTAS_PATH):
                        d = _jread(p)
                        d.pop(nome, None)
                        _jwrite(p, d)
                    gp = _jread(GIT_PROJ_PATH)
                    for k in [k for k, v in gp.items()
                              if v.get("servicos") == [nome]]:
                        gp.pop(k)
                    _jwrite(GIT_PROJ_PATH, gp)
                    st.success(f"{nome} removido.")
                    time.sleep(1.5)
                    st.rerun()


def render(URL_BASE: str) -> None:
    st.title("➕ Novo App (instalador 1-clique)")
    st.caption(
        "Padrão da casa, agora automático: clona o repo, cria venv próprio, "
        "systemd + rota Nginx (com rollback) e liga push→deploy. A parte root "
        "passa pelo provisionador auditado (sudoers restrito, User=ubuntu sempre)."
    )

    if not Path(PROVISION).exists():
        st.error("Provisionador ainda não instalado no servidor — rode o kit "
                 "**INSTALAR_NOVO_APP.md** (1 colada no SSH) e recarregue a página.")
        return
    rc, _ = _provision(["listar"], timeout=15)
    if rc != 0:
        st.error("O sudoers do provisionador não está ativo — rode o kit "
                 "**INSTALAR_NOVO_APP.md** (passo do sudoers) e recarregue.")
        return

    ger = _listar_gerenciados()

    with st.form("novo_app_auto"):
        c1, c2 = st.columns(2)
        nome = c1.text_input("Nome do serviço", placeholder="meu-app (a-z, 0-9, -)")
        rotulo = c2.text_input("Rótulo no painel (com emoji!)", value="🚀 Meu App")
        repo = st.text_input(
            "Repo GitHub (deixe vazio se a pasta já está no servidor)",
            placeholder="https://github.com/diogobsbastos/meu-app",
        )
        c3, c4 = st.columns(2)
        pasta_in = c3.text_input("Pasta no servidor (vazio = ~/NOME)", value="")
        principal = c4.text_input("Arquivo principal", value="app.py")
        c5, c6 = st.columns(2)
        porta = c5.number_input("Porta interna", 8502, 8599, _sugerir_porta(ger))
        rota_in = c6.text_input("Rota (vazio = /NOME)", value="")
        st.selectbox("Template", ["Streamlit"],
                     help="Next.js e FastAPI entram no próximo corte do Pacote C")
        enviar = st.form_submit_button("🚀 Instalar agora", type="primary")

    if enviar:
        _instalar(URL_BASE, nome.strip(), rotulo.strip(), repo.strip(),
                  pasta_in.strip(), principal.strip(), int(porta), rota_in)

    _secao_remover(URL_BASE, ger)
