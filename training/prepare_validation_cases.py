#!/usr/bin/env python3
"""
Prepara, fuera del contenedor, los datos de las secuencias de validación (CASES de evaluate_cases.py).

  convert : MDV (stories_mdv/<story>/HHMMSS.mdv) -> NetCDF con LROSE Mdv2NetCDF, con el mismo
            .params y el mismo nombre (<story><HHMMSS>.nc) que usa StoryManager._batch_convert_mdv_to_nc.
  predict : corre el modelo de producción sobre la ventana de 8 frames que termina en el último
            frame de entrada de cada caso y guarda los 7 NC de predicción igual que el worker
            (output_predictions/<pred_subdir>/*.nc).

Después: python evaluate_cases.py / python baseline_persistencia.py apuntando a data/validation_cases.

`predict` importa backend/core/config.py, que exige SECRET_KEY e INGEST_SECRET_KEY aunque acá no se
usen. Pasarlas descartables:
  SECRET_KEY=$(openssl rand -hex 32) INGEST_SECRET_KEY=$(openssl rand -hex 32) \
      python training/prepare_validation_cases.py predict

Las funciones load_and_preprocess_input_sequence, postprocess_prediction y save_prediction_as_netcdf
están COPIADAS TEXTUALMENTE de backend/worker/main.py (ese módulo arranca hilos y servicios al
importarse, no se puede importar desde acá). Si cambian en el worker, volver a copiarlas.
"""
import argparse
import glob
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pyproj
import torch
import xarray as xr
from netCDF4 import Dataset as NCDataset

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(HERE))

from evaluate_cases import CASES, HORIZONS  # noqa: E402

LROSE_PARAMS = REPO / "backend" / "lrose_params" / "Mdv2NetCDF.params"
DEFAULT_ROOT = REPO / "data" / "validation_cases"
DEFAULT_MODEL = REPO / "training" / "trained_models" / "phase3_refinement_best.pth"


# =============================================================================
# Copiado textualmente de backend/worker/main.py (load_and_preprocess_input_sequence,
# postprocess_prediction, save_prediction_as_netcdf). NO EDITAR ACÁ.
# =============================================================================
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


# =============================================================================
# 1. convert
# =============================================================================
def _find_mdv2netcdf():
    exe = shutil.which("Mdv2NetCDF") or "/usr/local/lrose/bin/Mdv2NetCDF"
    if not os.path.exists(exe):
        sys.exit("No encuentro Mdv2NetCDF (LROSE). Instalalo o agregá /usr/local/lrose/bin al PATH.")
    return exe


def convert_one(exe, params, mdv_path: Path, nc_path: Path):
    if nc_path.exists():
        return "skip"
    work = tempfile.mkdtemp(prefix="val_conv_")
    try:
        subprocess.run([exe, "-params", str(params), "-f", str(mdv_path.resolve())],
                       check=True, capture_output=True, text=True, timeout=120, cwd=work)
        found = (glob.glob(os.path.join(work, "netCDF", "**", "*.nc"), recursive=True)
                 or glob.glob(os.path.join(work, "netCDF", "*.nc")))
        if not found:
            return "sin salida"
        nc_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(found[0], nc_path)
        return "ok"
    except subprocess.CalledProcessError as e:
        return f"error LROSE: {e.stderr[:200]}"
    except subprocess.TimeoutExpired:
        return "timeout"
    finally:
        shutil.rmtree(work, ignore_errors=True)


def cmd_convert(args):
    exe = _find_mdv2netcdf()
    stories = args.story or sorted({c["story_id"] for cs in CASES.values() for c in cs})
    jobs = []
    for story in stories:
        mdv_dir = Path(args.mdv_root) / story
        if not mdv_dir.is_dir():
            logging.warning("No existe %s — omitida", mdv_dir)
            continue
        for mdv in sorted(mdv_dir.glob("*.mdv")):
            # MDV: HHMMSS.mdv -> NC: YYYYMMDDHHMMSS.nc (igual que StoryManager)
            jobs.append((mdv, Path(args.nc_root) / story / f"{story}{mdv.stem}.nc"))
    logging.info("Convirtiendo %d MDV con %d procesos en paralelo…", len(jobs), args.jobs)
    counts = {}
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        for (mdv, _), res in zip(jobs, ex.map(lambda j: convert_one(exe, args.params, *j), jobs)):
            key = res.split(":")[0]
            counts[key] = counts.get(key, 0) + 1
            if res not in ("ok", "skip"):
                logging.error("%s: %s", mdv, res)
    logging.info("Resultado: %s", counts)
    for story in stories:
        logging.info("  %s: %d NC", story, len(list((Path(args.nc_root) / story).glob("*.nc"))))


# =============================================================================
# 2. predict
# =============================================================================
def cmd_predict(args):
    from core.config import DATA_CONFIG, SECUENCE_LENGHT  # noqa: E402  (rutas /app no se usan acá)
    from model.predict import ModelPredictor              # noqa: E402

    torch.set_num_threads(args.threads)
    predictor = ModelPredictor(args.model)
    for tipo, secuencias in CASES.items():
        for seq in secuencias:
            story, sub = seq["story_id"], seq["pred_subdir"]
            last_ts = sub.replace("-", "")
            all_nc = sorted((Path(args.nc_root) / story).glob("*.nc"))
            inputs = [f for f in all_nc if f.stem <= last_ts][-SECUENCE_LENGHT:]
            after = [f for f in all_nc if f.stem > last_ts]
            if len(inputs) != SECUENCE_LENGHT or inputs[-1].stem != last_ts:
                logging.error("[%s/%s] ventana de entrada incompleta (%d frames) o último frame distinto (%s) — omitida",
                              tipo, story, len(inputs), inputs[-1].stem if inputs else None)
                continue
            if len(after) < HORIZONS:
                logging.warning("[%s/%s] solo %d frames observados después de %s (hacen falta %d)",
                                tipo, story, len(after), last_ts, HORIZONS)
            out_dir = Path(args.pred_root) / sub
            if out_dir.is_dir() and len(list(out_dir.glob("*.nc"))) == HORIZONS and not args.force:
                logging.info("[%s/%s] %s ya existe — omitida (usar --force para rehacer)", tipo, story, sub)
                continue
            out_dir.mkdir(parents=True, exist_ok=True)
            for old in out_dir.glob("*.nc"):
                old.unlink()

            # Igual que run_sequence(): el intervalo entre los dos últimos scans de entrada
            data_cfg = dict(DATA_CONFIG)
            t1 = datetime.strptime(inputs[-2].stem, "%Y%m%d%H%M%S")
            t2 = datetime.strptime(inputs[-1].stem, "%Y%m%d%H%M%S")
            interval = (t2 - t1).total_seconds() / 60.0
            if 2.0 <= interval <= 15.0:
                data_cfg["prediction_interval_minutes"] = interval

            t0 = time.time()
            x = load_and_preprocess_input_sequence([str(p) for p in inputs], data_cfg)
            pred = predictor.predict(x)
            cleaned = postprocess_prediction(pred, data_cfg)
            last_dt = datetime.strptime(last_ts, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            save_prediction_as_netcdf(str(out_dir), cleaned, data_cfg, last_dt)
            logging.info("[%s/%s] %s listo en %.0f s", tipo, story, sub, time.time() - t0)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    c = sp.add_parser("convert", help="MDV -> NetCDF con LROSE")
    c.add_argument("--mdv-root", default=str(DEFAULT_ROOT / "stories_mdv"))
    c.add_argument("--nc-root", default=str(DEFAULT_ROOT / "stories_nc"))
    c.add_argument("--params", default=str(LROSE_PARAMS))
    c.add_argument("--story", nargs="*", help="story_ids a convertir (default: los de CASES)")
    c.add_argument("--jobs", type=int, default=4)
    c.set_defaults(fn=cmd_convert)

    p = sp.add_parser("predict", help="re-inferencia del modelo sobre los casos de CASES")
    p.add_argument("--nc-root", default=str(DEFAULT_ROOT / "stories_nc"))
    p.add_argument("--pred-root", default=str(DEFAULT_ROOT / "output_predictions"))
    p.add_argument("--model", default=str(DEFAULT_MODEL))
    p.add_argument("--threads", type=int, default=8)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_predict)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
