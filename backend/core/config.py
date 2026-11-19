import os
import torch
from dotenv import load_dotenv

load_dotenv() # Load environment variables from .env file

# --- Rutas del Pipeline ---
# Todas las rutas deben ser relativas a la raíz del contenedor (/app)
# Auto-detect model path
if os.getenv("MODEL_PATH"):
    MODEL_PATH = os.getenv("MODEL_PATH")
elif os.path.exists("/workspace/model/best_convlstm_model.pth"):
    MODEL_PATH = "/workspace/model/best_convlstm_model.pth"
else:
    MODEL_PATH = "/app/model/best_convlstm_model.pth"
INPUT_DIR = "/app/data/input_scans/"          # Buffer para archivos NC listos para predecir
OUTPUT_DIR = "/app/data/output_predictions/"   # Salida de predicciones en NC
ARCHIVE_DIR = "/app/data/archive_scans/"       # Archivo para los NC ya procesados
MDV_INBOX_DIR = "/app/data/mdv_inbox/"         # Bandeja de entrada para archivos MDV
MDV_ARCHIVE_DIR = "/app/data/mdv_archive/"     # Archivo para los MDV ya convertidos
MDV_OUTPUT_DIR = "/app/data/mdv_predictions/"  # Salida de predicciones en MDV
IMAGE_OUTPUT_DIR = "/app/backend/output/images"
STATUS_FILE_PATH = "/app/data/status.json"     # Archivo de estado constante para la API
DATA_DIR = "/app/data"                         # Archivos de estado diarios (manga de granizo, pushes diarios). Siempre absoluto:
                                               # la API corre con --chdir /app/backend y el worker en /app.
DB_PATH = "/app/data/radar_history.db"         # Base de datos SQLite

# --- Story Mode v2 ---
STORIES_MDV_DIR = "/app/data/stories_mdv/"         # Carpetas de tormentas con archivos MDV originales (INTOCABLES)
STORIES_NC_CACHE_DIR = "/app/data/stories_nc_cache/"  # NC temporales generados on-the-fly (se borran 1h post-stop)
STORIES_PREVIEWS_DIR = "/app/data/stories_previews/"  # PNG permanentes de scans originales (para exploración pre-modelo)
STORY_STATE_FILE = "/app/data/story_state.json"    # Estado persistente del story mode
STORY_FEED_INTERVAL_SECONDS = 60                   # Intervalo entre cada frame alimentado al modelo (1 minuto)

# --- Parámetros del Watcher ---
SECUENCE_LENGHT = 8
POLL_INTERVAL_SECONDS = 10

# --- Configuración del Dispositivo ---
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# --- Configuración del Modelo ---
MODEL_CONFIG = {
    'input_dim': 1, 'hidden_dims': [128, 128, 128], 'kernel_sizes': [(3, 3), (3, 3), (3, 3)],
    'num_layers': 3, 'pred_steps': 7, 'use_layer_norm': True,
    'img_height': 250, 'img_width': 250
}

# --- Configuración de Datos ---
DATA_CONFIG = {
    'min_dbz': -29.0, 'max_dbz': 65.0, 'variable_name': 'DBZ',
    'prediction_interval_minutes': 3.5,
    'physical_threshold_dbz': 30.0,
    'sensor_latitude': -34.64799880981445,
    'sensor_longitude': -68.01699829101562,
    'sensor_altitude_km': 0.55,
    'earth_radius_m': 6378137.0,

    # Parámetros para empaquetar la salida como byte, igual que el archivo de entrada
    'output_nc_scale_factor': 0.5,
    'output_nc_add_offset': 33.5,
    'output_nc_fill_value': -128, # _FillValue para el tipo byte
}

# --- Configuración de Inferencia ---
Z_BATCH_SIZE = 2

# --- Radares ---
# Definición de cada radar que el pipeline sabe atender. Activar con RADARS=sur,norte
# (default solo 'sur'). El primero de la lista es el primario: conserva las rutas históricas,
# corre el modo historia y, hasta que las alertas combinen radares, es el único que las dispara.
RADAR_DEFINITIONS = {
    "sur":   {"name": "SAN_RAFAEL", "label": "Radar Sur (San Rafael)",
              "lat": DATA_CONFIG['sensor_latitude'], "lon": DATA_CONFIG['sensor_longitude']},
    # min_level_km: altura mínima del filtro de ecos fijos propia de este radar (ver ALERT_MIN_LEVEL_KM).
    # Frente a San Martín la cordillera supera los 6 km (Aconcagua, Tupungato, Cordón del Plata),
    # así que los 5 km del sur dejan pasar decenas de ecos de montaña con "tope" clavado en 5 km.
    "norte": {"name": "SAN_MARTIN", "label": "Radar Norte (San Martín)", "lat": -33.0718, "lon": -68.4643,
              "min_level_km": 6.5},
}
RADARS = [r.strip().lower() for r in os.getenv("RADARS", "sur").split(",") if r.strip()]
if not RADARS or RADARS[0] != "sur" or any(r not in RADAR_DEFINITIONS for r in RADARS):
    raise RuntimeError(f"RADARS inválido: {RADARS!r}. Debe empezar por 'sur' y usar ids de {list(RADAR_DEFINITIONS)}")

# --- Seguridad ---
# SECRET_KEY firma los JWT. Sin ella no se arranca: con un default conocido
# cualquiera podría forjar un token de superadmin.
# Generar con: openssl rand -hex 32
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY or SECRET_KEY == "dev-secret-key-change-me":
    raise RuntimeError(
        "SECRET_KEY no configurada (o con el valor de ejemplo). "
        "Definila en backend/.env o en el entorno del contenedor: openssl rand -hex 32"
    )

# CORS / Frontend Domain
# Remove trailing slash to match Browser Origin header format
FRONTEND_URL = os.getenv("FRONTEND_URL", "*").rstrip("/")

# --- VAPID Keys for Push Notifications ---
# Generated with `vapid --gen`
# in production, set these via environment variables or file mounts
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY")
if VAPID_PRIVATE_KEY:
    # Remove wrapping quotes if present (common mistake in some env editors)
    VAPID_PRIVATE_KEY = VAPID_PRIVATE_KEY.strip('"').strip("'")
    VAPID_PRIVATE_KEY = VAPID_PRIVATE_KEY.replace("\\n", "\n")

VAPID_CLAIM_EMAIL = os.getenv("VAPID_CLAIM_EMAIL", "mailto:admin@example.com")

# --- Clave compartida para ingesta (telemetría TITAN y upload de MDV) ---
INGEST_SECRET_KEY = os.getenv("INGEST_SECRET_KEY")
if not INGEST_SECRET_KEY or INGEST_SECRET_KEY == "cambiar_esta_clave_en_el_dot_env":
    raise RuntimeError(
        "INGEST_SECRET_KEY no configurada (o con el valor de ejemplo). "
        "Es la clave compartida con telemetry_streamer.py y radar_bridge.py: openssl rand -hex 32"
    )

# --- Filtro de ecos fijos para las alertas ---
# Una celda cuenta como tormenta solo si tiene > 50 dBZ por encima de esta altura (km MSL).
# El clutter del piedemonte (terreno de 2 a 3,5 km) queda afuera; 50 dBZ sobre 5 km, por
# encima de la isoterma de 0 °C estival, es además la firma clásica de granizo.
# Se aplica a la detección de celdas (alertas y marcadores) y a la manga de granizo. 0 desactiva.
ALERT_MIN_LEVEL_KM = float(os.getenv("ALERT_MIN_LEVEL_KM", "5"))


def alert_min_level_km(radar_id: str) -> float:
    """Altura mínima del filtro de ecos fijos para un radar.

    Orden: ALERT_MIN_LEVEL_KM_<RADAR> en el entorno (p. ej. ALERT_MIN_LEVEL_KM_NORTE=7),
    luego ``min_level_km`` de RADAR_DEFINITIONS, y si no, el ALERT_MIN_LEVEL_KM global.
    """
    env = os.getenv(f"ALERT_MIN_LEVEL_KM_{radar_id.upper()}")
    if env:
        return float(env)
    return float(RADAR_DEFINITIONS.get(radar_id, {}).get("min_level_km", ALERT_MIN_LEVEL_KM))
# dBZ mínimo (por encima de ALERT_MIN_LEVEL_KM) para que un eco cuente como celda de tormenta.
# 45 dBZ a esa altura ya es convección fuerte. La manga de granizo sigue usando 55.
CELL_DBZ_THRESHOLD = float(os.getenv("CELL_DBZ_THRESHOLD", "45"))

# --- Telegram (mismo bot que usa el watchdog; ver services/telegram_service.py) ---
# Offset de getUpdates: solo el worker pollea al bot (vinculación de cuentas).
TELEGRAM_OFFSET_FILE = "/app/data/telegram_updates_offset.txt"
