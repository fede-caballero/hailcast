#!/bin/bash
# HEALTHCHECK de Docker: sano solo si la API responde Y el worker escribió
# status.json hace menos de STATUS_MAX_AGE_SECONDS. Con `docker ps` se ve
# (healthy)/(unhealthy) y sirve para monitoreo externo.
STATUS_FILE="${STATUS_FILE_PATH:-/app/data/status.json}"
MAX_AGE="${STATUS_MAX_AGE_SECONDS:-900}"

curl -s -m 5 -f -o /dev/null http://127.0.0.1:8000/api/story/status || { echo "API no responde"; exit 1; }
[ -f "$STATUS_FILE" ] || { echo "status.json no existe"; exit 1; }
age=$(( $(date +%s) - $(stat -c %Y "$STATUS_FILE") ))
[ "$age" -le "$MAX_AGE" ] || { echo "status.json viejo (${age}s)"; exit 1; }
echo "ok (status hace ${age}s)"
