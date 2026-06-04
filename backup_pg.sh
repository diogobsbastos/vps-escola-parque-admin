#!/bin/bash
# ============================================================
# VPS BACKUP — bancos Postgres + configs, com rotacao GFS
# ============================================================
# Roda as 03:30 (timer vpsbackup) ou manual (painel/SSH).
# Guarda: 7 diarios + 4 semanais (domingo) + 6 mensais (dia 01).
# Inclui pacote de segredos/configs p/ desastre total (dir 700).
# Restaurar: gunzip -c ARQ.sql.gz | sudo -u postgres psql -d BANCO
set -e
DIR=/home/ubuntu/backups_pg
HOJE=$(date +%F)
mkdir -p "$DIR/diario" "$DIR/semanal" "$DIR/mensal"
chmod 700 "$DIR"

U=$(python3 -c "import json;print(json.load(open('/home/ubuntu/.innova_db.json'))['admin']['user'])")
export PGPASSWORD=$(python3 -c "import json;print(json.load(open('/home/ubuntu/.innova_db.json'))['admin']['pass'])")

# 1) um dump por banco (todos os bancos de usuario)
for db in $(psql -h 127.0.0.1 -U "$U" -d postgres -Atc \
  "select datname from pg_database where not datistemplate and datname <> 'postgres'"); do
  pg_dump -h 127.0.0.1 -U "$U" -d "$db" | gzip > "$DIR/diario/${db}_${HOJE}.sql.gz"
  echo "✓ $db -> ${db}_${HOJE}.sql.gz ($(du -h "$DIR/diario/${db}_${HOJE}.sql.gz" | cut -f1))"
done

# 2) pacote de configs/segredos (recuperacao de desastre)
tar czf "$DIR/diario/configs_${HOJE}.tgz" -C /home/ubuntu \
  .innova_db.json .postgrest_jwt_secret postgrest.conf \
  .vps_webhook_secret .vps_webhook_rota .vps_config.json \
  .vps_git_projetos.json .vps_git_state.json .vps_git_historico.json 2>/dev/null || true

# 3) promocoes GFS
[ "$(date +%u)" = "7" ] && cp -f "$DIR/diario/"*"_${HOJE}.sql.gz" "$DIR/semanal/" 2>/dev/null || true
[ "$(date +%d)" = "01" ] && cp -f "$DIR/diario/"*"_${HOJE}.sql.gz" "$DIR/mensal/" 2>/dev/null || true

# 4) rotacao
find "$DIR/diario"  -type f -mtime +7   -delete
find "$DIR/semanal" -type f -mtime +28  -delete
find "$DIR/mensal"  -type f -mtime +180 -delete

echo "backup ok $(date '+%F %T') · $(ls "$DIR/diario" | wc -l) arquivos no diario"
