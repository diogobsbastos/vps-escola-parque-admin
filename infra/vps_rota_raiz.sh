#!/bin/bash
# Define a pagina inicial do dominio (location = /) e recarrega o nginx.
# Instalar em /usr/local/bin (755) + sudoers NOPASSWD (ver INSTALL.md).
set -e
ROTA="$1"
echo "$ROTA" | grep -Eq '^/[a-zA-Z0-9/_-]*$' || { echo "rota invalida"; exit 1; }
CONF=/etc/nginx/sites-available/apps
if grep -q "location = /" "$CONF"; then
  sed -i "s|location = / { return 302 [^;]*; }|location = / { return 302 $ROTA; }|" "$CONF"
else
  sed -i "/listen 443 ssl/a\    location = / { return 302 $ROTA; }" "$CONF"
fi
nginx -t && systemctl reload nginx
