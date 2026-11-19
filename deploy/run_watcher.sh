#!/bin/bash
# Lanza el Drive Watcher solo si hay remoto configurado (si no, sale limpio y supervisord no lo reintenta).
if [ -z "$RCLONE_REMOTE_BASE" ]; then
    echo "RCLONE_REMOTE_BASE no seteada: Drive Watcher deshabilitado."
    exit 0
fi
echo "Drive Watcher apuntando a $RCLONE_REMOTE_BASE"
exec python3 /app/tools/drive_watcher.py --remote-base "$RCLONE_REMOTE_BASE" --interval "${WATCHER_INTERVAL:-20}"
