import os
import time
import logging
import shutil
import json
import subprocess
from datetime import datetime, timedelta, timezone
import numpy as np
import torch
import xarray as xr
from netCDF4 import Dataset as NCDataset
import pyproj
import glob
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap, BoundaryNorm
import cartopy.crs as ccrs
import sqlite3
from scipy.ndimage import label, center_of_mass


# Importamos desde nuestros módulos
from core.config import (MDV_INBOX_DIR, MDV_ARCHIVE_DIR, INPUT_DIR, OUTPUT_DIR, ARCHIVE_DIR, 
                    SECUENCE_LENGHT, POLL_INTERVAL_SECONDS, MODEL_PATH, 
                    DATA_CONFIG, STATUS_FILE_PATH, MDV_OUTPUT_DIR, IMAGE_OUTPUT_DIR, DB_PATH,
                    FRONTEND_URL, STORY_FEED_INTERVAL_SECONDS, TELEGRAM_OFFSET_FILE, DATA_DIR,
                    ALERT_MIN_LEVEL_KM, CELL_DBZ_THRESHOLD)
from model.predict import ModelPredictor
from services import aircraft_tracker
from services import telemetry_replay  # noqa: F401  starts replay thread on import
from services import notifier, telegram_service, solar_parks, lightning
from core.radar_context import RadarContext, get_radar_contexts

# --- Utilitarios ---
def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # Radius of earth in km
    dLat = np.radians(lat2 - lat1)
    dLon = np.radians(lon2 - lon1)
    a = np.sin(dLat/2) * np.sin(dLat/2) + \
        np.cos(np.radians(lat1)) * np.cos(np.radians(lat2)) * \
        np.sin(dLon/2) * np.sin(dLon/2)
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1-a))
    d = R * c # Distance in km
    return d


# --- Configuración del Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

ECHO_TOP_DBZ = 30.0  # Altura de tope: nivel más alto con al menos este dBZ dentro de la celda


def detect_storm_cells(dbz_data, x_vals, y_vals, projection, dbz_3d=None, altitudes=None, threshold_field=None):
    """
    Detecta celdas de tormenta (> CELL_DBZ_THRESHOLD, default 45 dBZ) y calcula sus centroides.
    Clasificación por el máximo de la columna:
      - > 45 dBZ: Tormenta Intensa
      - > 50 dBZ: Lluvia Torrencial
      - > 55 dBZ: Probable Granizo
      - > 58 dBZ: Granizo / Lluvia Torrencial Extrema
    Si se pasa el volumen 3D (``dbz_3d`` [z, y, x]) y sus alturas en km, agrega ``top_km``
    (altura de tope) a cada celda. Las predicciones son 2D y no lo tienen.
    ``threshold_field`` (2D, misma grilla) es el campo sobre el que se aplica el umbral de dBZ;
    por defecto es ``dbz_data``. Con el composite de niveles altos (ver ALERT_MIN_LEVEL_KM) se
    descartan los ecos fijos del piedemonte: solo cuenta la reflectividad por encima de esa altura.
    El ``max_dbz`` informado sigue siendo el máximo de ``dbz_data`` (toda la columna) dentro de la celda.
    """
    cells = []
    if threshold_field is None or threshold_field.shape != dbz_data.shape:
        threshold_field = dbz_data
    # Thresholding: solo interesa lo que supera el umbral (por encima de ALERT_MIN_LEVEL_KM)
    mask = threshold_field > CELL_DBZ_THRESHOLD
    
    if not np.any(mask):
        return []

    top_mask_3d = None
    if dbz_3d is not None and altitudes is not None and dbz_3d.ndim == 3 and dbz_3d.shape[-2:] == dbz_data.shape:
        top_mask_3d = np.nan_to_num(dbz_3d, nan=-999.0) >= ECHO_TOP_DBZ

    # Connected Components
    labeled_array, num_features = label(mask)
    
    geo_proj = ccrs.Geodetic()

    for label_idx in range(1, num_features + 1):
        # Mask para esta celda
        cell_mask = (labeled_array == label_idx)
        
        # Max dBZ en la celda
        max_dbz = float(np.max(dbz_data[cell_mask]))
        
        # Clasificación
        hazard_type = "Tormenta Intensa"
        if max_dbz > 58:
            hazard_type = "Granizo Confirmado"
        elif max_dbz > 55:
            hazard_type = "Probable Granizo"
        elif max_dbz > 50:
            hazard_type = "Lluvia Torrencial"
            
        # Encontrar las coordenadas del píxel más intenso (Core de la tormenta)
        # en lugar del centroide geométrico para mayor precisión en el marcador
        # Encontrar los índices (y, x) donde ocurre el max_dbz dentro de esta celda
        y_indices, x_indices = np.where(cell_mask & (dbz_data >= max_dbz - 0.1))
        
        # Si hay un cluster de píxeles con la máxima intensidad, promediamos sus posiciones
        # para que el marcador quede en el verdadero "centro de gravedad" de la intensidad máxima.
        mean_y_idx = int(np.mean(y_indices))
        mean_x_idx = int(np.mean(x_indices))
        
        # Bounds check
        px_x = max(0, min(mean_x_idx, len(x_vals) - 1))
        px_y = max(0, min(mean_y_idx, len(y_vals) - 1))
        
        real_x = x_vals[px_x] * 1000 # a metros
        real_y = y_vals[px_y] * 1000 # a metros
        
        # Transformar a Lat/Lon
        geo_point = geo_proj.transform_point(real_x, real_y, projection)
        lon, lat = geo_point[0], geo_point[1]
        
        # Altura de tope (solo scans 3D observados)
        top_km = None
        if top_mask_3d is not None:
            try:
                levels_hit = np.any(top_mask_3d & cell_mask[None, :, :], axis=(1, 2))
                if np.any(levels_hit):
                    top_km = round(float(altitudes[int(np.max(np.where(levels_hit)[0]))]), 1)
            except Exception as e:
                logging.debug(f"No se pudo calcular altura de tope: {e}")

        cells.append({
            "type": hazard_type,
            "max_dbz": round(max_dbz, 1),
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "top_km": top_km
        })
        
    return cells

import functools

def update_hail_swath(dbz_data: np.ndarray, x: np.ndarray, y: np.ndarray, projection, min_dbz: float = 55.0,
                      ctx: RadarContext = None):
    """
    Extrae los píxeles donde DBZ >= min_dbz y los añade al archivo JSON acumulativo del día.
    """
    try:
        # Encontrar índices donde la reflectividad supera el umbral
        y_indices, x_indices = np.where(dbz_data >= min_dbz)
        if len(x_indices) == 0:
            return  # No hay granizo

        geo_proj = ccrs.Geodetic()
        new_points = []
        
        # Reducir resolución para no guardar miles de puntos por cada celda 
        # (ej. tomamos 1 de cada 4 puntos)
        step = 4
        for i in range(0, len(x_indices), step):
            xi = x[x_indices[i]] * 1000  # Convertir km a metros para la proyección
            yi = y[y_indices[i]] * 1000
            lon, lat = geo_proj.transform_point(xi, yi, projection)
            # Redondear a 3 decimales (~110m precisión) para deduplicar fácil
            new_points.append([round(float(lon), 3), round(float(lat), 3)])

        if not new_points:
            return

        # El día pluviométrico/granicero empieza a las 8 AM local (11 AM UTC)
        now_utc = datetime.now(timezone.utc)
        if now_utc.hour < 11:
            # Antes de las 11 AM UTC (8 AM local), pertenece al día anterior
            business_day = now_utc - timedelta(days=1)
        else:
            business_day = now_utc
            
        today_str = business_day.strftime("%Y%m%d")
        swath_file = ctx.hail_swath_file(today_str) if ctx else os.path.join(DATA_DIR, f"hail_swath_{today_str}.json")
        
        # Leer puntos existentes
        existing_points = set()
        if os.path.exists(swath_file):
            try:
                with open(swath_file, 'r') as f:
                    data = json.load(f)
                    for pt in data.get("points", []):
                        existing_points.add((pt[0], pt[1]))
            except json.JSONDecodeError:
                pass
                
        # Agregar nuevos puntos si no existen
        added_count = 0
        for pt in new_points:
            t_pt = (pt[0], pt[1])
            if t_pt not in existing_points:
                existing_points.add(t_pt)
                added_count += 1
                
        if added_count > 0:
            # Guardar
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(swath_file, 'w') as f:
                json.dump({"date": today_str, "points": [list(pt) for pt in existing_points]}, f)
            logging.info(f"Agregados {added_count} puntos a la manga de granizo de hoy.")

    except Exception as e:
        logging.error(f"Error actualizando la manga de granizo: {e}")

def _grid_bounds(x_km, y_km, lat_0, lon_0):
    """[[lat_min, lon_min], [lat_max, lon_max]] de una grilla cartesiana (km) centrada en el radar.

    Mismo criterio que TITAN: extremos sobre las esquinas y los puntos medios de los bordes de la
    grilla, proyectados con azimutal equidistante (R=6378137, como en el NC de salida).
    """
    proj = pyproj.Proj(proj="aeqd", lat_0=lat_0, lon_0=lon_0, R=6378137.0)
    xs = (float(np.min(x_km)) * 1000.0, 0.0, float(np.max(x_km)) * 1000.0)
    ys = (float(np.min(y_km)) * 1000.0, 0.0, float(np.max(y_km)) * 1000.0)
    lons, lats = [], []
    for px in xs:
        for py in ys:
            lon, lat = proj(px, py, inverse=True)
            lons.append(lon); lats.append(lat)
    return [[round(min(lats), 4), round(min(lons), 4)], [round(max(lats), 4), round(max(lons), 4)]]


def _scan_time_from_path(nc_file_path):
    """Hora (UTC) del scan a partir del nombre YYYYMMDDHHMMSS.nc; si no se puede, ahora."""
    base = os.path.splitext(os.path.basename(nc_file_path))[0]
    digits = ''.join(ch for ch in base if ch.isdigit())
    try:
        return datetime.strptime(digits[-14:], '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def check_proximity_alerts(storm_cells):
    """
    Avisa a los usuarios que tienen una celda de tormenta a menos de 20 km (push y/o Telegram).
    Cooldown de 10 minutos por usuario. En Story Mode solo a admins (es una repetición histórica).
    Solo debe llamarse con scans OBSERVADOS, nunca con predicciones.
    """
    if not storm_cells:
        return

    try:
        from core.story_manager import story_state
        admin_only = story_state.get_status().get('is_active', False)

        conn = sqlite3.connect(DB_PATH)
        targets = notifier.proximity_targets(conn, admin_only=admin_only)
        if not targets:
            conn.close()
            return

        now = datetime.now(timezone.utc)
        alerted_users = set()

        for t in targets:
            # Cooldown de 10 min por usuario (vale para todos sus dispositivos y Telegram)
            if t["last_alert"]:
                try:
                    if (now - datetime.fromisoformat(t["last_alert"])).total_seconds() < 600:
                        continue
                except Exception:
                    pass

            nearest = min(storm_cells, key=lambda c: haversine(t["lat"], t["lon"], c['lat'], c['lon']))
            min_dist = haversine(t["lat"], t["lon"], nearest['lat'], nearest['lon'])
            if min_dist >= 20:
                continue

            title = "¡Tormenta Cercana!"
            msg = f"Detectada {nearest['type']} a {int(min_dist)}km de tu ubicación."
            if nearest.get("motion_dir") and nearest["motion_dir"] != "estacionaria":
                msg += f" Se desplaza hacia el {nearest['motion_dir'].lower()}."
            logging.info(f"Proximity Alert -> user {t['user_id']} ({t['channel']}): {msg}")

            if t["channel"] == "push":
                ok = notifier.send_push(t["endpoint"], t["p256dh"], t["auth"], notifier.payload(title, msg, "/"))
            else:
                ok = notifier.send_telegram_user(t["user_id"], t["chat_id"], notifier.telegram_text(title, msg))
            if ok:
                alerted_users.add(t["user_id"])

        for user_id in alerted_users:
            conn.execute("UPDATE users SET last_proximity_alert = ? WHERE id = ?", (now.isoformat(), user_id))
        conn.commit()
        conn.close()

    except Exception as e:
        logging.error(f"Error in check_proximity_alerts: {e}")


def check_solar_park_alerts(storm_cells):
    """Alertas privadas a parques solares (Telegram al admin). Se omiten en Story Mode: es una repetición histórica."""
    try:
        from core.story_manager import story_state
        if story_state.get_status().get('is_active', False):
            return
        solar_parks.check_alerts(storm_cells)
    except Exception as e:
        logging.error(f"Error in check_solar_park_alerts: {e}")


def check_and_send_aircraft_alerts(sent_aircraft_alerts):
    """
    Verifica si hay aviones antigranizo en el tracking y avisa a quienes lo pidieron (push y Telegram).
    Usa un diccionario en memoria para no spamear (cooldown de 4 horas por avión).
    """
    try:
        data = aircraft_tracker.get_aircraft_data()
        if not data:
            return

        now = datetime.now(timezone.utc)
        # Solo notificar para la flota antigranizo conocida (filtrar errores tipograficos ADS-B)
        VALID_CALLSIGNS = ["VBCR", "VBCT", "VBCU"]

        for ac in data:
            # Opensky gives us "reg", local UDP gives us "callsign" mostly.
            reg_or_callsign = (ac.get("reg") or ac.get("callsign") or "").strip()
            if not reg_or_callsign or reg_or_callsign.upper() not in VALID_CALLSIGNS:
                continue

            # Check if we already alerted for this aircraft recently (4 hours cooldown)
            last_alert_time = sent_aircraft_alerts.get(reg_or_callsign)
            if last_alert_time and (now - last_alert_time).total_seconds() < 14400:
                continue

            logging.info(f"New aircraft flight detected: {reg_or_callsign}. Sending alerts...")
            sent_aircraft_alerts[reg_or_callsign] = now
            notifier.broadcast("aircraft", "¡Avión Antigranizo!",
                               f"✈️ Avión en vuelo ({reg_or_callsign}) detectado en el radar.", "/")
    except Exception as e:
        logging.error(f"Error checking aircraft alerts: {e}")

FORECAST_DAILY_FILE = os.path.join(DATA_DIR, "last_forecast_push.txt")
CLEANUP_INTERVAL_SECONDS = int(os.environ.get("CLEANUP_INTERVAL_MINUTES", 60)) * 60
_last_cleanup_ts = 0.0

import requests
import subprocess

def check_periodic_cleanup():
    """
    Dispara scripts/cleanup.py en segundo plano cada CLEANUP_INTERVAL_MINUTES
    (y una vez al arrancar). Antes corría una vez al día con retención de 48 h:
    con NetCDF de ~44 MB (formato 1000x1000x36) eso llenaba el disco del VPS
    y SQLite fallaba con 'database or disk is full'.
    """
    global _last_cleanup_ts
    now_ts = time.time()
    if now_ts - _last_cleanup_ts < CLEANUP_INTERVAL_SECONDS:
        return
    _last_cleanup_ts = now_ts
    try:
        cleanup_script_path = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'cleanup.py')
        # Asíncrono para que el worker no se congele borrando archivos
        subprocess.Popen(["python3", cleanup_script_path])
        logging.info("Limpieza periódica disparada (scripts/cleanup.py).")
    except Exception as e:
        logging.error(f"Error disparando script de limpieza: {e}")

def check_daily_forecast_alert():
    """
    Se ejecuta periódicamente. Comprueba si son más de las 12:00 PM hora local de Mendoza
    y si se envió el pronóstico del día hoy midiendo el archivo de bandera text.
    Si no se ha enviado, consulta la API pública de pronóstico y envía un push con la data.
    """
    now = datetime.now(timezone.utc)
    # Convertir a hora local (Mendoza UTC-3)
    local_dt = now - timedelta(hours=3)
    local_hour = local_dt.hour
    
    # 1. Comprobar horario de triggeo (a partir de las 12 PM)
    if local_hour < 12:
        return
        
    today_str = local_dt.strftime("%Y%m%d")
    already_sent = False
    
    # 2. Comprobar si ya se mandó hoy
    if os.path.exists(FORECAST_DAILY_FILE):
        try:
            with open(FORECAST_DAILY_FILE, "r") as f:
                last_sent = f.read().strip()
                if last_sent == today_str:
                    already_sent = True
        except:
            pass
            
    if already_sent:
        return
        
    logging.info("Forecast Alert: Es mediodía y no se envió hoy. Descargando pronóstico...")
    
    # 3. Descargar pronóstico oficial (San Rafael, Sur de Mendoza - Open Meteo)
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast?"
            "latitude=-34.6177&longitude=-68.3301"
            "&daily=weathercode,temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max,windspeed_10m_max,"
            "winddirection_10m_dominant,uv_index_max"
            "&current_weather=true"
            "&timezone=America%2FArgentina%2FMendoza&forecast_days=1"
        )
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if "daily" not in data:
                logging.warning("Forecast Alert: Open-Meteo no devolvió daily metrics para hoy.")
                return
            
            daily = data["daily"]
            wmo_code = daily.get("weathercode", [0])[0]
            max_temp = daily.get("temperature_2m_max", ["?"])[0]
            min_temp = daily.get("temperature_2m_min", ["?"])[0]
            precip_prob = daily.get("precipitation_probability_max", [0])[0] or 0
            wind_max = daily.get("windspeed_10m_max", [0])[0] or 0
            wind_dir_deg = daily.get("winddirection_10m_dominant", [0])[0] or 0
            uv_max = daily.get("uv_index_max", [0])[0] or 0
            
            # --- Construir descripción rica ---
            # Cielo
            WMO_CIELO = {
                0: "Cielo despejado", 1: "Cielo mayormente despejado",
                2: "Cielo parcialmente nublado", 3: "Cielo nublado",
                45: "Niebla", 48: "Niebla con escarcha",
                51: "Llovizna leve", 53: "Llovizna moderada", 55: "Llovizna densa",
                61: "Lluvias leves", 63: "Lluvias moderadas", 65: "Lluvias fuertes",
                71: "Nevadas leves", 73: "Nevadas moderadas", 75: "Nevadas fuertes",
                80: "Chaparrones leves", 81: "Chaparrones moderados", 82: "Chaparrones violentos",
                95: "Tormentas eléctricas", 96: "Tormentas con granizo leve",
                99: "Tormentas con granizo fuerte"
            }
            cielo = WMO_CIELO.get(wmo_code, "Condiciones variables")
            
            # Dirección del viento
            DIRS = ["norte", "noreste", "este", "sureste", "sur", "suroeste", "oeste", "noroeste"]
            wind_label = DIRS[int((wind_dir_deg + 22.5) % 360 / 45)]
            
            # Intensidad del viento
            if wind_max < 10:
                wind_txt = f"Vientos calmos del {wind_label}"
            elif wind_max < 25:
                wind_txt = f"Vientos leves del {wind_label} ({wind_max:.0f} km/h)"
            elif wind_max < 45:
                wind_txt = f"Vientos moderados del {wind_label} ({wind_max:.0f} km/h)"
            else:
                wind_txt = f"Vientos fuertes del {wind_label} ({wind_max:.0f} km/h)"
            
            # Precipitación
            if precip_prob >= 60:
                precip_txt = f"Alta probabilidad de precipitaciones ({precip_prob}%)"
            elif precip_prob >= 30:
                precip_txt = f"Probabilidad moderada de precipitaciones ({precip_prob}%)"
            elif precip_prob >= 10:
                precip_txt = f"Baja probabilidad de precipitaciones ({precip_prob}%)"
            else:
                precip_txt = "Sin precipitaciones esperadas"
            
            # UV
            if uv_max >= 8:
                uv_txt = f"Índice UV muy alto ({uv_max:.0f})"
            elif uv_max >= 6:
                uv_txt = f"Índice UV alto ({uv_max:.0f})"
            elif uv_max >= 3:
                uv_txt = f"Índice UV moderado ({uv_max:.0f})"
            else:
                uv_txt = ""
            
            # Ensamblar descripción completa
            parts = [f"{cielo}.", wind_txt + ".", precip_txt + "."]
            if uv_txt:
                parts.append(uv_txt + ".")
            txt_pronostico = " ".join(parts)
            
            # Formatear el Web Push Payload
            notification_data = {
                "title": f"🌤️ Pronóstico San Rafael {local_dt.strftime('%d/%m')}",
                "body": f"Máx: {max_temp}°C | Mín: {min_temp}°C\n{txt_pronostico}",
                "url": "/",
                "icon": "/icon-192x192.png"
            }
            
            # 4. Historial + push + Telegram (ver services/notifier.py)
            result = notifier.broadcast("forecast", notification_data["title"], notification_data["body"], "/", ttl="86400")
            logging.info(f"Forecast Alert: Enviado a {result['push_sent']} dispositivos y {result['telegram_sent']} chats de Telegram.")
            
            # 5. Marcar como enviado
            try:
                with open(FORECAST_DAILY_FILE, "w") as f:
                    f.write(today_str)
            except Exception as e:
                logging.error(f"Failed to write forecast state file: {e}")
                
        else:
            logging.error(f"Forecast Alert: HTTP Error {resp.status_code}")
    except Exception as e:
        logging.error(f"Forecast Alert Runtime Error: {e}")


def check_daily_engagement_alerts(storm_cells, radar_lat, radar_lon, ctx: RadarContext):
    """
    Verifica si hay celdas >= 55 dBZ a menos de 70km del radar.
    Si la condición se mantiene por 30 minutos entre las 10:00 y las 23:00,
    envía un push notification global. Solo envía 1 por día.
    """
    if not storm_cells:
        ctx.engagement_state["first_detection_time"] = None
        return

    now = datetime.now(timezone.utc)
    # Convertir a hora local (Mendoza UTC-3)
    local_hour = (now.hour - 3) % 24
    if not (10 <= local_hour <= 23):
        ctx.engagement_state["first_detection_time"] = None
        return

    # Verificar si ya se envió hoy
    today_str = (now - timedelta(hours=3)).strftime("%Y%m%d")
    already_sent = False
    if os.path.exists(ctx.engagement_daily_file):
        try:
            with open(ctx.engagement_daily_file, "r") as f:
                last_sent = f.read().strip()
                if last_sent == today_str:
                    already_sent = True
        except:
            pass
    
    if already_sent:
        # Prevent keeping active triggers if already sent today
        ctx.engagement_state["first_detection_time"] = None
        return

    # Verificar celdas válidas (>= 55 dBZ, dist < 70km)
    has_valid_cell = False
    for cell in storm_cells:
        if cell.get("max_dbz", 0) >= 55.0:
            dist = haversine(radar_lat, radar_lon, cell['lat'], cell['lon'])
            if dist < 70.0:
                has_valid_cell = True
                break
                
    if not has_valid_cell:
        # Abort if condition breaks before 30m
        ctx.engagement_state["first_detection_time"] = None
        return

    # Si hay celda válida y es la primera vez (estado vacío):
    if ctx.engagement_state["first_detection_time"] is None:
        ctx.engagement_state["first_detection_time"] = now
        logging.info("Engagement Alert: Primera celda detectada >= 55dBZ. Iniciando reloj de 30 mins.")
        return
        
    # Verificar si pasaron 30 min (1800 seg)
    time_diff = (now - ctx.engagement_state["first_detection_time"]).total_seconds()
    if time_diff >= 1800:
        logging.info("Engagement Alert: Condición de severidad mantenida >30 mins. Enviando Push Global.")
        # Aviso global: historial + push + Telegram (ver services/notifier.py)
        result = notifier.broadcast(
            "engagement", "¡Tormentas detectadas! ⛈️",
            "Se registró actividad importante en el radar. ¿Ya revisaste el mapa en vivo?", "/", ttl="86400")
        logging.info(f"Engagement Alert: Enviada a {result['push_sent']} dispositivos y {result['telegram_sent']} chats de Telegram.")
        
        # Registrar como eviado hoy
        try:
            with open(ctx.engagement_daily_file, "w") as f:
                f.write(today_str)
        except Exception as e:
            logging.error(f"Failed to write engagement file: {e}")
            
        ctx.engagement_state["first_detection_time"] = None

def _save_latest_cells(ctx: RadarContext, cells: list, scan_time: datetime) -> None:
    """Últimas celdas observadas por el radar, para alertas que combinan todos los radares."""
    try:
        with open(ctx.cells_file, "w") as f:
            json.dump({"radar": ctx.radar_id, "time": scan_time.isoformat(), "cells": cells}, f)
    except Exception as e:
        logging.warning(f"[{ctx.radar_id}] No se pudo guardar cells_latest: {e}")


def generar_imagen_transparente_y_bounds(nc_file_path: str, output_image_path: str, skip_levels: int = 2,
                                         is_observation: bool = False, ctx: RadarContext = None):
    """
    Genera una imagen transparente de reflectividad compuesta y devuelve sus coordenadas geográficas.
    Con ``is_observation=True`` (scan real del radar) además evalúa las alertas: proximidad,
    engagement y parques solares. Las predicciones del modelo NUNCA disparan alertas.
    """
    try:
        logging.info(f"Generando imagen transparente para: {os.path.basename(nc_file_path)}")
        
        ds = xr.open_dataset(nc_file_path, mask_and_scale=True, decode_times=False)

        lon_name = 'longitude' if 'longitude' in ds.coords else 'x0'
        lat_name = 'latitude' if 'latitude' in ds.coords else 'y0'
        
        x = ds[lon_name].values
        y = ds[lat_name].values
        dbz_data = ds['DBZ'].squeeze().values
        alt_name = next((n for n in ('altitude', 'z0', 'z', 'height') if n in ds.coords or n in ds.dims), None)
        altitudes = ds[alt_name].values if alt_name else None

        # --- 1. Crear el composite visual ---
        if dbz_data.ndim == 3:
            if dbz_data.shape[0] > skip_levels:
                composite_data_2d = np.nanmax(dbz_data[skip_levels:, :, :], axis=0)
            else:
                 composite_data_2d = np.nanmax(dbz_data, axis=0)
                 
            # --- 1.5 Crear el composite para DETECCIÓN (ignorar clutter bajo 3km) ---
            # Si el radar tiene resolución vertical de 1km, saltar 3 niveles ignora 0, 1 y 2km.
            detection_skip = max(skip_levels, 3) 
            if dbz_data.shape[0] > detection_skip:
                detection_composite_2d = np.nanmax(dbz_data[detection_skip:, :, :], axis=0)
            else:
                detection_composite_2d = composite_data_2d

            # --- 1.6 Composite de niveles ALTOS para el umbral de celdas (filtro de ecos fijos) ---
            # Solo cuenta como tormenta lo que supera CELL_DBZ_THRESHOLD por encima de ALERT_MIN_LEVEL_KM.
            # La altura es por radar (ctx.min_level_km): el norte necesita más que el sur por la cordillera.
            min_level_km = ctx.min_level_km if ctx is not None else ALERT_MIN_LEVEL_KM
            alert_composite_2d = detection_composite_2d
            if min_level_km > 0 and altitudes is not None and len(altitudes) == dbz_data.shape[0]:
                high_levels = np.asarray(altitudes, dtype=float) >= min_level_km
                if np.any(high_levels):
                    alert_composite_2d = np.nanmax(dbz_data[high_levels, :, :], axis=0)
                else:
                    radar_tag = ctx.radar_id if ctx is not None else "sur"
                    logging.warning(f"[{radar_tag}] min_level_km={min_level_km} supera el nivel más alto del volumen; sin filtro.")
                
        elif dbz_data.ndim == 2:
            # Ya es 2D (caso predicciones)
            composite_data_2d = dbz_data
            detection_composite_2d = dbz_data
            alert_composite_2d = dbz_data
        else:
            logging.error(f"Dimensiones inesperadas en {nc_file_path}: {dbz_data.ndim}")
            return None

        # --- 2. Obtener la información de la proyección ---
        proj_info = ds['grid_mapping_0'].attrs
        lon_0 = proj_info['longitude_of_projection_origin']
        lat_0 = proj_info['latitude_of_projection_origin']
        projection = ccrs.AzimuthalEquidistant(central_longitude=lon_0, central_latitude=lat_0)

        # La carpeta puede no existir (la limpieza borra subcarpetas vacías de images/): crearla siempre
        os.makedirs(os.path.dirname(os.path.abspath(output_image_path)), exist_ok=True)

        # --- 3. Creación del gráfico transparente (TITAN Color Scale) ---
        fig = plt.figure(figsize=(10, 10), dpi=150)
        ax = fig.add_subplot(1, 1, 1)
        fig.patch.set_alpha(0)
        ax.patch.set_alpha(0)
        ax.set_axis_off()

        # Configuración de Colores TITAN
        titan_bounds = [5, 10, 20, 30, 35, 36, 39, 42, 45, 48, 51, 54, 57, 60, 65, 70, 80]
        titan_colors = [
            '#483d8b', # 5-10
            '#005a00', # 10-20
            '#007000', # 20-30
            '#087fdb', # 30-35
            '#1c47e8', # 35-36
            '#6e0dc6', # 36-39
            '#c80f86', # 39-42
            '#c06487', # 42-45
            '#d2883b', # 45-48
            '#fac431', # 48-51
            '#fefa03', # 51-54
            '#fe9a58', # 54-57
            '#fe5f05', # 57-60
            '#fd341c', # 60-65
            '#bebebe', # 65-70
            '#d3d3d3'  # 70-80
        ]
        cmap = ListedColormap(titan_colors)
        cmap.set_under('none') # Transparente por debajo de 5 dbz
        norm = BoundaryNorm(titan_bounds, cmap.N)

        # Dibujar la imagen de reflectividad SIN CONTORNOS SUAVIZADOS
        # Usamos pcolormesh para representar los píxeles (celdas) exactamente como son, 
        # sin que la interpolación de contourf reduzca su tamaño visual o cambie su forma.
        
        ax.pcolormesh(x, y, composite_data_2d, cmap=cmap, norm=norm, shading='auto')
        
        plt.tight_layout(pad=0)
        # Aumentamos DPI a 300 para que los contornos se vean nítidos en móviles retina/high-res
        plt.savefig(output_image_path, dpi=300, transparent=True, bbox_inches='tight', pad_inches=0)
        
        # --- 3.5 Creación de la versión SUAVIZADA ---
        ax.clear()
        ax.set_axis_off()
        ax.patch.set_alpha(0)
        
        # contourf suaviza los valores de la matriz entre puntos
        ax.contourf(x, y, composite_data_2d, levels=titan_bounds, colors=titan_colors, extend='max')
        plt.tight_layout(pad=0)
        
        smoothed_output_path = output_image_path.replace(".png", "_smoothed.png")
        plt.savefig(smoothed_output_path, dpi=300, transparent=True, bbox_inches='tight', pad_inches=0)
        
        plt.close(fig)

        # --- 4. Calcular Bounding Box Geográfico ---
        # El radar sur conserva los valores exactos del output de TITAN (PrintMdv):
        #   minLat, minLon: -36.8913, -70.8209 / maxLat, maxLon: -32.3798, -65.2191
        # Para cualquier otro radar se calculan desde la proyección y la grilla del scan.
        if ctx is None or ctx.is_primary:
            bounds = [[-36.8913, -70.8209], [-32.3798, -65.2191]] # Formato: [[lat_min, lon_min], [lat_max, lon_max]]
        else:
            bounds = _grid_bounds(x, y, lat_0, lon_0)

        # --- 5. Detectar Celdas y Centroides ---
        storm_cells = detect_storm_cells(detection_composite_2d, x, y, projection,
                                         dbz_3d=dbz_data if dbz_data.ndim == 3 else None, altitudes=altitudes,
                                         threshold_field=alert_composite_2d)
        
        # --- 5.5 Registrar Manga de Granizo (> 55 dBZ por encima de ALERT_MIN_LEVEL_KM) ---
        # Solo registrar si la imagen es una observación real (input), no una predicción
        if 'input' in nc_file_path.lower():
            update_hail_swath(alert_composite_2d, x, y, projection, min_dbz=55.0, ctx=ctx)
        
        # --- 6. Alertas: SOLO con scans observados (las predicciones no disparan avisos) ---
        if is_observation and ctx is not None:
            scan_time = _scan_time_from_path(nc_file_path)
            storm_cells = ctx.tracker.update(storm_cells, scan_time)
            # Rayos GLM de los últimos 10 min a menos de 15 km de cada celda (indicador de severidad)
            for cell in storm_cells:
                cell["flashes_10min"] = lightning.count_near(cell["lat"], cell["lon"], km=15.0, minutes=10)
            _save_latest_cells(ctx, storm_cells, scan_time)
            # Hasta que las alertas combinen las celdas de todos los radares (entrega 4), solo el
            # primario las dispara: dos radares evaluando por separado los mismos parques/usuarios
            # mandarían alertas y ceses cruzados en la zona de solapamiento.
            if ctx.is_primary:
                check_proximity_alerts(storm_cells)
                check_daily_engagement_alerts(storm_cells, lat_0, lon_0, ctx)
                check_solar_park_alerts(storm_cells)

        logging.info(f"  -> Imagen transparente guardada en: {output_image_path}")
        logging.info(f"  -> Coordenadas calculadas: {bounds}")
        logging.info(f"  -> Celdas detectadas: {len(storm_cells)}")
        
        return bounds, storm_cells

    except Exception as e:
        logging.error(f"No se pudo generar la imagen para {nc_file_path}: {e}", exc_info=True)
        return None

from core.database import init_db, DB_PATH

def log_prediction(timestamp, input_seq_id, output_path, status="SUCCESS"):
    """Registra una predicción en la base de datos."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO predictions (timestamp, input_sequence_id, output_path, status)
            VALUES (?, ?, ?, ?)
        ''', (timestamp.isoformat(), input_seq_id, output_path, status))
        conn.commit()
        conn.close()
        logging.info(f"Predicción registrada en DB: {input_seq_id}")
    except Exception as e:
        logging.error(f"Error al registrar en DB: {e}")


def update_status(ctx: RadarContext, status_message: str, file_count: int, total_needed: int):
    """Crea y escribe el estado actual del radar en su archivo JSON."""
    status = {
        "status": status_message,
        "files_in_buffer": file_count,
        "files_needed_for_run": total_needed,
        "last_update": datetime.now(timezone.utc).isoformat()
    }
    try:
        import tempfile
        import os
        dir_name = os.path.dirname(ctx.status_file) or "."
        os.makedirs(dir_name, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(dir=dir_name, suffix='.json')
        with os.fdopen(fd, 'w') as f:
            json.dump(status, f, indent=4)
        os.replace(temp_path, ctx.status_file)
        logging.info(f"[{ctx.radar_id}] Estado actualizado: {status_message}")
    except Exception as e:
        logging.error(f"No se pudo escribir en el archivo de estado: {e}")

def load_and_preprocess_input_sequence(input_file_paths: list, data_cfg: dict) -> torch.Tensor:
    """
    Carga secuencia, preprocesa (Max Projection 3D->2D) y hace DOWNSAMPLING (500->250).
    """
    frames = []
    min_dbz = data_cfg['min_dbz']
    max_dbz = data_cfg['max_dbz']
    target_height = 250 # Resolución del modelo
    target_width = 250

    for file_path in input_file_paths:
        try:
            with xr.open_dataset(file_path, mask_and_scale=True, decode_times=False) as ds:
                # Fallback variable name
                var_name = data_cfg.get('variable_name', 'DBZ')
                if var_name not in ds:
                    var_name = list(ds.data_vars)[0]
                data = ds[var_name].values
            
            # Convert to Tensor
            data = torch.from_numpy(data).float()
            
            # 1. Handle NaNs IMMEDIATELY (Same as training)
            data = torch.nan_to_num(data, nan=min_dbz)
            
            # 2. Dimension Reduction (Max Projection)
            while data.ndim > 2:
                if data.shape[0] == 1:
                    data = data.squeeze(0)
                else:
                    data = torch.max(data, dim=0)[0] # Max Composite
            
            # Ensure (1, H, W) -> Channel dim
            if data.ndim == 2:
                data = data.unsqueeze(0)
                
            # 3. Clip & Normalize
            data = torch.clamp(data, min=min_dbz, max=max_dbz)
            data = (data - min_dbz) / (max_dbz - min_dbz)
            
            # 4. Resize (500 -> 250)
            if data.shape[1] != target_height or data.shape[2] != target_width:
                data = torch.nn.functional.interpolate(
                    data.unsqueeze(0),
                    size=(target_height, target_width),
                    mode='bilinear',
                    align_corners=False
                ).squeeze(0)
            
            frames.append(data)
            
        except Exception as e:
            logging.error(f"Error procesando archivo {file_path}: {e}")
            # En producción, o saltamos o rellenamos con ceros. Rellenar es más seguro para no romper batch.
            frames.append(torch.zeros((1, target_height, target_width)))

    # Stack Time: (Seq, C, H, W)
    full_sequence = torch.stack(frames, dim=0)
    # Add Batch: (B, Seq, C, H, W) -> Esto lo hace el caller, pero run_inference devuelve (1, Seq, C, H, W)
    # Pipeline espera (Seq, C, H, W) porque Predictor agrega batch dimension? 
    # Revisemos predict.py: predict(input_volume). input_volume es (Z, T, H, W, C) <- ESTO ES VIEJO.
    # El nuevo modelo espera (B, T, C, H, W).
    return full_sequence.unsqueeze(0) # (1, Seq, C, H, W)

def postprocess_prediction(prediction_norm: torch.Tensor, data_cfg: dict) -> np.ndarray:
    """
    Aplica Upsampling (250->500), desnormalización y umbral físico.
    Entrada: (1, T, C, 250, 250)
    Salida: (T, C, 500, 500) numpy array
    """
    # 1. Upsampling (250 -> 500)
    b, t, c, h, w = prediction_norm.shape
    # Flatten T into B for interpolation
    pred_reshaped = prediction_norm.view(b * t, c, h, w)
    
    pred_upsampled = torch.nn.functional.interpolate(
        pred_reshaped, 
        size=(500, 500), 
        mode='bicubic', # Bicubic para mejor calidad visual
        align_corners=False
    )
    
    # Restore shape: (B, T, C, 500, 500)
    pred_upsampled = pred_upsampled.view(b, t, c, 500, 500)

    # 2. Denormalize
    output_np = pred_upsampled.cpu().numpy()
    pred_physical_raw = output_np * (data_cfg['max_dbz'] - data_cfg['min_dbz']) + data_cfg['min_dbz']
    pred_physical_clipped = np.clip(pred_physical_raw, data_cfg['min_dbz'], data_cfg['max_dbz'])
    
    pred_physical_cleaned = pred_physical_clipped.copy()

    # 3. Clean Noise
    threshold = data_cfg.get('physical_threshold_dbz', 30.0)
    pred_physical_cleaned[pred_physical_cleaned < threshold] = np.nan
    
    # Remove batch dim: (1, T, C, H, W) -> (T, C, H, W)
    # C=1 (Composite). Post-processing logic expects this.
    return pred_physical_cleaned[0]

def save_prediction_as_netcdf(output_subdir: str, pred_sequence_cleaned: np.ndarray, data_cfg: dict, start_datetime: datetime):
    # entrada: (T, C, 500, 500). C=1
    num_pred_steps, num_c, num_y, num_x = pred_sequence_cleaned.shape
    num_z = 1 # Force 1 level (Max Projection)
    
    # --- Grid Preparation ---
    x_coords = np.arange(-249.5, -249.5 + num_x * 1.0, 1.0, dtype=np.float32)
    y_coords = np.arange(-249.5, -249.5 + num_y * 1.0, 1.0, dtype=np.float32)
    z_coords = np.array([1.0], dtype=np.float32)

    proj = pyproj.Proj(
        proj="aeqd", 
        lon_0=data_cfg['sensor_longitude'], 
        lat_0=data_cfg['sensor_latitude'], 
        R=data_cfg['earth_radius_m']
    )
    x_grid_m, y_grid_m = np.meshgrid(x_coords * 1000.0, y_coords * 1000.0)
    lon0_grid, lat0_grid = proj(x_grid_m, y_grid_m, inverse=True)

    for i in range(num_pred_steps):
        lead_time_minutes = (i + 1) * data_cfg.get('prediction_interval_minutes', 3)
        forecast_dt_utc = start_datetime + timedelta(minutes=lead_time_minutes)
        
        file_ts = forecast_dt_utc.strftime("%Y%m%d_%H%M%S")
        output_filename = os.path.join(output_subdir, f"{file_ts}.nc")

        with NCDataset(output_filename, 'w', format='NETCDF3_CLASSIC') as ds_out:
            # --- Global Attributes ---
            ds_out.Conventions = "CF-1.6"
            ds_out.title = f"{data_cfg.get('radar_title', 'SAN_RAFAEL_PRED')} - Forecast t+{lead_time_minutes}min"
            ds_out.institution = "UM"
            ds_out.source = "ConvLSTM Model Prediction"
            ds_out.history = f"Created {datetime.now(timezone.utc).isoformat()} by pipeline."
            ds_out.comment = f"Forecast 2D (Max Projection). Lead time: {lead_time_minutes} min."

            # --- Dimensions ---
            ds_out.createDimension('time', None)
            ds_out.createDimension('bounds', 2)
            ds_out.createDimension('longitude', num_x)
            ds_out.createDimension('latitude', num_y)
            ds_out.createDimension('altitude', num_z)

            # --- Variables ---
            time_value = (forecast_dt_utc.replace(tzinfo=timezone.utc) - datetime(1970, 1, 1, tzinfo=timezone.utc)).total_seconds()
            time_v = ds_out.createVariable('time', 'f8', ('time',))
            time_v.standard_name = "time"; time_v.axis = "T"
            time_v.units = "seconds since 1970-01-01T00:00:00Z"
            time_v[:] = [time_value]

            x_v = ds_out.createVariable('longitude', 'f4', ('longitude',))
            x_v.standard_name = "projection_x_coordinate"; x_v.units = "km"; x_v.axis = "X"
            x_v[:] = x_coords

            y_v = ds_out.createVariable('latitude', 'f4', ('latitude',))
            y_v.standard_name = "projection_y_coordinate"; y_v.units = "km"; y_v.axis = "Y"
            y_v[:] = y_coords

            z_v = ds_out.createVariable('altitude', 'f4', ('altitude',))
            z_v.standard_name = "altitude"; z_v.units = "km"; z_v.axis = "Z"; z_v.positive = "up"
            z_v[:] = z_coords

            lat0_v = ds_out.createVariable('lat0', 'f4', ('latitude', 'longitude',))
            lat0_v.standard_name = "latitude"; lat0_v.units = "degrees_north"
            lat0_v[:] = lat0_grid

            lon0_v = ds_out.createVariable('lon0', 'f4', ('latitude', 'longitude',))
            lon0_v.standard_name = "longitude"; lon0_v.units = "degrees_east"
            lon0_v[:] = lon0_grid

            gm_v = ds_out.createVariable('grid_mapping_0', 'i4')
            gm_v.grid_mapping_name = "azimuthal_equidistant"
            gm_v.longitude_of_projection_origin = data_cfg['sensor_longitude']
            gm_v.latitude_of_projection_origin = data_cfg['sensor_latitude']
            gm_v.false_easting = 0.0; gm_v.false_northing = 0.0
            gm_v.earth_radius = data_cfg['earth_radius_m']

            # --- Data Variable (Single Level) ---
            fill_value_float = np.float32(-999.0)
            dbz_v = ds_out.createVariable('DBZ', 'f4', ('time', 'altitude', 'latitude', 'longitude'), fill_value=fill_value_float)
            dbz_v.units = "dBZ"; dbz_v.standard_name = "reflectivity"
            dbz_v.grid_mapping = "grid_mapping_0"
            dbz_v.coordinates = "lat0 lon0"
            
            # Get 2D frame: (C, H, W) -> C=1
            pred_2d = pred_sequence_cleaned[i] # (1, 500, 500)
            
            # Expand to (1, 1, Y, X) for saving
            pred_final = pred_2d[np.newaxis, :, :, :] 
            
            data_final = np.nan_to_num(pred_final, nan=fill_value_float)
            
            dbz_v[:] = data_final

        logging.info(f"  -> Predicción guardada en: {os.path.basename(output_filename)}")

def convert_mdv_to_nc(mdv_filepath: str, final_output_dir: str, params_path: str,
                      temp_work_dir: str = "/app/temp_conversion_workspace"):
    mdv_filename = os.path.basename(mdv_filepath)
    logging.info(f"Iniciando conversión de {mdv_filename}...")
    if os.path.exists(temp_work_dir):
        shutil.rmtree(temp_work_dir)
    os.makedirs(temp_work_dir)
    original_dir = os.getcwd()
    os.chdir(temp_work_dir)
    try:
        command = ["Mdv2NetCDF", "-params", params_path, "-f", os.path.abspath(mdv_filepath)]
        logging.info(f"Ejecutando comando: {' '.join(command)}")
        subprocess.run(command, check=True, capture_output=True, text=True)
        search_path = os.path.join(temp_work_dir, "netCDF", "*.nc")
        nc_files_found = glob.glob(search_path)
        if not nc_files_found:
            logging.error("Conversión reportó éxito, pero no se encontró ningún archivo .nc.")
            return False
        created_nc_path = nc_files_found[0]
        base_nc_name = os.path.basename(created_nc_path)
        try:
            parts = base_nc_name.replace('ncfdata', '').replace('.nc', '').split('_')
            final_filename = f"{parts[0]}{parts[1]}.nc"
        except IndexError:
            logging.warning(f"No se pudo parsear el nombre '{base_nc_name}'. Usando nombre original.")
            final_filename = base_nc_name
        final_nc_path = os.path.join(final_output_dir, final_filename)
        logging.info(f"Moviendo y renombrando '{base_nc_name}' a '{final_nc_path}'")
        shutil.move(created_nc_path, final_nc_path)
        logging.info("Conversión y limpieza completadas exitosamente.")
        return True
    except subprocess.CalledProcessError as e:
        logging.error(f"Falló la conversión de {mdv_filename}. Error de LROSE: {e.stderr}")
        return False
    except FileNotFoundError:
        logging.error("Error crítico: 'Mdv2NetCDF' no se encontró.")
        return False
    finally:
        os.chdir(original_dir)
        if os.path.exists(temp_work_dir):
            shutil.rmtree(temp_work_dir)

def convert_predictions_to_mdv(nc_input_dir: str, mdv_output_dir: str, params_template_path: str,
                               ctx: RadarContext = None):
    logging.info(f"Iniciando conversión de NetCDF en '{nc_input_dir}' a MDV en '{mdv_output_dir}'")
    mdv_env = os.environ.copy()
    mdv_env["MDV_WRITE_FORMAT"] = "FORMAT_MDV"
    try:
        with open(params_template_path, 'r') as f:
            template_content = f.read()
        abs_nc_input_dir = os.path.abspath(nc_input_dir)
        abs_mdv_output_dir = os.path.abspath(mdv_output_dir)
        # El origen de la proyección es el del radar (la plantilla tenía fijo San Rafael)
        origin_lat = ctx.sensor_latitude if ctx else DATA_CONFIG['sensor_latitude']
        origin_lon = ctx.sensor_longitude if ctx else DATA_CONFIG['sensor_longitude']
        final_params_content = (template_content
                                .replace("%%INPUT_DIR%%", abs_nc_input_dir)
                                .replace("%%OUTPUT_DIR%%", abs_mdv_output_dir)
                                .replace("%%ORIGIN_LAT%%", f"{origin_lat:.7f}")
                                .replace("%%ORIGIN_LON%%", f"{origin_lon:.7f}"))
        temp_params_path = ctx.temp_params_path if ctx else "/app/lrose_params/temp.params.final"
        with open(temp_params_path, 'w') as f:
            f.write(final_params_content)
    except Exception as e:
        logging.error(f"No se pudo preparar el archivo de parámetros: {e}")
        return False
    command = ["NcGeneric2Mdv", "-params", temp_params_path, "-start", "2005 01 01 00 00 00", "-end", "2030 12 31 23 59 59"]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, env=mdv_env)
        logging.info("NcGeneric2Mdv ejecutado exitosamente.")
    except subprocess.CalledProcessError as e:
        logging.error(f"Falló la ejecución de NcGeneric2Mdv. Error: {e.stderr}")
        return False
    logging.info("Renombrando archivos MDV de salida...")
    try:
        generated_files = glob.glob(os.path.join(abs_mdv_output_dir, "**", "*.mdv"), recursive=True)
        if not generated_files:
            logging.warning("NcGeneric2Mdv se ejecutó pero no se encontraron archivos .mdv en la salida.")
            return True
        nc_files = sorted(glob.glob(os.path.join(abs_nc_input_dir, "*.nc")))
        nc_timestamps = [os.path.basename(f).replace('.nc', '') for f in nc_files]
        for old_filepath in generated_files:
            filename = os.path.basename(old_filepath)
            if filename.endswith('.mdv') and '_' in filename:
                new_filename = filename
                new_filepath = os.path.join(os.path.dirname(old_filepath), new_filename)
                timestamp = filename.replace('.mdv', '')
                if timestamp in nc_timestamps:
                    logging.info(f"  Manteniendo nombre MDV: '{filename}'")
                    if old_filepath != new_filepath:
                        os.rename(old_filepath, new_filepath)
                else:
                    logging.warning(f"  MDV '{filename}' no tiene NC correspondiente. Manteniendo nombre.")
            else:
                logging.warning(f"  Formato inesperado para '{filename}'. Manteniendo nombre.")
        return True
    except Exception as e:
        logging.error(f"Ocurrió un error durante el renombrado de archivos: {e}")
        return False

def run_sequence(ctx: RadarContext, predictor, full_paths: list, seq_id: str) -> None:
    """Ciclo completo para una ventana de SECUENCE_LENGHT NC de un radar: imágenes de los últimos 3
    scans observados, predicción, NC + MDV de salida e imágenes de las predicciones.
    Lo comparten el modo normal y el modo historia. La gestión del buffer la hace quien llama."""
    # --- 1. Generar imágenes transparentes y bounds de los últimos 3 scans ---
    logging.info(f"[{ctx.radar_id}] Generando imágenes para los últimos 3 inputs...")
    for input_nc_path in full_paths[-3:]:
        input_seq_id = os.path.splitext(os.path.basename(input_nc_path))[0]
        input_image_path = os.path.join(ctx.image_dir, f"INPUT_{input_seq_id}.png")
        # Solo generar si no existe (optimización)
        if not os.path.exists(input_image_path):
            # skip_levels=3 enmascara los índices 0, 1 y 2 (clutter)
            result = generar_imagen_transparente_y_bounds(input_nc_path, input_image_path, skip_levels=3,
                                                          is_observation=True, ctx=ctx)
            if result:
                bounds, cells = result
                with open(f"{input_image_path}.json", 'w') as f:
                    json.dump({"bounds": bounds, "cells": cells}, f)
        else:
            logging.info(f"[{ctx.radar_id}] Imagen de input ya existe: {os.path.basename(input_image_path)}")

    # --- 2. Predecir ---
    input_tensor = load_and_preprocess_input_sequence(full_paths, ctx.data_config)
    prediction_tensor = predictor.predict(input_tensor)
    prediction_cleaned = postprocess_prediction(prediction_tensor, ctx.data_config)

    try:
        last_input_dt_utc = datetime.strptime(seq_id, '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
    except ValueError:
        last_input_dt_utc = datetime.now(timezone.utc)

    # --- 2.1 Detectar/validar el intervalo entre scans (lo usa el NC de salida) ---
    try:
        t1 = datetime.strptime(os.path.splitext(os.path.basename(full_paths[-2]))[0], '%Y%m%d%H%M%S')
        t2 = datetime.strptime(os.path.splitext(os.path.basename(full_paths[-1]))[0], '%Y%m%d%H%M%S')
        detected_interval = (t2 - t1).total_seconds() / 60.0
        if 2.0 <= detected_interval <= 15.0:
            ctx.data_config['prediction_interval_minutes'] = detected_interval
            logging.info(f"[{ctx.radar_id}] Intervalo detectado dinámicamente: {detected_interval:.2f} min")
        else:
            logging.warning(f"[{ctx.radar_id}] Intervalo detectado ({detected_interval}) fuera de rango. Usando config: {ctx.data_config['prediction_interval_minutes']}")
    except Exception as e:
        logging.warning(f"[{ctx.radar_id}] No se pudo detectar intervalo dinámico: {e}. Usando config: {ctx.data_config['prediction_interval_minutes']}")

    # --- 3. Guardar predicciones en NetCDF ---
    output_subdir_name = last_input_dt_utc.strftime('%Y%m%d-%H%M%S')
    output_subdir_path = os.path.join(ctx.output_dir, output_subdir_name)
    os.makedirs(output_subdir_path, exist_ok=True)
    save_prediction_as_netcdf(output_subdir_path, prediction_cleaned, ctx.data_config, last_input_dt_utc)
    log_prediction(datetime.now(timezone.utc), seq_id, output_subdir_path, "SUCCESS")

    # --- 4. Convertir predicciones a MDV (los lee TITAN) ---
    convert_predictions_to_mdv(output_subdir_path, ctx.mdv_output_dir, "/app/lrose_params/params.nc2mdv.final", ctx)

    # --- 5. Generar imágenes transparentes y bounds de las predicciones ---
    logging.info(f"[{ctx.radar_id}] Iniciando generación de imágenes de predicción...")
    for nc_file in sorted(glob.glob(os.path.join(output_subdir_path, "*.nc"))):
        pred_filename_base = os.path.splitext(os.path.basename(nc_file))[0]
        pred_image_path = os.path.join(ctx.image_dir, f"PRED_{output_subdir_name}_{pred_filename_base}.png")
        result = generar_imagen_transparente_y_bounds(nc_file, pred_image_path, skip_levels=3, ctx=ctx)
        if result:
            pred_bounds, pred_cells = result
            with open(f"{pred_image_path}.json", 'w') as f:
                json.dump({"bounds": pred_bounds, "cells": pred_cells}, f)

    logging.info(f"[{ctx.radar_id}] Ciclo de predicción para la secuencia {seq_id} completado.")


def _mdv_step(ctx: RadarContext) -> bool:
    """Convierte UN MDV pendiente de la bandeja del radar a NC. True si había uno."""
    mdv_files = sorted(f for f in os.listdir(ctx.mdv_inbox_dir) if f.endswith('.mdv'))
    if not mdv_files:
        return False
    mdv_name = mdv_files[0]
    mdv_path = os.path.join(ctx.mdv_inbox_dir, mdv_name)
    success = convert_mdv_to_nc(mdv_path, ctx.input_dir, "/app/lrose_params/Mdv2NetCDF.params", ctx.temp_work_dir)
    shutil.move(mdv_path, os.path.join(ctx.mdv_archive_dir, mdv_name))
    if success:
        logging.info(f"[{ctx.radar_id}] {mdv_name} archivado y convertido a NC.")
    else:
        logging.warning(f"[{ctx.radar_id}] {mdv_name} archivado, pero la conversión a NC falló.")
    return True


def normal_mode_step(ctx: RadarContext, predictor) -> bool:
    """Un paso del modo normal para un radar: primero vacía la bandeja de MDV (uno por paso),
    después, si la ventana está completa, corre el ciclo y desliza la ventana. True si hizo algo."""
    if _mdv_step(ctx):
        return True
    input_files = sorted(f for f in os.listdir(ctx.input_dir) if f.endswith('.nc'))
    if len(input_files) < SECUENCE_LENGHT:
        update_status(ctx, "IDLE - Esperando archivos NC", len(input_files), SECUENCE_LENGHT)
        return False
    files_to_process = input_files[-SECUENCE_LENGHT:]
    full_paths = [os.path.join(ctx.input_dir, f) for f in files_to_process]
    seq_id = os.path.splitext(files_to_process[-1])[0]
    update_status(ctx, f"Procesando secuencia terminada en {seq_id}", len(files_to_process), SECUENCE_LENGHT)
    run_sequence(ctx, predictor, full_paths, seq_id)
    # Ventana deslizante: archivar el scan más viejo para esperar nuevos
    oldest = files_to_process[0]
    logging.info(f"[{ctx.radar_id}] Ventana deslizante: archivando '{oldest}' para esperar nuevos escaneos.")
    shutil.move(os.path.join(ctx.input_dir, oldest), os.path.join(ctx.archive_dir, oldest))
    return True


def story_mode_step(ctx: RadarContext, predictor, status_obj: dict) -> None:
    """STORY MODE v2: alimentación secuencial por posición (solo el radar primario)."""
    from core.story_manager import story_state
    conversion_status = status_obj.get("conversion_status", "idle")

    if conversion_status == "converting":
        update_status(ctx, "STORY: Convirtiendo MDV → NC...", 0, SECUENCE_LENGHT)
        time.sleep(5)
        return
    if conversion_status == "error":
        error_msg = status_obj.get("conversion_error", "Error desconocido")
        update_status(ctx, f"STORY ERROR: {error_msg}", 0, SECUENCE_LENGHT)
        time.sleep(10)
        return
    if conversion_status != "ready":
        update_status(ctx, "STORY: Esperando conversión...", 0, SECUENCE_LENGHT)
        time.sleep(2)
        return

    current_idx = status_obj.get("current_index", 0)
    total = status_obj.get("total_frames", 0)
    if not story_state.should_advance():
        progress = status_obj.get("progress_percent", 0)
        update_status(ctx, f"STORY: Esperando próximo frame ({current_idx+1}/{total}, {progress}%)", current_idx + 1, total)
        time.sleep(2)
        return

    full_paths = story_state.get_current_nc_window()
    if len(full_paths) < SECUENCE_LENGHT:
        update_status(ctx, f"STORY: Ventana incompleta ({len(full_paths)}/{SECUENCE_LENGHT})", len(full_paths), SECUENCE_LENGHT)
        if story_state.advance_position() is None:
            update_status(ctx, "STORY: Finalizado", 0, 0)
        time.sleep(2)
        return

    seq_id = os.path.splitext(os.path.basename(full_paths[-1]))[0]
    update_status(ctx, f"STORY: Procesando frame {current_idx+1}/{total} ({seq_id})", current_idx + 1, total)
    run_sequence(ctx, predictor, full_paths, seq_id)

    if story_state.advance_position() is None:
        update_status(ctx, "STORY: Finalizado — todas las imágenes procesadas", 0, 0)
    else:
        logging.info(f"STORY MODE: Frame procesado. Siguiente en {STORY_FEED_INTERVAL_SECONDS}s.")
    time.sleep(1)


def main():
    logging.info("====== INICIO DEL WORKER DEL PIPELINE (v12 - Multi-radar) ======")
    contexts = get_radar_contexts()
    for ctx in contexts:
        ctx.ensure_dirs()
    primary = contexts[0]
    logging.info("Radares atendidos: " + ", ".join(str(c) for c in contexts))

    init_db()
    predictor = ModelPredictor(MODEL_PATH)
    lightning.start_background()   # rayos GLM (GOES-19) en hilo aparte; ver services/lightning.py

    last_periodic_check = 0
    last_cleanup_check = 0

    while True:
        try:
            # --- Tareas periódicas (cada 60 s) ---
            now_ts = time.time()
            if now_ts - last_periodic_check >= 60:
                check_periodic_cleanup()
                check_daily_forecast_alert()
                telegram_service.process_updates(DB_PATH, TELEGRAM_OFFSET_FILE)  # /start <token>, /stop
                last_periodic_check = now_ts

            # --- Limpieza de NC del modo historia (cada 5 min) ---
            from core.story_manager import story_state
            if now_ts - last_cleanup_check >= 300:
                story_state.cleanup_expired_nc()
                last_cleanup_check = now_ts

            status_obj = story_state.get_status()
            is_story_mode = status_obj.get("is_active", False)
            is_worker_paused = status_obj.get("worker_paused", False)

            # Defensivo: reanudar si el modo historia no está activo pero quedó la pausa
            if not is_story_mode and is_worker_paused:
                logging.warning("[Worker] Story mode inactive but worker_paused=True. Auto-resuming.")
                story_state.resume_worker()
                is_worker_paused = False

            # ============================================================
            # STORY MODE — solo el radar primario; los demás esperan
            # ============================================================
            if is_story_mode:
                story_mode_step(primary, predictor, status_obj)
                continue

            # ============================================================
            # NORMAL MODE — un paso por radar, en serie
            # ============================================================
            if is_worker_paused:
                for ctx in contexts:
                    update_status(ctx, "WORKER PAUSADO (Modo Historia)", 0, SECUENCE_LENGHT)
                time.sleep(POLL_INTERVAL_SECONDS)
                continue

            did_work = False
            for ctx in contexts:
                try:
                    did_work = normal_mode_step(ctx, predictor) or did_work
                except Exception as e:
                    update_status(ctx, "ERROR - ver logs para detalles", -1, -1)
                    logging.error(f"[{ctx.radar_id}] Error en el ciclo: {e}", exc_info=True)
            time.sleep(1 if did_work else POLL_INTERVAL_SECONDS)

        except Exception as e:
            update_status(primary, "ERROR - ver logs para detalles", -1, -1)
            logging.error(f"Ocurrió un error en el bucle principal: {e}", exc_info=True)
            time.sleep(POLL_INTERVAL_SECONDS * 2)


if __name__ == "__main__":
    main()
