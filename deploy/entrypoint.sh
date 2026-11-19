#!/bin/bash
# Entrypoint del contenedor: resuelve el modelo y delega todo a supervisord (PID 1).
set -e

echo "Buscando modelo .pth en /app/model..."
FOUND_MODEL=$(find /app/model -name "*.pth" 2>/dev/null | head -n 1)
if [ -n "$FOUND_MODEL" ]; then
    echo "Modelo: $FOUND_MODEL"
    export MODEL_PATH="$FOUND_MODEL"
else
    echo "WARNING: no se encontró ningún .pth en /app/model. El worker va a fallar al arrancar."
fi

# Limitar hilos de PyTorch para no saturar el VPS (sobrescribible desde el .env)
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-2}"

mkdir -p /app/logs
# supervisord hereda este entorno y se lo pasa a cada programa
exec /usr/bin/supervisord -n -c /app/deploy/supervisord.conf
