#!/bin/bash
# El frontend de producción vive en Vercel; el local solo se levanta si se pide explícitamente.
if [ "$ENABLE_LOCAL_FRONTEND" != "1" ]; then
    echo "Frontend local deshabilitado (ENABLE_LOCAL_FRONTEND != 1). El de producción está en Vercel."
    exit 0
fi
exec npm start
