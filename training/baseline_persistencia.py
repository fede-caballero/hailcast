#!/usr/bin/env python3
"""
Baseline de persistencia para HailCast: mismas secuencias, mismos targets, mismas unidades,
misma máscara, mismos umbrales y mismo código de métricas que evaluate_cases.py.

Persistencia: "dentro de k scans el radar se ve igual que ahora". La predicción para los 7
horizontes es el último frame observado de la ventana de entrada, repetido 7 veces.

Variantes de persistencia que se calculan:
  * Persistencia            : último frame con el MISMO post-procesado que aplica el worker al
                              producto del modelo (physical_threshold_dbz: todo lo < 30 dBZ pasa
                              a MIN_DBZ). Es la comparación justa y la que va en la figura.
  * Persistencia_sin_umbral : último frame crudo (composite de máximo, sin umbral). Solo cambia
                              MAE y SSIM; el CSI a 35/55 dBZ es idéntico en las dos variantes.

Agregación del CSI (se reportan las dos, decir cuál se usa):
  * pooled : se suman aciertos, falsas alarmas y fallos de todas las secuencias y se divide una
             vez. Es el criterio principal (estable cuando hay secuencias sin píxeles >= 55 dBZ).
  * mean   : promedio del CSI de cada secuencia, ignorando los indefinidos (lo que hace
             evaluate_cases.py).

Salidas (en --out-dir):
  baseline_vs_hailcast.csv               tabla larga: grupo x horizonte x modelo x métricas
  baseline_vs_hailcast_por_secuencia.csv detalle por secuencia y horizonte
  bootstrap_ic95.csv                     valor e IC 95 % (bootstrap sobre secuencias) de cada
                                         métrica y de la diferencia HailCast - Persistencia
  csi35_por_horizonte.png                figura principal: CSI >= 35 dBZ, dos curvas, IC 95 %
  (*_en.png con --lang en: mismas figuras con textos en inglés)
  metricas_por_horizonte.png             MAE, SSIM, CSI35, CSI55 por horizonte, todas las secuencias
  csi35_por_tipologia.png                CSI >= 35 dBZ por horizonte, un panel por tipología
  csi35_tipologias_curvas.png            CSI >= 35 dBZ por horizonte, una curva por tipología (HailCast
                                         sólido, persistencia a trazos, mismo color)
  bootstrap_ic95_por_tipologia.csv       bootstrap de la diferencia HailCast - Persistencia por tipología
  resumen.md                             criterio de selección, casos, descartados, tablas y bootstrap

Casos: CASES fijo de evaluate_cases.py o --cases <manifiesto CSV de prepare_validation_cases.py ingest |
carpeta de casos por tipología>. El manifiesto aporta además los casos descartados y su motivo.

Uso:
  python baseline_persistencia.py --stories-nc data/validation_cases/stories_nc \
      --predictions data/validation_cases/output_predictions --out-dir training/results/baseline
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluate_cases import (CASES, CSI_THRESHOLDS, DBZ_CELDA, HORIZONS, MIN_DBZ, N_INPUT,  # noqa: E402
                            compute_csi, compute_metrics, iter_cases, load_cases, load_dbz)

PHYSICAL_THRESHOLD_DBZ = 30.0   # = DATA_CONFIG['physical_threshold_dbz'] del worker
MODEL_NAME = "HailCast"
PERS_NAME = "Persistencia"
PERS_RAW_NAME = "Persistencia_sin_umbral"

# Paleta (slots categóricos 1 y 2 de la guía de visualización) y tokens de texto
COLORS = {MODEL_NAME: "#2a78d6", PERS_NAME: "#eb6834"}
STYLES = {MODEL_NAME: dict(linestyle="-", marker="o"), PERS_NAME: dict(linestyle="--", marker="s")}
TEXT, TEXT2, GRID = "#0b0b0b", "#52514e", "#e6e5e1"
# Slots categóricos 1-5 de la guía (orden fijo, validado para visión normal y daltonismo)
TIPO_COLORS = {"multicelda": "#2a78d6", "supercelda": "#eb6834", "adveccion": "#1baf7a",
               "decaimiento": "#eda100", "debil": "#e87ba4"}

# Textos de las figuras (--lang es|en). Los CSV y resumen.md quedan siempre en español.
I18N = {
    "es": {"xlabel": "minutos de anticipación", "pers": "Persistencia",
           "csi35_y": "CSI (≥ 35 dBZ, pooled)",
           "csi35_title": "CSI ≥ 35 dBZ por horizonte — {n} secuencias, banda = IC 95 % bootstrap",
           "mae": "MAE (dBZ) — menor es mejor", "ssim": "SSIM — mayor es mejor",
           "csi35": "CSI ≥ 35 dBZ (pooled)", "csi55": "CSI ≥ 55 dBZ (pooled)",
           "suptitle": "HailCast vs persistencia — {n} secuencias de validación",
           "curvas_title": "CSI ≥ 35 dBZ por tipología — HailCast (sólido) vs persistencia (trazos)",
           "tipos": {"multicelda": "multicelda", "supercelda": "supercelda", "adveccion": "advección",
                     "decaimiento": "decaimiento", "debil": "débil"}},
    "en": {"xlabel": "lead time (minutes)", "pers": "Persistence",
           "csi35_y": "CSI (≥ 35 dBZ, pooled)",
           "csi35_title": "CSI ≥ 35 dBZ by lead time — {n} sequences, band = 95 % bootstrap CI",
           "mae": "MAE (dBZ) — lower is better", "ssim": "SSIM — higher is better",
           "csi35": "CSI ≥ 35 dBZ (pooled)", "csi55": "CSI ≥ 55 dBZ (pooled)",
           "suptitle": "HailCast vs persistence — {n} validation sequences",
           "curvas_title": "CSI ≥ 35 dBZ by storm type — HailCast (solid) vs persistence (dashed)",
           "tipos": {"multicelda": "multicell", "supercelda": "supercell", "adveccion": "advection",
                     "decaimiento": "decay", "debil": "weak"}},
}
T = I18N["es"]


# ----------------------------------------------------------------------------- datos
def persistencia(last_frame, horizons=HORIZONS, umbral=PHYSICAL_THRESHOLD_DBZ):
    """Repite el último frame observado para cada horizonte. Con `umbral`, aplica el mismo
    post-procesado que el worker al producto del modelo (< umbral -> MIN_DBZ)."""
    f = last_frame.copy()
    if umbral is not None:
        f[f < umbral] = MIN_DBZ
    return np.repeat(f[None], horizons, axis=0)


def load_data(stories_nc, predictions, cases=CASES):
    """Devuelve meta (DataFrame por secuencia) y arrays (N, 7, H, W) en dBZ: Y, P_model, P_pers, P_pers_raw."""
    meta, Y, P, PP, PPR = [], [], [], [], []
    for case in iter_cases(stories_nc, predictions, cases=cases):
        y = np.stack([load_dbz(f) for f in case["truth_files"]])
        p = np.stack([load_dbz(f) for f in case["pred_files"]])
        inputs = [load_dbz(f) for f in case["input_files"][-N_INPUT:]]
        last = inputs[-1]
        assert y.shape == p.shape == (HORIZONS,) + last.shape, (y.shape, p.shape, last.shape)
        ts = [case["input_files"][-1].stem] + [f.stem for f in case["truth_files"]]
        t = [datetime.strptime(s, "%Y%m%d%H%M%S") for s in ts]
        cadencia = float(np.mean(np.diff([x.timestamp() for x in t])) / 60.0)
        # Diagnóstico (no entra en las métricas): píxeles >= DBZ_CELDA del último frame de entrada que NO
        # superan ese umbral en los 8 frames (los que sí lo hacen son ecos fijos: cordillera, clutter).
        X = np.stack(inputs)
        fijos = np.all(X >= DBZ_CELDA, axis=0)
        px_mov = int(((last >= DBZ_CELDA) & ~fijos).sum())
        meta.append({"tipo": case["tipo"], "story_id": case["story_id"], "pred_subdir": case["pred_subdir"],
                     "last_input_ts": case["last_input_ts"], "cadencia_min": round(cadencia, 2),
                     "n_inputs_disponibles": len(case["input_files"]),
                     "px_ge45_entrada": px_mov, "px_ecos_fijos_45": int(fijos.sum()),
                     "max_dbz_verif": round(float(y.max()), 1),
                     "observacion": ("" if px_mov > 0 or case["tipo"] == "debil"
                                     else f"sin píxeles ≥ {DBZ_CELDA:.0f} dBZ (móviles) en la entrada: ¿debil?")})
        Y.append(y); P.append(p)
        PP.append(persistencia(last)); PPR.append(persistencia(last, umbral=None))
        print(f"  cargada {case['tipo']:10s} {case['story_id']}  último input {case['last_input_ts']}  "
              f"cadencia {cadencia:.2f} min  max obs {y.max():.1f} dBZ")
    if not meta:
        sys.exit("No se cargó ninguna secuencia: revisá --stories-nc y --predictions.")
    return (pd.DataFrame(meta), np.stack(Y).astype(np.float32), np.stack(P).astype(np.float32),
            np.stack(PP).astype(np.float32), np.stack(PPR).astype(np.float32))


# ----------------------------------------------------------------------------- métricas
def short_name(key):
    """'CSI_35dBZ' -> 'CSI35' (nombre de columna)."""
    return key.replace("CSI_", "CSI").replace("dBZ", "")


def contingency(pred, obs, thr):
    """(tp, fp, fn) por secuencia y horizonte: arrays (N, T). Mismo operador >= que compute_csi."""
    p, o = pred >= thr, obs >= thr
    axes = (-2, -1)
    return (np.sum(p & o, axis=axes), np.sum(p & ~o, axis=axes), np.sum(~p & o, axis=axes))


def per_sequence_metrics(preds, Y, meta):
    """compute_metrics (el de evaluate_cases.py) por secuencia, horizonte y modelo. Formato largo."""
    rows = []
    for name, P in preds.items():
        for i in range(Y.shape[0]):
            for t in range(HORIZONS):
                m = compute_metrics(P[i, t], Y[i, t])
                rows.append({"modelo": name, "tipo": meta.tipo[i], "story_id": meta.story_id[i],
                             "pred_subdir": meta.pred_subdir[i], "horizonte": t + 1, **m})
    return pd.DataFrame(rows)


def pooled_csi(P, Y, thr):
    """CSI pooled por horizonte: compute_csi sobre todas las secuencias apiladas."""
    return np.array([compute_csi(P[:, t], Y[:, t], thr) for t in range(HORIZONS)])


def summary_table(per_seq, preds, Y, meta, cadencia):
    """Tabla grupo x horizonte x modelo con MAE, SSIM, CSI mean y CSI pooled."""
    grupos = {"todas": np.arange(len(meta))}
    for tipo in meta.tipo.unique():
        grupos[tipo] = np.where(meta.tipo.values == tipo)[0]
    rows = []
    for grupo, idx in grupos.items():
        seqs = set(meta.pred_subdir.values[idx])
        for name, P in preds.items():
            sub = per_seq[(per_seq.modelo == name) & per_seq.pred_subdir.isin(seqs)]
            g = sub.groupby("horizonte")
            for t in range(1, HORIZONS + 1):
                row = {"grupo": grupo, "horizonte": t, "lead_min": round(t * cadencia, 1), "modelo": name,
                       "n_secuencias": len(idx),
                       "MAE_dBZ": g.MAE_dBZ.mean()[t], "SSIM": g.SSIM.mean()[t]}
                for key, thr in CSI_THRESHOLDS.items():
                    short = short_name(key)
                    row[f"{short}_mean"] = g[key].mean()[t]          # nanmean: ignora indefinidos
                    row[f"{short}_pooled"] = compute_csi(P[idx, t - 1], Y[idx, t - 1], thr)
                rows.append(row)
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- bootstrap
def bootstrap(per_seq, preds, Y, meta, n_boot, seed):
    """IC 95 % por remuestreo de secuencias (con reposición) para cada métrica, modelo y horizonte,
    y para la diferencia pareada HailCast - Persistencia (mismos índices en los dos)."""
    rng = np.random.default_rng(seed)
    N = len(meta)
    idxs = rng.integers(0, N, (n_boot, N))
    names = list(preds)
    # Estadísticos por secuencia: (N, T) por métrica y modelo
    stats = {}
    for name, P in preds.items():
        sub = per_seq[(per_seq.modelo == name) & per_seq.pred_subdir.isin(meta.pred_subdir)]
        mat = lambda col: sub.pivot(index="pred_subdir", columns="horizonte", values=col).reindex(meta.pred_subdir).values
        stats[name] = {"MAE_dBZ": ("mean", mat("MAE_dBZ")), "SSIM": ("mean", mat("SSIM"))}
        for key, thr in CSI_THRESHOLDS.items():
            short = short_name(key)
            stats[name][f"{short}_pooled"] = ("pooled", contingency(P, Y, thr))

    def value(name, metric, idx):
        kind, s = stats[name][metric]
        if kind == "mean":
            return np.nanmean(s[idx], axis=0)                         # (T,)
        tp, fp, fn = (c[idx].sum(axis=0) for c in s)
        den = tp + fp + fn
        return np.where(den > 0, tp / np.maximum(den, 1), np.nan)     # (T,)

    rows = []
    full = np.arange(N)
    for metric in stats[names[0]]:
        boots = {name: np.array([value(name, metric, idx) for idx in idxs]) for name in names}   # (B, T)
        for name in names:
            v = value(name, metric, full)
            lo, hi = np.nanpercentile(boots[name], [2.5, 97.5], axis=0)
            for t in range(HORIZONS):
                rows.append({"metrica": metric, "horizonte": t + 1, "modelo": name,
                             "valor": v[t], "ic_lo": lo[t], "ic_hi": hi[t]})
        d = boots[MODEL_NAME] - boots[PERS_NAME]
        v = value(MODEL_NAME, metric, full) - value(PERS_NAME, metric, full)
        lo, hi = np.nanpercentile(d, [2.5, 97.5], axis=0)
        for t in range(HORIZONS):
            rows.append({"metrica": metric, "horizonte": t + 1, "modelo": f"{MODEL_NAME} - {PERS_NAME}",
                         "valor": v[t], "ic_lo": lo[t], "ic_hi": hi[t]})
    return pd.DataFrame(rows)


def bootstrap_por_tipologia(per_seq, preds, Y, meta, n_boot, seed):
    """bootstrap() restringido a cada tipología (>= 2 secuencias). Columna extra: grupo."""
    out = []
    for tipo in meta.tipo.unique():
        idx = np.where(meta.tipo.values == tipo)[0]
        if len(idx) < 2:
            continue
        sub_meta = meta.iloc[idx].reset_index(drop=True)
        b = bootstrap(per_seq, {k: v[idx] for k, v in preds.items()}, Y[idx], sub_meta, n_boot, seed)
        b.insert(0, "grupo", tipo)
        b["n_secuencias"] = len(idx)
        out.append(b)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


# ----------------------------------------------------------------------------- figuras
def _style(ax, ylabel=None, ylim=None):
    ax.grid(True, color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=TEXT2, labelsize=9)
    if ylabel:
        ax.set_ylabel(ylabel, color=TEXT2, fontsize=9)
    if ylim:
        ax.set_ylim(*ylim)
    ax.set_xlabel(T["xlabel"], color=TEXT2, fontsize=9)


def _curve(ax, x, name, y, lo=None, hi=None, label=None):
    c = COLORS[name]
    if lo is not None:
        ax.fill_between(x, lo, hi, color=c, alpha=0.15, linewidth=0)
    ax.plot(x, y, color=c, linewidth=2, markersize=6, markeredgecolor="white", markeredgewidth=1.5,
            solid_capstyle="round", label=label or (T["pers"] if name == PERS_NAME else name), **STYLES[name])


def fig_csi35(boot, x, n, out):
    fig, ax = plt.subplots(figsize=(6.4, 3.8), dpi=200)
    for name in (MODEL_NAME, PERS_NAME):
        b = boot[(boot.metrica == "CSI35_pooled") & (boot.modelo == name)].sort_values("horizonte")
        _curve(ax, x, name, b.valor.values, b.ic_lo.values, b.ic_hi.values)
    _style(ax, T["csi35_y"], (0, 1))
    ax.set_title(T["csi35_title"].format(n=n),
                 color=TEXT, fontsize=10, loc="left")
    ax.legend(frameon=False, fontsize=9, labelcolor=TEXT2)
    fig.tight_layout(); fig.savefig(out); plt.close(fig)


def fig_metricas(tabla, boot, x, n, out):
    panels = [("MAE_dBZ", T["mae"], None), ("SSIM", T["ssim"], None),
              ("CSI35_pooled", T["csi35"], (0, 1)), ("CSI55_pooled", T["csi55"], (0, 1))]
    fig, axes = plt.subplots(2, 2, figsize=(9, 6.4), dpi=200)
    for ax, (metric, title, ylim) in zip(axes.ravel(), panels):
        for name in (MODEL_NAME, PERS_NAME):
            b = boot[(boot.metrica == metric) & (boot.modelo == name)].sort_values("horizonte")
            _curve(ax, x, name, b.valor.values, b.ic_lo.values, b.ic_hi.values)
        _style(ax, None, ylim)
        ax.set_title(title, color=TEXT, fontsize=10, loc="left")
    axes[0, 0].legend(frameon=False, fontsize=9, labelcolor=TEXT2)
    fig.suptitle(T["suptitle"].format(n=n), color=TEXT, fontsize=11, x=0.01, ha="left")
    fig.tight_layout(); fig.savefig(out); plt.close(fig)


def fig_tipologia(tabla, x, out):
    tipos = [g for g in tabla.grupo.unique() if g != "todas"]
    ncols = min(3, len(tipos)); nrows = int(np.ceil(len(tipos) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(3.3 * ncols, 3.4 * nrows), dpi=200, sharey=True, squeeze=False)
    axes = axes.ravel()
    for ax, tipo in zip(axes, tipos):
        sub = tabla[tabla.grupo == tipo]
        for name in (MODEL_NAME, PERS_NAME):
            s = sub[sub.modelo == name].sort_values("horizonte")
            _curve(ax, x, name, s.CSI35_pooled.values)
        _style(ax, T["csi35_y"] if ax is axes[0] else None, (0, 1))
        ax.set_title(f"{T['tipos'].get(tipo, tipo)} (n={int(sub.n_secuencias.iloc[0])})", color=TEXT, fontsize=10, loc="left")
    for ax in axes[len(tipos):]:
        ax.set_visible(False)
    axes[0].legend(frameon=False, fontsize=9, labelcolor=TEXT2)
    fig.tight_layout(); fig.savefig(out); plt.close(fig)


def fig_tipologias_curvas(tabla, x, out):
    """Un solo eje: una curva por tipología (color fijo por tipología); HailCast sólido con marcador,
    persistencia a trazos. Etiqueta directa al final de cada curva de HailCast."""
    tipos = [g for g in tabla.grupo.unique() if g != "todas"]
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=200)
    ends = []
    for tipo in tipos:
        c = TIPO_COLORS.get(tipo, "#898781")
        sub = tabla[tabla.grupo == tipo]
        n = int(sub.n_secuencias.iloc[0])
        label = f"{T['tipos'].get(tipo, tipo)} (n={n})"
        hc = sub[sub.modelo == MODEL_NAME].sort_values("horizonte").CSI35_pooled.values
        pe = sub[sub.modelo == PERS_NAME].sort_values("horizonte").CSI35_pooled.values
        ax.plot(x, pe, color=c, linewidth=1.6, linestyle="--", alpha=0.85)
        ax.plot(x, hc, color=c, linewidth=2, marker="o", markersize=5.5, markeredgecolor="white",
                markeredgewidth=1.2, solid_capstyle="round", label=label)
        ends.append([hc[-1], label, c])
    # Etiquetas directas al final de cada curva de HailCast, separadas si se superponen
    ends.sort(key=lambda e: e[0])
    min_sep, y_prev = 0.045, -1.0
    for e in ends:
        e.append(max(e[0], y_prev + min_sep)); y_prev = e[-1]
    for y_end, label, c, y_lab in ends:
        ax.annotate(label, (x[-1], y_end), xytext=(x[-1] + 0.6, y_lab), textcoords="data",
                    color=TEXT2, fontsize=8, va="center",
                    arrowprops=dict(arrowstyle="-", color=c, lw=0.8, alpha=0.6) if abs(y_lab - y_end) > 1e-9 else None)
    _style(ax, T["csi35_y"], (0, 1))
    ax.set_xlim(x[0] - 1, x[-1] + 0.45 * (x[-1] - x[0]))
    ax.set_title(T["curvas_title"], color=TEXT, fontsize=10, loc="left")
    ax.legend(frameon=False, fontsize=8, labelcolor=TEXT2, loc="upper right")
    fig.tight_layout(); fig.savefig(out); plt.close(fig)


# ----------------------------------------------------------------------------- resumen
def _boot_lines(d, cadencia, metrics):
    L = []
    for metric in metrics:
        for t in (1, 4, 7):
            sel = d[(d.metrica == metric) & (d.horizonte == t)]
            if sel.empty:
                continue
            r = sel.iloc[0]
            signo = "+" if r.valor >= 0 else "−"
            L.append(f"- {metric} a t+{t} ({t * cadencia:.1f} min): {signo}{abs(r.valor):.3f}, IC 95 % "
                     f"[{r.ic_lo:+.3f}, {r.ic_hi:+.3f}]{'  ← no incluye el cero' if r.ic_lo * r.ic_hi > 0 else ''}")
    return L


def write_resumen(tabla, boot, meta, cadencia, out_dir, n_boot, boot_tipo=None, descartes=(), criterio=None):
    cols = ["MAE_dBZ", "SSIM", "CSI35_pooled", "CSI55_pooled", "CSI35_mean", "CSI55_mean"]
    L = [f"# HailCast vs persistencia — {len(meta)} secuencias de validación\n",
         f"Generado {datetime.now():%Y-%m-%d %H:%M} por training/baseline_persistencia.py.\n"]
    if criterio:
        L += [criterio.strip(), ""]
    L += [
         "**Criterio del CSI:** pooled (se suman aciertos, falsas alarmas y fallos de todas las secuencias "
         "y se divide una vez), operador `>=`, umbrales 35 y 55 dBZ. Se incluye también el promedio por "
         "secuencia (`*_mean`, ignora indefinidos) para comparar con evaluate_cases.py.\n",
         "**Persistencia:** último frame observado de la ventana de entrada repetido 7 veces, con el mismo "
         f"post-procesado que el producto del modelo (< {PHYSICAL_THRESHOLD_DBZ:.0f} dBZ → {MIN_DBZ:.0f} dBZ). "
         "Mismas secuencias, mismos 7 targets, dBZ, misma máscara (NaN → −29 dBZ, recorte a [−29, 65]) y "
         "mismo código de métricas (`evaluate_cases.compute_metrics`).\n",
         f"Cadencia media del radar en estas secuencias: {cadencia:.2f} min por scan "
         f"(t+1 … t+7 = {cadencia:.1f} … {7 * cadencia:.1f} min).\n",
         f"Secuencias por tipología: {meta.tipo.value_counts().to_dict()}.\n"]
    L.append("\n## Casos usados\n")
    L.append("`px_ge45_entrada`: píxeles ≥ 45 dBZ del último frame de entrada excluyendo los que superan 45 dBZ "
             "en los 8 frames (`px_ecos_fijos_45`, ecos fijos). Es un diagnóstico de la regla de la tipología "
             "`debil`; la clasificación la hace el analista en TITAN y no se modifica acá.\n")
    L.append(meta[["tipo", "story_id", "pred_subdir", "last_input_ts", "cadencia_min", "px_ge45_entrada",
                   "px_ecos_fijos_45", "max_dbz_verif", "observacion"]].to_markdown(index=False))
    if len(descartes):
        L.append("\n## Casos descartados\n")
        dd = pd.DataFrame(list(descartes))
        keep = [c for c in ["tipo", "story_id", "pred_subdir", "n_scans", "cadencia_max_min", "motivo", "origen"] if c in dd]
        L.append(dd[keep].to_markdown(index=False))
    L.append("")
    for grupo in tabla.grupo.unique():
        sub = tabla[(tabla.grupo == grupo) & tabla.modelo.isin([MODEL_NAME, PERS_NAME])]
        piv = sub.pivot(index="horizonte", columns="modelo", values=cols)
        piv = piv.reorder_levels([1, 0], axis=1).sort_index(axis=1, level=0, sort_remaining=False)
        piv.columns = [f"{m} {c}" for m, c in piv.columns]
        L.append(f"\n## {grupo} (n={int(sub.n_secuencias.iloc[0])})\n")
        L.append(piv.round(3).to_markdown())
        L.append("")
    L.append(f"\n## Bootstrap de la diferencia HailCast − Persistencia ({n_boot} remuestreos de secuencias, IC 95 %)\n")
    L.append("Si el intervalo no incluye el cero, la diferencia es consistente en la muestra.\n")
    diff_name = f"{MODEL_NAME} - {PERS_NAME}"
    L.append(f"\n### todas (n={len(meta)})\n")
    L += _boot_lines(boot[boot.modelo == diff_name], cadencia, ["CSI35_pooled", "CSI55_pooled", "MAE_dBZ", "SSIM"])
    if boot_tipo is not None and len(boot_tipo):
        for tipo in boot_tipo.grupo.unique():
            d = boot_tipo[(boot_tipo.grupo == tipo) & (boot_tipo.modelo == diff_name)]
            L.append(f"\n### {tipo} (n={int(d.n_secuencias.iloc[0])})\n")
            L += _boot_lines(d, cadencia, ["CSI35_pooled", "CSI55_pooled", "MAE_dBZ"])
    (Path(out_dir) / "resumen.md").write_text("\n".join(L) + "\n", encoding="utf-8")


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stories-nc", required=True, help="carpeta con <story_id>/<YYYYMMDDHHMMSS>.nc observados")
    ap.add_argument("--predictions", required=True, help="carpeta con <pred_subdir>/*.nc del modelo")
    ap.add_argument("--out-dir", default="results/baseline")
    ap.add_argument("--n-boot", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--lang", choices=list(I18N), default="es", help="idioma de las figuras (en agrega sufijo _en)")
    ap.add_argument("--cases", help="manifiesto CSV (prepare_validation_cases.py ingest) o carpeta de casos; default: CASES")
    ap.add_argument("--criterio", help="markdown con el criterio de selección a incluir en resumen.md "
                                       "(default: <out-dir>/criterio_seleccion.md si existe)")
    args = ap.parse_args()
    global T
    T = I18N[args.lang]
    sfx = "" if args.lang == "es" else f"_{args.lang}"
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)

    cases, descartes = load_cases(args.cases)
    criterio_path = Path(args.criterio) if args.criterio else out / "criterio_seleccion.md"
    criterio = criterio_path.read_text(encoding="utf-8") if criterio_path.exists() else None

    print("Cargando secuencias…")
    meta, Y, P, PP, PPR = load_data(args.stories_nc, args.predictions, cases)
    faltan = [f"{c['pred_subdir']} ({t})" for t, cs in cases.items() for c in cs if c["pred_subdir"] not in set(meta.pred_subdir)]
    if faltan:
        print(f"ATENCIÓN: {len(faltan)} casos de CASES omitidos: {', '.join(faltan)}")
    cadencia = float(meta.cadencia_min.mean())
    preds = {MODEL_NAME: P, PERS_NAME: PP, PERS_RAW_NAME: PPR}

    print("Métricas por secuencia…")
    per_seq = per_sequence_metrics(preds, Y, meta)
    per_seq.to_csv(out / "baseline_vs_hailcast_por_secuencia.csv", index=False)

    tabla = summary_table(per_seq, preds, Y, meta, cadencia)
    tabla.to_csv(out / "baseline_vs_hailcast.csv", index=False)

    print(f"Bootstrap ({args.n_boot} remuestreos)…")
    boot = bootstrap(per_seq, {k: preds[k] for k in (MODEL_NAME, PERS_NAME)}, Y, meta, args.n_boot, args.seed)
    boot.to_csv(out / "bootstrap_ic95.csv", index=False)
    boot_tipo = bootstrap_por_tipologia(per_seq, {k: preds[k] for k in (MODEL_NAME, PERS_NAME)}, Y, meta,
                                        args.n_boot, args.seed)
    boot_tipo.to_csv(out / "bootstrap_ic95_por_tipologia.csv", index=False)

    x = np.arange(1, HORIZONS + 1) * cadencia
    fig_csi35(boot, x, len(meta), out / f"csi35_por_horizonte{sfx}.png")
    fig_metricas(tabla, boot, x, len(meta), out / f"metricas_por_horizonte{sfx}.png")
    fig_tipologia(tabla, x, out / f"csi35_por_tipologia{sfx}.png")
    fig_tipologias_curvas(tabla, x, out / f"csi35_tipologias_curvas{sfx}.png")
    write_resumen(tabla, boot, meta, cadencia, out, args.n_boot, boot_tipo, descartes, criterio)

    todas = tabla[(tabla.grupo == "todas") & tabla.modelo.isin([MODEL_NAME, PERS_NAME])]
    print("\n=== TODAS LAS SECUENCIAS (CSI pooled) ===")
    print(todas.pivot(index="horizonte", columns="modelo",
                      values=["MAE_dBZ", "SSIM", "CSI35_pooled", "CSI55_pooled"]).round(3).to_string())
    print(f"\nSalidas en {out.resolve()}")


if __name__ == "__main__":
    main()
