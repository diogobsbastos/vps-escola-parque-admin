#!/usr/bin/env python3
"""
COLETOR DE MÉTRICAS — histórico leve do servidor 📈
=====================================================
Timer a cada 1 min (vpsmetricas). Grava 1 linha CSV em ~/.vps_metricas.csv
com CPU%, RAM%, disco%, load. Mantém ~7 dias (10080 linhas) e descarta o resto.
Leitura instantânea, zero dependência além de psutil (já usado pelo painel).
"""
import time
from pathlib import Path

ARQ = Path.home() / ".vps_metricas.csv"
MAX_LINHAS = 10080  # 7 dias * 1440 min


def main() -> None:
    try:
        import psutil
    except Exception:
        return
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory().percent
    disco = psutil.disk_usage("/").percent
    try:
        load1 = psutil.getloadavg()[0]
    except Exception:
        load1 = 0.0
    linha = f"{int(time.time())},{cpu:.1f},{mem:.1f},{disco:.1f},{load1:.2f}\n"

    try:
        antigas = ARQ.read_text().splitlines() if ARQ.exists() else []
    except Exception:
        antigas = []
    if not antigas or not antigas[0].startswith("ts,"):
        antigas = ["ts,cpu,ram,disco,load"] + antigas
    antigas.append(linha.rstrip())
    # cabeçalho + últimas MAX_LINHAS
    corpo = antigas[1:][-MAX_LINHAS:]
    ARQ.write_text("\n".join([antigas[0]] + corpo) + "\n")


if __name__ == "__main__":
    main()
