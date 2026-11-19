#!/bin/bash
# =====================================================================
# Watchdog de HailCast.
# - Si /app/data/status.json no se actualiza en STATUS_MAX_AGE_SECONDS,
#   el worker está colgado (el loop lo escribe cada pocos segundos, salvo
#   durante un ciclo de inferencia): se reinicia vía supervisorctl.
# - Si la API deja de responder en localhost:8000, avisa.
# - Avisos por Telegram si TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID están
#   seteados (opcional; sin ellos solo loguea).
# =====================================================================
STATUS_FILE="${STATUS_FILE_PATH:-/app/data/status.json}"
MAX_AGE="${STATUS_MAX_AGE_SECONDS:-900}"
CHECK_EVERY="${WATCHDOG_INTERVAL_SECONDS:-60}"
API_URL="http://127.0.0.1:8000/api/story/status"
DISK_ALERT_PCT="${DISK_ALERT_PCT:-85}"   # avisa cuando /app/data supera este uso
MEM_ALERT_MB="${MEM_ALERT_MB:-600}"       # avisa cuando al HOST le queda menos memoria disponible que esto
STARTUP_GRACE=180   # tiempo para que el worker cargue el modelo al arrancar

log() { echo "$(date -u +'%Y-%m-%dT%H:%M:%SZ') $*"; }

tg_send() {
    if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
        curl -s -m 10 -o /dev/null -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="$TELEGRAM_CHAT_ID" --data-urlencode text="🌩️ HailCast VPS — $*" || true
    fi
}

notify() {
    log "ALERTA: $*"
    tg_send "$*"
}

status_age() {
    if [ ! -f "$STATUS_FILE" ]; then echo 999999; return; fi
    echo $(( $(date +%s) - $(stat -c %Y "$STATUS_FILE") ))
}

log "Watchdog iniciado (umbral status.json: ${MAX_AGE}s, chequeo cada ${CHECK_EVERY}s)"
if [ -n "$TELEGRAM_BOT_TOKEN" ] && [ -n "$TELEGRAM_CHAT_ID" ]; then
    # Ping de arranque: confirma que Telegram está configurado y hace visible cada reinicio del contenedor
    tg_send "Contenedor iniciado. Disco al $(df --output=pcent /app/data 2>/dev/null | tail -1 | tr -d ' ')."
else
    log "Telegram no configurado (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID): las alertas solo van al log."
fi
sleep "$STARTUP_GRACE"

last_worker_restart=0
api_failures=0
api_was_down=0
disk_alerted=0
mem_alerted=0

# Memoria: /proc/meminfo es la del host (no del cgroup); el uso del contenedor sale del cgroup.
host_mem_available_mb() { awk '/MemAvailable/ {printf "%d", $2/1024}' /proc/meminfo; }
container_mem_mb() {
    if [ -f /sys/fs/cgroup/memory.current ]; then
        awk '{printf "%d", $1/1048576}' /sys/fs/cgroup/memory.current
    elif [ -f /sys/fs/cgroup/memory/memory.usage_in_bytes ]; then
        awk '{printf "%d", $1/1048576}' /sys/fs/cgroup/memory/memory.usage_in_bytes
    else
        echo "?"
    fi
}

while true; do
    now=$(date +%s)

    # --- Worker ---
    age=$(status_age)
    if [ "$age" -gt "$MAX_AGE" ]; then
        if [ $(( now - last_worker_restart )) -gt "$MAX_AGE" ]; then
            notify "status.json sin actualizar hace ${age}s (> ${MAX_AGE}s). Reiniciando el worker."
            supervisorctl -c /app/deploy/supervisord.conf restart worker || log "supervisorctl restart worker falló"
            last_worker_restart=$now
        else
            log "status.json viejo (${age}s) pero el worker fue reiniciado hace poco; esperando."
        fi
    fi

    # --- API ---
    if curl -s -m 5 -o /dev/null -f "$API_URL"; then
        if [ "$api_was_down" -eq 1 ]; then
            notify "La API volvió a responder."
            api_was_down=0
        fi
        api_failures=0
    else
        api_failures=$(( api_failures + 1 ))
        if [ "$api_failures" -ge 3 ] && [ "$api_was_down" -eq 0 ]; then
            notify "La API no responde en localhost:8000 (3 chequeos seguidos). supervisord la reinicia si murió; si sigue caída, revisar api.log."
            api_was_down=1
        fi
    fi

    # --- Memoria: una línea por chequeo (datos para el próximo incidente) + alerta ---
    avail=$(host_mem_available_mb); cmem=$(container_mem_mb)
    log "mem: host disponible ${avail} MB | contenedor ${cmem} MB | load $(cut -d' ' -f1-3 /proc/loadavg)"
    if [ -n "$avail" ]; then
        if [ "$avail" -lt "$MEM_ALERT_MB" ] && [ "$mem_alerted" -eq 0 ]; then
            notify "Memoria del host baja: ${avail} MB disponibles (contenedor usa ${cmem} MB). Riesgo de cuelgue; revisar 'docker stats'."
            mem_alerted=1
        elif [ "$avail" -gt $(( MEM_ALERT_MB * 2 )) ] && [ "$mem_alerted" -eq 1 ]; then
            notify "Memoria del host normalizada (${avail} MB disponibles)."
            mem_alerted=0
        fi
    fi

    # --- Disco (SQLite falla con 'database or disk is full' y el worker deja de escribir PNGs) ---
    used_pct=$(df --output=pcent /app/data 2>/dev/null | tail -1 | tr -dc '0-9')
    if [ -n "$used_pct" ]; then
        if [ "$used_pct" -ge "$DISK_ALERT_PCT" ] && [ "$disk_alerted" -eq 0 ]; then
            notify "Disco al ${used_pct}% (umbral ${DISK_ALERT_PCT}%). Forzando limpieza; si no baja, revisar 'docker system df' y data/."
            (cd /app && PYTHONPATH=/app/backend python3 /app/backend/scripts/cleanup.py >> /app/logs/cleanup.log 2>&1) || true
            disk_alerted=1
        elif [ "$used_pct" -lt $(( DISK_ALERT_PCT - 10 )) ] && [ "$disk_alerted" -eq 1 ]; then
            notify "Disco normalizado (${used_pct}%)."
            disk_alerted=0
mem_alerted=0

# Memoria: /proc/meminfo es la del host (no del cgroup); el uso del contenedor sale del cgroup.
host_mem_available_mb() { awk '/MemAvailable/ {printf "%d", $2/1024}' /proc/meminfo; }
container_mem_mb() {
    if [ -f /sys/fs/cgroup/memory.current ]; then
        awk '{printf "%d", $1/1048576}' /sys/fs/cgroup/memory.current
    elif [ -f /sys/fs/cgroup/memory/memory.usage_in_bytes ]; then
        awk '{printf "%d", $1/1048576}' /sys/fs/cgroup/memory/memory.usage_in_bytes
    else
        echo "?"
    fi
}
        fi
    fi

    sleep "$CHECK_EVERY"
done
