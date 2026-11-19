# Operación del contenedor (VPS)

`supervisord` es el PID 1 del contenedor y mantiene vivos estos programas
(`deploy/supervisord.conf`): `worker` (pipeline de inferencia), `api`
(gunicorn), `watcher` (rclone/Drive), `watchdog` y opcionalmente `frontend`.
Si uno muere, se reinicia solo. Los logs en `/app/logs` rotan (20 MB × 5).

## Comandos útiles

```bash
# Estado de cada proceso
docker exec convlstm_cpu_app supervisorctl -c /app/deploy/supervisord.conf status

# Reiniciar solo el worker (o api / watcher) sin tocar el contenedor
docker exec convlstm_cpu_app supervisorctl -c /app/deploy/supervisord.conf restart worker

# Salud del contenedor (healthy / unhealthy) y motivo
docker inspect convlstm_cpu_app --format '{{.State.Health.Status}}'
docker inspect convlstm_cpu_app --format '{{json .State.Health.Log}}' | python3 -m json.tool | tail -20

# Logs
docker exec convlstm_cpu_app tail -f /app/logs/worker.log
docker exec convlstm_cpu_app tail -f /app/logs/watchdog.log
```

## Watchdog

Cada `WATCHDOG_INTERVAL_SECONDS` (60) revisa:

- `status.json` (lo escribe el worker en cada vuelta del loop). Si tiene más
  de `STATUS_MAX_AGE_SECONDS` (900) reinicia el worker y avisa. Subir el
  umbral si un ciclo de inferencia en CPU tarda más de eso.
- La API en `localhost:8000`. Tres fallos seguidos → aviso.

Avisos por Telegram si en el `.env` están `TELEGRAM_BOT_TOKEN` y
`TELEGRAM_CHAT_ID` (bot con @BotFather, chat_id con @userinfobot). Sin
ellos, solo loguea. Para saber si el VPS entero se cayó hace falta un
monitor externo (p. ej. UptimeRobot sobre `https://vps-api.../api/status`).

## Variables del `.env` relacionadas

| Variable | Default | Uso |
| --- | --- | --- |
| `STATUS_MAX_AGE_SECONDS` | 900 | Umbral del watchdog / healthcheck |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | — | Avisos del watchdog y alertas a parques solares |
| `TELEGRAM_BOT_USERNAME` | getMe | Username del bot para el link de vinculación de usuarios |
| `GLM_ENABLED` / `GLM_BBOX` / `GLM_KEEP_MINUTES` | 1 / Mendoza+San Juan / 60 | Rayos GLM de GOES-19 (NOAA S3, ~90 MB/h); `/api/lightning?minutes=N` |
| `RADARS` | sur | Radares que atiende el worker, el primero es el primario; `sur,norte` activa San Martín |
| `ALERT_MIN_LEVEL_KM` | 5 | Una celda cuenta solo si supera `CELL_DBZ_THRESHOLD` por encima de esta altura (filtro de ecos fijos; también la manga). Vale para el radar sur |
| `ALERT_MIN_LEVEL_KM_NORTE` | 6.5 | Lo mismo para el radar norte: frente a San Martín la cordillera pasa los 6 km. Cualquier radar acepta `ALERT_MIN_LEVEL_KM_<ID>` |
| `CELL_DBZ_THRESHOLD` | 45 | dBZ mínimo para que un eco cuente como celda de tormenta |
| `SOLAR_PARKS_FILE` | `/app/data/solar_parks.json` | Lista de parques `[{id, name, lat, lon}]`. Plantilla en `deploy/solar_parks.example.json`; la lista real no se versiona |
| `SOLAR_PARK_ALERT_KM` / `SOLAR_PARK_CLEAR_KM` | 20 / 25 | Distancia celda–parque para ALERTA / CESE |
| `SOLAR_PARK_REPEAT_MINUTES` | 30 | Repetición del aviso mientras siga la condición |
| `SOLAR_PARK_HAIL_DBZ` | 51 | dBZ desde el cual el tipo es GRANIZO |
| `ENABLE_LOCAL_FRONTEND` | 0 | Levantar Next.js dentro del contenedor (prod usa Vercel) |
| `OMP_NUM_THREADS` | 2 | Hilos de PyTorch en CPU |
| `BIND_ADDRESS` | 0.0.0.0 | `127.0.0.1` para no exponer 8000/3000 a internet |

## Disco

El pipeline genera ~17 GB/día de NetCDF con el formato actual del radar (44 MB
por volumen). `backend/scripts/cleanup.py` corre cada hora desde el worker con
retención por directorio (`RETENTION_*_HOURS`) y una guardia por espacio libre
(`MIN_FREE_GB` / `TARGET_FREE_GB`); el watchdog avisa al superar `DISK_ALERT_PCT`.

**Después de cada `docker compose up --build`**, borrar las capas viejas: cada
imagen pesa ~5,6 GB y la caché de build llega a 20+ GB.

```bash
docker image prune -f && docker builder prune -af
```

Diagnóstico rápido cuando el disco se llena:

```bash
df -h / ; docker system df ; du -sh ~/app_convlstm/data/* | sort -h | tail
```

## Telegram: usuarios y parques solares

El mismo bot del watchdog (`TELEGRAM_BOT_TOKEN`) cumple dos funciones más:

- **Usuarios registrados**: desde Ajustes → Telegram generan un link `t.me/<bot>?start=<token>`
  (vence a los 15 min). El worker es el único proceso que hace `getUpdates`; atiende
  `/start <token>` (vincula el chat a la cuenta), `/start` (reactiva) y `/stop` (pausa).
  Reciben por Telegram las mismas alertas que por push, con las mismas preferencias
  (`backend/services/notifier.py`). Si el usuario bloquea al bot, se desvincula solo.
  **No** configurar un webhook para este bot ni pollearlo desde otro proceso: `getUpdates` da 409.
- **Parques solares** (`backend/services/solar_parks.py`): con cada scan observado, si hay una celda
  > 50 dBZ a menos de `SOLAR_PARK_ALERT_KM` de un parque se manda al chat del admin
  (`TELEGRAM_CHAT_ID`) un mensaje con el formato de AEMSA (tipo, dBZ, altura de tope,
  desplazamiento, distancia y rumbo). Se repite cada `SOLAR_PARK_REPEAT_MINUTES`, de inmediato si
  escala de TORMENTA a GRANIZO, y manda CESE cuando no queda ninguna celda a menos de
  `SOLAR_PARK_CLEAR_KM`. Estado e historial en `data/solar_park_alerts.json`; el superadmin ve los
  parques en el mapa (`GET /api/solar-parks`). Las predicciones del modelo y el Story Mode nunca
  disparan estas alertas. La lista real de parques vive solo en el VPS, en `data/solar_parks.json`;
  el repo trae `deploy/solar_parks.example.json` como plantilla del formato.

## Ingesta de scans: Radar Bridge (PC del radar → VPS)

Alternativa al Drive Watcher que no depende de Drive: `tools/radar_bridge.py` corre como
servicio en la PC del radar (`titan5`), vigila la carpeta de TITAN
(`.../mdv/radar/cart_no_clutter/AAAAMMDD/HHMMSS.mdv`) y sube cada MDV nuevo a
`POST /api/upload_mdv` con `INGEST_SECRET_KEY` (la misma clave que el streamer de
telemetría). Sube los archivos como `AAAAMMDD_HHMMSS.mdv` para que no colisionen dos días.
Lleva un registro de enviados (JSON) para no repetir tras reinicios, espera a que cada
archivo termine de escribirse, salta lo viejo al arrancar y reintenta con espera creciente
si el VPS no responde. El endpoint es idempotente (200 si el archivo ya llegó), así que el
bridge y el Drive Watcher pueden convivir; `--radar` etiqueta la fuente (sur/norte).
Cada radar tiene su bandeja: `sur` usa `mdv_inbox` (pipeline actual); cualquier otro va a
`/app/data/mdv_inbox_<radar>`, que por ahora solo acumula (el worker multi-radar es la fase 2).
`--no-proxy` ignora `http_proxy`/`https_proxy` del sistema (la VM del radar norte las tiene mal
configuradas y la salida directa funciona); plantilla en `tools/radar_bridge_norte.service`.

Instalación en la PC del radar (una vez):

```bash
cp tools/radar_bridge.py /home/titan5/projDir/scripts/
sudo cp tools/radar_bridge.service /etc/systemd/system/
sudo nano /etc/systemd/system/radar_bridge.service   # poner INGEST_SECRET_KEY
sudo systemctl daemon-reload && sudo systemctl enable --now radar_bridge
tail -f /home/titan5/projDir/logs/radar_bridge.log
```
