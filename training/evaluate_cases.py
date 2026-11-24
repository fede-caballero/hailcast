# training/evaluate_cases.py
"""
Métricas del modelo HailCast sobre las secuencias de validación (CASES).

Para cada caso toma los 7 NC de predicción del worker (output_predictions/<pred_subdir>/)
y los 7 frames observados inmediatamente posteriores al último frame de entrada
(stories_nc_cache/<story_id>/), y calcula MAE (dBZ), SSIM y CSI a 35 y 55 dBZ por horizonte.

Las funciones de este módulo (load_dbz, compute_csi, compute_metrics, iter_cases) son LA
definición de las métricas del proyecto: baseline_persistencia.py las importa para que el
modelo y la persistencia se midan con exactamente el mismo código.

Los casos salen de CASES (lista fija) o, con --cases, de un manifiesto CSV generado por
`prepare_validation_cases.py ingest` o directamente de una carpeta de casos clasificados por
tipología (ver discover_cases): <raíz>/<tipologia>/.../<caso>/<HHMMSS>.mdv, donde <caso> es
YYYYMMDD-HHMMSS (HHMMSS = último scan de entrada) o cualquier nombre si tiene exactamente 15 scans (la
fecha se lee de la cabecera MDV y el corte es el 8.º scan). Tipologías admitidas con mayúsculas y acentos.

Uso:
    python evaluate_cases.py                       # rutas del contenedor (/app/data/...)
    python evaluate_cases.py --stories-nc DIR --predictions DIR --out validation_metrics.csv
    python evaluate_cases.py --cases training/results/baseline_ampliado/casos.csv ...
"""
import argparse
import csv
import re
import struct
import unicodedata
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
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

# Criterio de selección (training/results/baseline_ampliado/criterio_seleccion.md), verificado
# automáticamente por discover_cases / check_case para cada caso.
TIPOLOGIAS = ("multicelda", "supercelda", "adveccion", "decaimiento", "debil")
N_INPUT = 8                 # frames de entrada (= SECUENCE_LENGHT del worker)
MAX_GAP_MIN = 6.0           # separación máxima admitida entre scans consecutivos de la ventana
DBZ_CELDA = 45.0            # sin ninguna celda >= 45 dBZ en la entrada -> tipología "debil"

_RE_CASE = re.compile(r"^(\d{8})(?:[-_](\d{6}))?$")        # YYYYMMDD o YYYYMMDD-HHMMSS
_RE_SCAN = re.compile(r"(?:^|\D)(\d{6})\.mdv$")             # HHMMSS.mdv (admite prefijos)
_TIPO_ALIAS = {"advección": "adveccion", "débil": "debil"}


def norm_tipo(name):
    """'Advección' -> 'adveccion', 'Débil' -> 'debil' (minúsculas, sin acentos ni espacios)."""
    n = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode().lower().strip()
    return _TIPO_ALIAS.get(n, n)


def mdv_centroid(blob):
    """Fecha/hora (UTC) del scan según la cabecera maestra del MDV: campo time_centroid
    (8.º int32 big-endian). `blob` son los primeros >= 32 bytes del archivo."""
    return datetime.utcfromtimestamp(struct.unpack(">8i", blob[:32])[7])


def _to_dt(story_id, s):
    """'HHMMSS' (con la fecha de story_id) o 'YYYYMMDDHHMMSS' -> datetime."""
    return datetime.strptime(s if len(s) == 14 else story_id + s, "%Y%m%d%H%M%S")


def check_case(story_id, scan_hhmmss, last_input=None, n_input=N_INPUT, horizons=HORIZONS,
               max_gap_min=MAX_GAP_MIN):
    """Aplica los criterios 1 y 2 a una lista de scans (HHMMSS) de una historia.

    Si `last_input` (HHMMSS) es None y la historia tiene exactamente n_input + horizons scans, el
    corte es el scan n_input; con otra cantidad hay que indicarlo (no se elige automáticamente).
    Devuelve dict con ok, motivo, last_input, n_scans, cadencia_media_min, cadencia_max_min,
    inputs (HHMMSS) y truth (HHMMSS).
    """
    # Los scans pueden venir como 'HHMMSS' (fecha = story_id) o 'YYYYMMDDHHMMSS'; se ordenan por tiempo real
    scans = sorted(set(scan_hhmmss), key=lambda x: _to_dt(story_id, x))
    n_total = n_input + horizons
    res = {"ok": False, "motivo": "", "last_input": last_input, "n_scans": len(scans),
           "cadencia_media_min": float("nan"), "cadencia_max_min": float("nan"), "inputs": [], "truth": []}
    if last_input is None:
        if len(scans) != n_total:
            res["motivo"] = (f"corte no especificado y la historia tiene {len(scans)} scans (no {n_total}); "
                             f"nombrar la carpeta {story_id}-HHMMSS con el último scan de entrada")
            return res
        last_input = scans[n_input - 1][-6:]
        res["last_input"] = last_input
    hits = [x for x in scans if x[-6:] == last_input[-6:]]
    if not hits:
        res["motivo"] = f"el scan de corte {last_input} no existe en la historia"
        return res
    cut_dt = _to_dt(story_id, hits[0])
    res["last_input"] = hits[0][-6:]
    inputs = [x for x in scans if _to_dt(story_id, x) <= cut_dt][-n_input:]
    truth = [x for x in scans if _to_dt(story_id, x) > cut_dt][:horizons]
    if len(inputs) < n_input or len(truth) < horizons:
        res["motivo"] = (f"ventana incompleta: {len(inputs)} de entrada (hacen falta {n_input}) y "
                         f"{len(truth)} de verificación (hacen falta {horizons}) alrededor de {last_input}")
        return res
    t = [_to_dt(story_id, x) for x in inputs + truth]
    gaps = [(b - a).total_seconds() / 60.0 for a, b in zip(t[:-1], t[1:])]
    res.update(cadencia_media_min=round(sum(gaps) / len(gaps), 2), cadencia_max_min=round(max(gaps), 2),
               inputs=inputs, truth=truth)
    if max(gaps) > max_gap_min:
        i = gaps.index(max(gaps))
        res["motivo"] = (f"hueco de {max(gaps):.1f} min entre {(inputs + truth)[i]} y {(inputs + truth)[i + 1]} "
                         f"(máximo admitido {max_gap_min:.0f} min)")
        return res
    res["ok"] = True
    return res


def _scan_names(path, fallback_date=None):
    """Scans .mdv de una carpeta (recursivo) o de un .zip. Devuelve {YYYYMMDDHHMMSS: origen}, con la
    fecha/hora leída de la cabecera MDV (time_centroid); si no se puede leer, usa la fecha de la carpeta
    (`fallback_date`) + HHMMSS del nombre. Si el HHMMSS del nombre no coincide con la cabecera, manda
    la cabecera y se anota en `names["_avisos"]`."""
    names, avisos = {}, []

    def add(fname, origen, blob):
        m = _RE_SCAN.search(fname)
        if not m:
            return
        try:
            dt = mdv_centroid(blob)
            key = dt.strftime("%Y%m%d%H%M%S")
            if key[-6:] != m.group(1):
                avisos.append(f"{fname}: nombre {m.group(1)} ≠ cabecera {key[-6:]}")
        except Exception:
            if not fallback_date:
                avisos.append(f"{fname}: cabecera ilegible y sin fecha de carpeta")
                return
            key = fallback_date + m.group(1)
        names[key] = origen

    if path.is_dir():
        for f in sorted(path.rglob("*.mdv")):
            with open(f, "rb") as fh:
                add(f.name, f, fh.read(32))
    elif path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                with z.open(info) as fh:
                    add(Path(info.filename).name, (path, info.filename), fh.read(32))
    if avisos:
        names["_avisos"] = avisos
    return names


def discover_cases(root):
    """Busca casos recursivamente en <root>/<tipologia>/.../<YYYYMMDD>[-<HHMMSS>] (carpeta con .mdv
    o .zip) y les aplica check_case. La tipología es la primera carpeta debajo de la raíz.

    Devuelve una lista de dicts (uno por caso, aceptado o descartado) con: tipo, story_id,
    pred_subdir, last_input_ts, estado ("ok" | "descartado"), motivo, n_scans, cadencia_media_min,
    cadencia_max_min, origen y scans ({HHMMSS: origen}, para copiar los MDV).
    """
    root = Path(root)
    found = []
    candidates = [p for p in root.rglob("*") if (p.is_dir() and any(p.glob("*.mdv"))) or p.suffix.lower() == ".zip"]
    for p in sorted(candidates):
        rel = p.relative_to(root).parts
        tipo = norm_tipo(rel[0]) if len(rel) > 1 else ""
        m = _RE_CASE.match(p.stem if p.suffix.lower() == ".zip" else p.name)
        row = {"tipo": tipo, "story_id": "", "pred_subdir": "", "last_input_ts": "", "estado": "descartado",
               "motivo": "", "n_scans": 0, "cadencia_media_min": float("nan"), "cadencia_max_min": float("nan"),
               "origen": str(p), "nota": "", "scans": {}, "window": set()}
        if tipo not in TIPOLOGIAS:
            row["motivo"] = f"tipología '{rel[0] if rel else ''}' desconocida (válidas: {', '.join(TIPOLOGIAS)})"
            found.append(row); continue
        folder_date, cut = (m.group(1), m.group(2)) if m else (None, None)
        scans = _scan_names(p, folder_date)
        avisos = scans.pop("_avisos", [])
        row["scans"], row["n_scans"] = scans, len(scans)
        if avisos:
            row["nota"] = "; ".join(avisos[:3])
        if not scans:
            row["motivo"] = "sin .mdv legibles"
            found.append(row); continue
        dates = sorted({k[:8] for k in scans})
        if len(dates) > 1:
            row["story_id"] = dates[0]
            row["motivo"] = f"la ventana cruza la medianoche UTC ({dates[0]} → {dates[-1]}); no soportado"
            found.append(row); continue
        story_id = dates[0]
        row["story_id"] = story_id
        if folder_date and folder_date != story_id:
            row["motivo"] = f"la carpeta dice {folder_date} pero la cabecera MDV dice {story_id}"
            found.append(row); continue
        chk = check_case(story_id, list(scans), cut)
        row["window"] = set(chk["inputs"] + chk["truth"])
        row.update(motivo=chk["motivo"], cadencia_media_min=chk["cadencia_media_min"],
                   cadencia_max_min=chk["cadencia_max_min"])
        if chk["last_input"]:
            row["pred_subdir"] = f"{story_id}-{chk['last_input']}"
            row["last_input_ts"] = f"{story_id}{chk['last_input']}"
        if chk["ok"]:
            row["estado"] = "ok"
        found.append(row)
    return found


MANIFEST_COLS = ["tipo", "story_id", "pred_subdir", "last_input_ts", "estado", "motivo",
                 "n_scans", "cadencia_media_min", "cadencia_max_min", "origen", "nota"]


def write_manifest(rows, path):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=MANIFEST_COLS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def read_manifest(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def rows_to_cases(rows):
    """Filas (manifiesto o discover_cases) -> (dict tipo -> [{story_id, pred_subdir}], descartados)."""
    cases, descartes = {}, []
    for r in rows:
        if r.get("estado") == "ok":
            cases.setdefault(r["tipo"], []).append({"story_id": r["story_id"], "pred_subdir": r["pred_subdir"]})
        else:
            descartes.append(r)
    return cases, descartes


def load_cases(spec=None):
    """spec None -> CASES fijo; carpeta -> discover_cases; .csv -> manifiesto. Devuelve (cases, descartados)."""
    if not spec:
        return CASES, []
    p = Path(spec)
    if p.is_dir():
        return rows_to_cases(discover_cases(p))
    return rows_to_cases(read_manifest(p))


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


def evaluate(stories_nc=STORIES_NC_CACHE, predictions=OUTPUT_PREDICTIONS, out_csv="validation_metrics.csv",
             cases=CASES):
    """Media y desvío por (tipo, horizonte) de las métricas del modelo. Devuelve el DataFrame."""
    accum = defaultdict(list)   # key: (tipo, horizonte_k) → list of metric dicts
    for case in iter_cases(stories_nc, predictions, cases=cases):
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
    ap.add_argument("--cases", help="manifiesto CSV (ingest) o carpeta de casos por tipología; default: CASES")
    args = ap.parse_args()
    cases, _ = load_cases(args.cases)
    evaluate(args.stories_nc, args.predictions, args.out, cases=cases)


if __name__ == "__main__":
    main()
