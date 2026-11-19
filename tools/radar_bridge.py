#!/usr/bin/env python3
"""
Radar Bridge: sube al VPS los MDV que TITAN va dejando en la PC del radar.

Reemplaza (o complementa) al Drive Watcher: en vez de depender de una carpeta de
Drive compartida, la PC del trabajo empuja cada scan nuevo a /api/upload_mdv con
la clave compartida INGEST_SECRET_KEY, igual que el streamer de telemetría.

    python3 radar_bridge.py --folder /home/titan5/projDir/data/mdv/radar/cart_no_clutter \
                            --api https://vps-api.hail-cast-mendoza.com --radar sur

Estructura esperada: <folder>/AAAAMMDD/HHMMSS.mdv (carpetas por día UTC, como TITAN).
Cada archivo se sube como AAAAMMDD_HHMMSS.mdv para que no colisionen dos días.

Comportamiento:
  - Sondeo cada --interval segundos de las carpetas de hoy y ayer (UTC).
  - Un archivo se sube recién cuando dejó de crecer (tamaño estable y > 3 s sin modificar).
  - Registro de enviados en --ledger (JSON): sobrevive reinicios y no repite subidas.
  - Al arrancar, lo más viejo que --catchup-minutes se marca como visto sin subirlo
    (no inunda al VPS con historia); si quedan más de --max-batch pendientes, sube
    solo los últimos (igual que el Drive Watcher).
  - Si el VPS no responde, reintenta con espera creciente (hasta 5 min).

Solo depende de `requests`. Compatible con Python 3.5 (la VM del radar norte): sin f-strings.
"""
import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

# En la VM del radar norte (Python 3.5, locale sin UTF-8) los emojis del log romperían la
# salida: se fuerza UTF-8 reemplazando lo que no se pueda codificar.
try:
    sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", errors="replace", buffering=1)
except Exception:
    pass
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("RadarBridge")

DAY_RE = re.compile(r"^\d{8}$")
MDV_RE = re.compile(r"^\d{6}\.mdv$")
STABLE_SECONDS = 3
LEDGER_MAX = 5000
HEARTBEAT_SECONDS = 600


class Ledger:
    """Archivos ya enviados (o saltados), persistidos en JSON."""

    def __init__(self, path):
        self.path = path
        self.sent = []
        self._set = set()
        try:
            with open(path, encoding="utf-8") as f:
                self.sent = list(json.load(f).get("sent", []))
                self._set = set(self.sent)
            logger.info("Registro cargado: {0} archivos ya enviados ({1})".format(len(self.sent), path))
        except FileNotFoundError:
            logger.info("Sin registro previo; se crea {0}".format(path))
        except Exception as e:
            logger.warning("Registro ilegible ({0}); se arranca vacío".format(e))

    def __contains__(self, key):
        return key in self._set

    def add(self, key):
        if key in self._set:
            return
        self.sent.append(key)
        self._set.add(key)
        if len(self.sent) > LEDGER_MAX:
            drop = self.sent[:-LEDGER_MAX]
            self.sent = self.sent[-LEDGER_MAX:]
            self._set.difference_update(drop)

    def save(self):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"sent": self.sent, "updated": datetime.now(timezone.utc).isoformat()}, f)
            os.replace(tmp, self.path)
        except Exception as e:
            logger.warning("No se pudo guardar el registro: {0}".format(e))


def day_folders(base):
    """Carpetas de hoy y ayer (UTC) que existan bajo base, más viejas primero."""
    now = datetime.now(timezone.utc)
    out = []
    for d in (now - timedelta(days=1), now):
        name = d.strftime("%Y%m%d")
        path = os.path.join(base, name)
        if os.path.isdir(path):
            out.append((name, path))
    return out


def scan_candidates(base):
    """[(clave_de_subida, ruta, tamaño, mtime)] de todos los MDV en las carpetas del día."""
    found = []
    for day, path in day_folders(base):
        try:
            names = os.listdir(path)
        except OSError as e:
            logger.warning("No se pudo listar {0}: {1}".format(path, e))
            continue
        for name in names:
            if not MDV_RE.match(name):
                continue
            full = os.path.join(path, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            found.append(("{0}_{1}".format(day, name), full, st.st_size, st.st_mtime))
    found.sort()
    return found


class Uploader:
    def __init__(self, api_base, radar, ingest_key, timeout=60, use_proxy=True):
        self.url = api_base.rstrip("/") + "/api/upload_mdv"
        self.radar = radar
        self.headers = {"X-Ingest-Key": ingest_key}
        self.timeout = timeout
        self.session = requests.Session()
        # Con --no-proxy se ignoran http_proxy/https_proxy del sistema (en la VM del radar
        # norte están mal configuradas y la salida directa funciona).
        self.session.trust_env = use_proxy

    def upload(self, key, path):
        """True si el VPS lo aceptó (201) o ya lo tenía (200). False para reintentar. None si la clave es inválida."""
        try:
            with open(path, "rb") as f:
                r = self.session.post(self.url, headers=self.headers, timeout=self.timeout,
                                  data={"radar": self.radar, "original_name": os.path.basename(path)},
                                  files={"file": (key, f, "application/octet-stream")})
        except requests.RequestException as e:
            logger.error("❌ Sin respuesta del VPS para {0}: {1}".format(key, e))
            return False
        if r.status_code in (200, 201):
            logger.info("✅ {0} ({1} kB) -> {2}".format(key, os.path.getsize(path) // 1024, r.status_code))
            return True
        if r.status_code == 401:
            logger.error("❌ El VPS rechazó la clave (401). Revisar INGEST_SECRET_KEY en el servicio.")
            return None
        logger.error("❌ {0}: HTTP {1} {2}".format(key, r.status_code, r.text[:200]))
        return False


def main():
    ap = argparse.ArgumentParser(description="Radar Bridge: sube MDV nuevos de TITAN al VPS")
    ap.add_argument("--folder", required=True, help="Base con carpetas AAAAMMDD (ej. .../mdv/radar/cart_no_clutter)")
    ap.add_argument("--api", required=True, help="URL base de la API (ej. https://vps-api.hail-cast-mendoza.com)")
    ap.add_argument("--radar", default="sur", help="Etiqueta del radar (sur, norte). Default: sur")
    ap.add_argument("--interval", type=int, default=10, help="Segundos entre sondeos (default 10)")
    ap.add_argument("--ledger", default=None, help="JSON de enviados (default: <folder>/../radar_bridge_<radar>.json)")
    ap.add_argument("--catchup-minutes", type=int, default=30, help="Al arrancar, no subir lo más viejo que esto (default 30)")
    ap.add_argument("--max-batch", type=int, default=8, help="Máximo de pendientes a subir por ciclo; el resto se salta (default 8)")
    ap.add_argument("--no-proxy", action="store_true", help="Ignorar http_proxy/https_proxy del sistema (salida directa)")
    args = ap.parse_args()

    ingest_key = os.environ.get("INGEST_SECRET_KEY", "").strip()
    if not ingest_key:
        logger.error("INGEST_SECRET_KEY no seteada: el VPS va a rechazar las subidas. Definila en el servicio.")
        sys.exit(2)
    if not os.path.isdir(args.folder):
        logger.error("La carpeta {0} no existe.".format(args.folder))
        sys.exit(2)

    ledger_path = args.ledger or os.path.join(os.path.dirname(os.path.abspath(args.folder)), "radar_bridge_{0}.json".format(args.radar))
    ledger = Ledger(ledger_path)
    uploader = Uploader(args.api, args.radar, ingest_key, use_proxy=not args.no_proxy)
    logger.info("📡 Radar Bridge [{0}] vigilando {1} -> {2} (cada {3}s)".format(args.radar, args.folder, uploader.url, args.interval))

    # Arranque: lo viejo se marca como visto, no se sube (el VPS solo necesita estar al día).
    cutoff = time.time() - args.catchup_minutes * 60
    old = [c for c in scan_candidates(args.folder) if c[3] < cutoff and c[0] not in ledger]
    for key, *_ in old:
        ledger.add(key)
    if old:
        ledger.save()
        logger.info("⏭️ {0} archivos de más de {1} min marcados como vistos sin subir".format(len(old), args.catchup_minutes))

    last_sizes = {}
    failures = 0
    sent_total = 0
    last_heartbeat = time.time()

    while True:
        try:
            candidates = scan_candidates(args.folder)
            now = time.time()
            ready = []
            for key, path, size, mtime in candidates:
                if key in ledger:
                    continue
                stable = last_sizes.get(key) == size and now - mtime >= STABLE_SECONDS
                last_sizes[key] = size
                if stable and size > 0:
                    ready.append((key, path))
            # Limpiar tamaños de archivos que ya no están
            current_keys = {c[0] for c in candidates}
            for k in list(last_sizes):
                if k not in current_keys:
                    del last_sizes[k]

            if len(ready) > args.max_batch:
                skipped = ready[:-args.max_batch]
                ready = ready[-args.max_batch:]
                for key, _ in skipped:
                    ledger.add(key)
                ledger.save()
                logger.warning("⚠️ {0} pendientes antiguos saltados para estar en vivo ({1} ... {2})".format(len(skipped), skipped[0][0], skipped[-1][0]))

            for key, path in ready:
                result = uploader.upload(key, path)
                if result:
                    ledger.add(key)
                    ledger.save()
                    sent_total += 1
                    failures = 0
                elif result is None:
                    failures = max(failures, 4)  # clave inválida: esperar bastante antes de insistir
                    break
                else:
                    failures += 1
                    break  # mantener el orden: no saltar al siguiente si este falló

            if time.time() - last_heartbeat >= HEARTBEAT_SECONDS:
                logger.info("💓 Activo. Enviados en esta sesión: {0}. Pendientes: {1}".format(sent_total, len(ready)))
                last_heartbeat = time.time()
        except Exception as e:
            logger.error("Error en el ciclo: {0}".format(e))
            failures += 1

        delay = min(args.interval * (2 ** min(failures, 5)), 300) if failures else args.interval
        time.sleep(delay)


if __name__ == "__main__":
    main()
