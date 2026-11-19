# training/evaluate_cases.py
"""
Métricas del modelo HailCast sobre las secuencias de validación (CASES).

Para cada caso toma los 7 NC de predicción del worker (output_predictions/<pred_subdir>/)
y los 7 frames observados inmediatamente posteriores al último frame de entrada
(stories_nc_cache/<story_id>/), y calcula MAE (dBZ), SSIM y CSI a 35 y 55 dBZ por horizonte.

Las funciones de este módulo (load_dbz, compute_csi, compute_metrics, iter_cases) son LA
definición de las métricas del proyecto: baseline_persistencia.py las importa para que el
modelo y la persistencia se midan con exactamente el mismo código.

Uso:
    python evaluate_cases.py                       # rutas del contenedor (/app/data/...)
    python evaluate_cases.py --stories-nc DIR --predictions DIR --out validation_metrics.csv
"""
import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import xarray as xr
from pytorch_msssim import ssim

MIN_DBZ, MAX_DBZ = -29.0, 65.0
CSI_THRESHOLDS = {"CSI_35dBZ": 35.0, "CSI_55dBZ": 55.0}
HORIZONS = 7

# Rutas por defecto dentro del contenedor. Fuera de él, pasarlas por línea de comandos
# (prepare_validation_cases.py las deja en data/validation_cases/).
STORIES_NC_CACHE = Path("/app/data/stories_nc_cache")
OUTPUT_PREDICTIONS = Path("/app/data/output_predictions")

# ---- CONFIGURAR ANTES DE CORRER ----
# Cada tipo acepta una lista de secuencias (story_id + pred_subdir).
# pred_subdir es YYYYMMDD-HHMMSS = timestamp del último frame de entrada.
CASES = {
    "multicelda": [
        {"story_id": "20251211", "pred_subdir": "20251211-033818"},
        {"story_id": "20251213", "pred_subdir": "20251213-230638"},
        {"story_id": "20251219", "pred_subdir": "20251219-220704"},
        {"story_id": "20251221", "pred_subdir": "20251221-012431"},
        {"story_id": "20251222", "pred_subdir": "20251222-215700"},
    ],
    "supercelda": [
        {"story_id": "20251204", "pred_subdir": "20251204-205445"},
        {"story_id": "20251226", "pred_subdir": "20251226-182621"},
        {"story_id": "20251214", "pred_subdir": "20251214-003257"},
        {"story_id": "20251229", "pred_subdir": "20251229-215525"},
        {"story_id": "20260108", "pred_subdir": "20260108-032734"},
    ],
    "adveccion": [
        {"story_id": "20260106", "pred_subdir": "20260106-174252"},
        {"story_id": "20251210", "pred_subdir": "20251210-201602"},
        {"story_id": "20260110", "pred_subdir": "20260110-191629"},
        {"story_id": "20260111", "pred_subdir": "20260111-231927"},
        {"story_id": "20260113", "pred_subdir": "20260113-030142"},
    ],
}
# ------------------------------------


def load_dbz(nc_path):
    """Carga un NC (observado o predicho) como composite 2D en dBZ.
    NaN / _FillValue -> MIN_DBZ, máximo sobre niveles, recorte a [MIN_DBZ, MAX_DBZ]."""
    with xr.open_dataset(nc_path, decode_times=False) as ds:
        var = 'DBZ' if 'DBZ' in ds else list(ds.data_vars)[0]
        data = ds[var].values
        data = np.nan_to_num(data, nan=MIN_DBZ)
        while data.ndim > 2:
            data = data.max(axis=0) if data.shape[0] > 1 else data.squeeze(0)
        return np.clip(data, MIN_DBZ, MAX_DBZ)


def compute_csi(pred_dbz, truth_dbz, threshold):
    """CSI = TP / (TP + FP + FN) con operador >= sobre el umbral. Acepta arrays 2D o apilados."""
    pred_mask  = pred_dbz  >= threshold
    truth_mask = truth_dbz >= threshold
    tp = np.sum( pred_mask &  truth_mask)
    fp = np.sum( pred_mask & ~truth_mask)
    fn = np.sum(~pred_mask &  truth_mask)
    return float(tp / (tp + fp + fn)) if (tp + fp + fn) > 0 else float('nan')


def compute_metrics(pred_dbz, truth_dbz):
    """MAE en dBZ, SSIM sobre el campo normalizado a [0, 1] (pytorch_msssim) y CSI por umbral."""
    mae = float(np.mean(np.abs(pred_dbz - truth_dbz)))
    pred_n = (pred_dbz - MIN_DBZ) / (MAX_DBZ - MIN_DBZ)
    truth_n = (truth_dbz - MIN_DBZ) / (MAX_DBZ - MIN_DBZ)
    pt = torch.from_numpy(np.ascontiguousarray(pred_n)).float().unsqueeze(0).unsqueeze(0)
    tt = torch.from_numpy(np.ascontiguousarray(truth_n)).float().unsqueeze(0).unsqueeze(0)
    ssim_val = float(ssim(pt, tt, data_range=1.0, size_average=True))
    csi = {name: compute_csi(pred_dbz, truth_dbz, thr) for name, thr in CSI_THRESHOLDS.items()}
    return {"MAE_dBZ": mae, "SSIM": ssim_val, **csi}


def iter_cases(stories_nc, predictions, horizons=HORIZONS, cases=CASES):
    """Recorre CASES y devuelve, por caso, los archivos ya emparejados.

    Yields dicts con: tipo, story_id, pred_subdir, last_input_ts,
      input_files (todos los NC observados hasta el último frame de entrada inclusive),
      truth_files (los `horizons` observados siguientes), pred_files (los NC del modelo).
    Los casos incompletos se informan y se omiten, igual que hacía el script original.
    """
    stories_nc, predictions = Path(stories_nc), Path(predictions)
    for tipo, secuencias in cases.items():
        for seq in secuencias:
            pred_dir  = predictions / seq["pred_subdir"]
            truth_dir = stories_nc / seq["story_id"]
            last_input_ts = seq["pred_subdir"].replace("-", "")  # "20251211033818"
            all_truth = sorted(truth_dir.glob("*.nc"))
            pred_files  = sorted(pred_dir.glob("*.nc"))
            input_files = [f for f in all_truth if f.stem <= last_input_ts]
            truth_files = [f for f in all_truth if f.stem > last_input_ts][:horizons]
            if len(pred_files) != horizons or len(truth_files) != horizons or not input_files:
                print(f"[{tipo} / {seq['story_id']}] ADVERTENCIA: pred={len(pred_files)} "
                      f"truth={len(truth_files)} inputs={len(input_files)} — omitida")
                continue
            if input_files[-1].stem != last_input_ts:
                print(f"[{tipo} / {seq['story_id']}] ADVERTENCIA: no existe el frame de entrada "
                      f"{last_input_ts}.nc (último disponible {input_files[-1].stem}) — omitida")
                continue
            yield {"tipo": tipo, "story_id": seq["story_id"], "pred_subdir": seq["pred_subdir"],
                   "last_input_ts": last_input_ts, "input_files": input_files,
                   "truth_files": truth_files, "pred_files": pred_files}


def evaluate(stories_nc=STORIES_NC_CACHE, predictions=OUTPUT_PREDICTIONS, out_csv="validation_metrics.csv"):
    """Media y desvío por (tipo, horizonte) de las métricas del modelo. Devuelve el DataFrame."""
    accum = defaultdict(list)   # key: (tipo, horizonte_k) → list of metric dicts
    for case in iter_cases(stories_nc, predictions):
        tipo = case["tipo"]
        for k, (pp, tp) in enumerate(zip(case["pred_files"], case["truth_files"]), start=1):
            m = compute_metrics(load_dbz(pp), load_dbz(tp))
            accum[(tipo, k)].append(m)
            print(f"  {tipo} {case['story_id']} t+{k}: MAE={m['MAE_dBZ']:.2f}  SSIM={m['SSIM']:.4f}  "
                  f"CSI35={m['CSI_35dBZ']:.4f}  CSI55={m['CSI_55dBZ']:.4f}")

    metric_keys = ["MAE_dBZ", "SSIM"] + list(CSI_THRESHOLDS.keys())
    rows = []
    for (tipo, k), samples in sorted(accum.items()):
        row = {"caso": tipo, "horizonte": f"t+{k}", "n_secuencias": len(samples)}
        for key in metric_keys:
            vals = [s[key] for s in samples if not np.isnan(s[key])]
            row[key]            = round(float(np.mean(vals)), 4) if vals else float('nan')
            row[f"{key}_std"]   = round(float(np.std(vals)),  4) if len(vals) > 1 else float('nan')
        rows.append(row)

    df = pd.DataFrame(rows)
    df.to_csv(out_csv, index=False)

    print("\n=== MEDIAS POR HORIZONTE ===")
    print(df.pivot(index="horizonte", columns="caso", values=metric_keys).to_string())
    print("\n=== DESVIACIONES ESTÁNDAR ===")
    std_cols = [f"{k}_std" for k in metric_keys]
    print(df.pivot(index="horizonte", columns="caso", values=std_cols).to_string())
    return df


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stories-nc", default=str(STORIES_NC_CACHE), help="carpeta con <story_id>/<YYYYMMDDHHMMSS>.nc observados")
    ap.add_argument("--predictions", default=str(OUTPUT_PREDICTIONS), help="carpeta con <pred_subdir>/*.nc del modelo")
    ap.add_argument("--out", default="validation_metrics.csv")
    args = ap.parse_args()
    evaluate(args.stories_nc, args.predictions, args.out)


if __name__ == "__main__":
    main()
