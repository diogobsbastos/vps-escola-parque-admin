#!/bin/bash
# ============================================================
# VPS BACKUP v2 — inteligente (liga/desliga, frequencia, destino externo)
# ============================================================
# Timer roda TODA HORA (:30). Este script le ~/.vps_backup.json (editado
# pelo painel) e decide se age. Manual: backup_pg.sh force
# GFS: 7 diarios + 4 semanais (dom) + 6 mensais (dia 01).
# Destino externo: rclone (ex.: gdrive:BackupsVPS) — sql sai do servidor.
# Restaurar: gunzip -c ARQ.sql.gz | sudo -u postgres psql -d BANCO
set -e
CFG=/home/ubuntu/.vps_backup.json
DIR=/home/ubuntu/backups_pg
LAST=/home/ubuntu/.vps_backup_last.json

cfg() { python3 -c "
import json, os
d = json.load(open('$CFG')) if os.path.exists('$CFG') else {}
print(d.get('$1', '$2'))"; }

ATIVO=$(cfg ativo true)
FREQ=$(cfg frequencia diario)
DEST=$(cfg destino "")
HORA=$(date +%H)

if [ "$1" != "force" ]; then
  case "$ATIVO" in True|true) ;; *) exit 0 ;; esac
  case "$FREQ" in
    diario) [ "$HORA" = "03" ] || exit 0 ;;
    12h)    case "$HORA" in 03|15) ;; *) exit 0 ;; esac ;;
    6h)     case "$HORA" in 03|09|15|21) ;; *) exit 0 ;; esac ;;
    1h)     ;;
    *)      [ "$HORA" = "03" ] || exit 0 ;;
  esac
fi

CARIMBO=$(date +%F_%H%M)
HOJE=$(date +%F)
mkdir -p "$DIR/diario" "$DIR/semanal" "$DIR/mensal"
chmod 700 "$DIR"

U=$(python3 -c "import json;print(json.load(open('/home/ubuntu/.innova_db.json'))['admin']['user'])")
export PGPASSWORD=$(python3 -c "import json;print(json.load(open('/home/ubuntu/.innova_db.json'))['admin']['pass'])")

for db in $(psql -h 127.0.0.1 -U "$U" -d postgres -Atc \
  "select datname from pg_database where not datistemplate and datname <> 'postgres'"); do
  pg_dump -h 127.0.0.1 -U "$U" -d "$db" | gzip > "$DIR/diario/${db}_${CARIMBO}.sql.gz"
  echo "✓ $db ($(du -h "$DIR/diario/${db}_${CARIMBO}.sql.gz" | cut -f1))"
done

tar czf "$DIR/diario/configs_${CARIMBO}.tgz" -C /home/ubuntu \
  .innova_db.json .postgrest_jwt_secret postgrest.conf \
  .vps_webhook_secret .vps_webhook_rota .vps_config.json \
  .vps_git_projetos.json .vps_git_state.json .vps_git_historico.json \
  .vps_backup.json 2>/dev/null || true

[ "$(date +%u)" = "7" ] && cp -f "$DIR/diario/"*"_${HOJE}"*.sql.gz "$DIR/semanal/" 2>/dev/null || true
[ "$(date +%d)" = "01" ] && cp -f "$DIR/diario/"*"_${HOJE}"*.sql.gz "$DIR/mensal/" 2>/dev/null || true
find "$DIR/diario"  -type f -mtime +7   -delete
find "$DIR/semanal" -type f -mtime +28  -delete
find "$DIR/mensal"  -type f -mtime +180 -delete

EXT="local apenas"
if [ -n "$DEST" ]; then
  if command -v rclone >/dev/null && rclone listremotes 2>/dev/null | grep -q "^${DEST%%:*}:"; then
    if rclone copy "$DIR/diario" "$DEST/diario" --max-age 24h >/dev/null 2>&1; then
      EXT="☁️ enviado p/ $DEST"
    else
      EXT="⚠️ ERRO no envio p/ $DEST"
    fi
  else
    EXT="⚠️ destino $DEST sem remote no rclone (rode: rclone config)"
  fi
fi

python3 - <<PY
import json, time
json.dump({"quando": time.strftime("%Y-%m-%d %H:%M"), "ok": True,
           "externo": "$EXT"}, open("$LAST", "w"))
PY
echo "backup ok $(date '+%F %T') · externo: $EXT"
