# HailCast vs persistencia — 77 secuencias de validación

Generado 2026-11-24 00:17 por training/baseline_persistencia.py.

## Criterio de selección de casos (fijado antes de correr el pipeline, 2026-11-23)

Las tormentas se eligen a mano analizándolas en TITAN y se clasifican por tipología. Para cada caso
el pipeline verifica automáticamente (`evaluate_cases.discover_cases`) y registra:

1. **Longitud:** la historia tiene al menos 15 scans consecutivos: 8 de entrada (el último es
   `last_input_ts`) y 7 de verificación posteriores.
2. **Cadencia:** la separación entre scans consecutivos es ≤ 6 minutos en toda la ventana de 15
   (sin huecos). Se informa la cadencia media y la máxima de la ventana.
3. **Corte:** el instante de corte (`last_input_ts`) se elige mirando solo los frames de entrada,
   nunca la predicción ni la verificación. Si un caso no cumple 1 o 2 se descarta y se anota el
   motivo; no se "arregla" moviendo el corte.
4. **Tipologías:** `multicelda`, `supercelda`, `adveccion` y la nueva `decaimiento` (celdas que
   pierden intensidad durante la ventana de verificación). La convección débil, sin ninguna celda
   ≥ 45 dBZ en los 8 frames de entrada, va en una quinta tipología `debil`, separada y no mezclada
   con las anteriores. El pipeline calcula el máximo de dBZ de la entrada y marca los casos de una
   tipología principal que no alcanzan 45 dBZ para revisarlos.
5. **Tamaño objetivo:** 35 a 45 casos en total, con al menos 6 por tipología principal.

Las 13 secuencias publicadas en `training/results/baseline/` se mantienen sin cambios y entran en
la muestra ampliada con los mismos cortes; las métricas y el criterio pooled del CSI
(`evaluate_cases.compute_metrics`, `compute_csi`) no se modifican, de modo que sus números deben
reproducirse exactamente.

**Criterio del CSI:** pooled (se suman aciertos, falsas alarmas y fallos de todas las secuencias y se divide una vez), operador `>=`, umbrales 35 y 55 dBZ. Se incluye también el promedio por secuencia (`*_mean`, ignora indefinidos) para comparar con evaluate_cases.py.

**Persistencia:** último frame observado de la ventana de entrada repetido 7 veces, con el mismo post-procesado que el producto del modelo (< 30 dBZ → -29 dBZ). Mismas secuencias, mismos 7 targets, dBZ, misma máscara (NaN → −29 dBZ, recorte a [−29, 65]) y mismo código de métricas (`evaluate_cases.compute_metrics`).

Cadencia media del radar en estas secuencias: 3.56 min por scan (t+1 … t+7 = 3.6 … 25.0 min).

Secuencias por tipología: {'supercelda': 21, 'multicelda': 21, 'adveccion': 20, 'debil': 15}.


## Casos usados

`px_ge45_entrada`: píxeles ≥ 45 dBZ del último frame de entrada excluyendo los que superan 45 dBZ en los 8 frames (`px_ecos_fijos_45`, ecos fijos). Es un diagnóstico de la regla de la tipología `debil`; la clasificación la hace el analista en TITAN y no se modifica acá.

| tipo       |   story_id | pred_subdir     |   last_input_ts |   cadencia_min |   px_ge45_entrada |   px_ecos_fijos_45 |   max_dbz_verif | observacion   |
|:-----------|-----------:|:----------------|----------------:|---------------:|------------------:|-------------------:|----------------:|:--------------|
| adveccion  |   20251209 | 20251209-204310 |  20251209204310 |           4.35 |               258 |                 87 |            65   |               |
| adveccion  |   20260106 | 20260106-161602 |  20260106161602 |           3.44 |               355 |                 39 |            65   |               |
| adveccion  |   20260106 | 20260106-170803 |  20260106170803 |           3.5  |               408 |                 20 |            65   |               |
| adveccion  |   20260121 | 20260121-215744 |  20260121215744 |           3.45 |               318 |                 19 |            65   |               |
| adveccion  |   20260105 | 20260105-232213 |  20260105232213 |           3.44 |               258 |                 60 |            65   |               |
| adveccion  |   20260106 | 20260106-185143 |  20260106185143 |           3.45 |               848 |                 63 |            65   |               |
| adveccion  |   20260106 | 20260106-194320 |  20260106194320 |           3.44 |              1145 |                 78 |            65   |               |
| adveccion  |   20251209 | 20251209-214117 |  20251209214117 |           3.45 |               262 |                 21 |            65   |               |
| adveccion  |   20260121 | 20260121-210539 |  20260121210539 |           3.45 |               186 |                 56 |            65   |               |
| adveccion  |   20251214 | 20251214-013141 |  20251214013141 |           3.45 |               454 |                 56 |            65   |               |
| adveccion  |   20251214 | 20251214-022332 |  20251214022332 |           3.45 |               131 |                 31 |            65   |               |
| adveccion  |   20251219 | 20251219-205049 |  20251219205049 |           3.47 |               302 |                 32 |            65   |               |
| adveccion  |   20260105 | 20260105-213856 |  20260105213856 |           3.44 |               227 |                 39 |            65   |               |
| adveccion  |   20260105 | 20260105-223034 |  20260105223034 |           3.44 |               179 |                 24 |            65   |               |
| adveccion  |   20260106 | 20260106-002415 |  20260106002415 |           3.44 |               418 |                 16 |            65   |               |
| adveccion  |   20260106 | 20260106-174252 |  20260106174252 |           3.45 |               370 |                 50 |            65   |               |
| adveccion  |   20251210 | 20251210-201602 |  20251210201602 |           3.46 |               296 |                 45 |            65   |               |
| adveccion  |   20260110 | 20260110-191629 |  20260110191629 |           3.44 |               178 |                 28 |            63.6 |               |
| adveccion  |   20260111 | 20260111-231927 |  20260111231927 |           3.44 |               235 |                 22 |            63.9 |               |
| adveccion  |   20260113 | 20260113-030142 |  20260113030142 |           3.44 |               742 |                 19 |            65   |               |
| debil      |   20251204 | 20251204-003234 |  20251204003234 |           4.57 |               170 |                 92 |            65   |               |
| debil      |   20251217 | 20251217-185919 |  20251217185919 |           3.56 |                80 |                 24 |            65   |               |
| debil      |   20251219 | 20251219-195845 |  20251219195845 |           3.47 |               149 |                 29 |            65   |               |
| debil      |   20260105 | 20260105-191743 |  20260105191743 |           3.45 |               190 |                 20 |            65   |               |
| debil      |   20260106 | 20260106-214704 |  20260106214704 |           3.43 |               453 |                 67 |            65   |               |
| debil      |   20260106 | 20260106-223839 |  20260106223839 |           3.44 |               289 |                 50 |            65   |               |
| debil      |   20260110 | 20260110-222824 |  20260110222824 |           3.42 |               142 |                 36 |            64.5 |               |
| debil      |   20251204 | 20251204-014118 |  20251204014118 |           4.56 |               107 |                 45 |            65   |               |
| debil      |   20251209 | 20251209-163828 |  20251209163828 |           4.55 |               226 |                 69 |            65   |               |
| debil      |   20251209 | 20251209-174846 |  20251209174846 |           4.61 |               207 |                 52 |            65   |               |
| debil      |   20251218 | 20251218-215625 |  20251218215625 |           4.59 |               103 |                 48 |            65   |               |
| debil      |   20251218 | 20251218-230524 |  20251218230524 |           4.58 |                83 |                 43 |            65   |               |
| debil      |   20251115 | 20251115-161339 |  20251115161339 |           4.65 |               144 |                 33 |            65   |               |
| debil      |   20251126 | 20251126-162604 |  20251126162604 |           3.45 |               226 |                 35 |            65   |               |
| debil      |   20251126 | 20251126-171753 |  20251126171753 |           3.45 |               278 |                  7 |            65   |               |
| multicelda |   20251103 | 20251103-224814 |  20251103224814 |           2.96 |               435 |                  7 |            65   |               |
| multicelda |   20260114 | 20260114-202314 |  20260114202314 |           3.44 |               371 |                 14 |            65   |               |
| multicelda |   20260114 | 20260114-211448 |  20260114211448 |           3.45 |               883 |                 30 |            65   |               |
| multicelda |   20260114 | 20260114-220623 |  20260114220623 |           3.47 |               558 |                 10 |            64.9 |               |
| multicelda |   20260106 | 20260106-021443 |  20260106021443 |           3.45 |               282 |                 64 |            65   |               |
| multicelda |   20260106 | 20260106-030630 |  20260106030630 |           3.45 |               343 |                 27 |            65   |               |
| multicelda |   20260106 | 20260106-035811 |  20260106035811 |           3.44 |               410 |                 12 |            65   |               |
| multicelda |   20260122 | 20260122-225516 |  20260122225516 |           3.43 |               356 |                  1 |            63.9 |               |
| multicelda |   20251103 | 20251103-233841 |  20251103233841 |           2.98 |               497 |                 21 |            65   |               |
| multicelda |   20251106 | 20251106-180650 |  20251106180650 |           2.97 |               257 |                 21 |            65   |               |
| multicelda |   20251106 | 20251106-185121 |  20251106185121 |           2.97 |               271 |                  6 |            65   |               |
| multicelda |   20251115 | 20251115-174123 |  20251115174123 |           4.48 |               874 |                 34 |            65   |               |
| multicelda |   20251115 | 20251115-184902 |  20251115184902 |           4.67 |               584 |                 39 |            65   |               |
| multicelda |   20251126 | 20251126-180940 |  20251126180940 |           3.45 |              1059 |                 37 |            65   |               |
| multicelda |   20251126 | 20251126-190133 |  20251126190133 |           3.48 |               793 |                 53 |            65   |               |
| multicelda |   20251126 | 20251126-195337 |  20251126195337 |           3.46 |               766 |                 52 |            65   |               |
| multicelda |   20251211 | 20251211-033818 |  20251211033818 |           3.46 |               168 |                 39 |            65   |               |
| multicelda |   20251213 | 20251213-230638 |  20251213230638 |           3.45 |               411 |                 28 |            65   |               |
| multicelda |   20251219 | 20251219-220704 |  20251219220704 |           3.47 |               234 |                 39 |            65   |               |
| multicelda |   20251221 | 20251221-012431 |  20251221012431 |           3.44 |               814 |                 61 |            65   |               |
| multicelda |   20251222 | 20251222-215700 |  20251222215700 |           3.45 |               503 |                 30 |            65   |               |
| supercelda |   20251101 | 20251101-002155 |  20251101002155 |           2.96 |               258 |                  3 |            60.2 |               |
| supercelda |   20260122 | 20260122-205737 |  20260122205737 |           3.45 |               462 |                 47 |            65   |               |
| supercelda |   20260122 | 20260122-220339 |  20260122220339 |           3.44 |               325 |                129 |            65   |               |
| supercelda |   20251210 | 20251210-210427 |  20251210210427 |           3.45 |               419 |                 22 |            65   |               |
| supercelda |   20251210 | 20251210-215618 |  20251210215618 |           3.46 |               539 |                 33 |            65   |               |
| supercelda |   20251211 | 20251211-224537 |  20251211224537 |           3.45 |               226 |                 45 |            65   |               |
| supercelda |   20260105 | 20260105-204718 |  20260105204718 |           3.44 |               197 |                 36 |            65   |               |
| supercelda |   20260123 | 20260123-013026 |  20260123013026 |           3.44 |               207 |                 14 |            65   |               |
| supercelda |   20251128 | 20251128-010529 |  20251128010529 |           3.46 |               294 |                 32 |            65   |               |
| supercelda |   20260114 | 20260114-183957 |  20260114183957 |           3.43 |               313 |                 14 |            65   |               |
| supercelda |   20251128 | 20251128-022456 |  20251128022456 |           3.45 |               658 |                 37 |            65   |               |
| supercelda |   20251128 | 20251128-031709 |  20251128031709 |           3.45 |               929 |                107 |            65   |               |
| supercelda |   20251128 | 20251128-050437 |  20251128050437 |           3.45 |               457 |                 28 |            65   |               |
| supercelda |   20260114 | 20260114-193130 |  20260114193130 |           3.46 |               252 |                 18 |            65   |               |
| supercelda |   20251204 | 20251204-191358 |  20251204191358 |           3.47 |               153 |                 40 |            65   |               |
| supercelda |   20251204 | 20251204-200608 |  20251204200608 |           3.47 |               166 |                 76 |            65   |               |
| supercelda |   20251204 | 20251204-205445 |  20251204205445 |           3.47 |               237 |                 50 |            65   |               |
| supercelda |   20251226 | 20251226-182621 |  20251226182621 |           3.45 |               217 |                  6 |            65   |               |
| supercelda |   20251214 | 20251214-003257 |  20251214003257 |           3.45 |               398 |                 66 |            65   |               |
| supercelda |   20251229 | 20251229-215525 |  20251229215525 |           3.45 |               291 |                 39 |            65   |               |
| supercelda |   20260108 | 20260108-032734 |  20260108032734 |           3.43 |               385 |                 54 |            65   |               |


## todas (n=77)

|   horizonte |   HailCast MAE_dBZ |   HailCast SSIM |   HailCast CSI35_pooled |   HailCast CSI55_pooled |   HailCast CSI35_mean |   HailCast CSI55_mean |   Persistencia MAE_dBZ |   Persistencia SSIM |   Persistencia CSI35_pooled |   Persistencia CSI55_pooled |   Persistencia CSI35_mean |   Persistencia CSI55_mean |
|------------:|-------------------:|----------------:|------------------------:|------------------------:|----------------------:|----------------------:|-----------------------:|--------------------:|----------------------------:|----------------------------:|--------------------------:|--------------------------:|
|           1 |              1.119 |           0.926 |                   0.483 |                   0.226 |                 0.432 |                 0.168 |                  1.183 |               0.93  |                       0.495 |                       0.308 |                     0.5   |                     0.306 |
|           2 |              1.166 |           0.924 |                   0.411 |                   0.161 |                 0.376 |                 0.117 |                  1.278 |               0.928 |                       0.366 |                       0.226 |                     0.386 |                     0.247 |
|           3 |              1.225 |           0.923 |                   0.364 |                   0.123 |                 0.333 |                 0.087 |                  1.366 |               0.926 |                       0.287 |                       0.186 |                     0.314 |                     0.221 |
|           4 |              1.303 |           0.921 |                   0.308 |                   0.09  |                 0.285 |                 0.059 |                  1.452 |               0.924 |                       0.231 |                       0.165 |                     0.256 |                     0.198 |
|           5 |              1.381 |           0.919 |                   0.254 |                   0.04  |                 0.237 |                 0.029 |                  1.526 |               0.922 |                       0.201 |                       0.163 |                     0.235 |                     0.199 |
|           6 |              1.472 |           0.918 |                   0.192 |                   0.015 |                 0.184 |                 0.014 |                  1.605 |               0.921 |                       0.171 |                       0.161 |                     0.206 |                     0.2   |
|           7 |              1.527 |           0.917 |                   0.146 |                   0.004 |                 0.143 |                 0.002 |                  1.653 |               0.919 |                       0.151 |                       0.154 |                     0.187 |                     0.189 |


## adveccion (n=20)

|   horizonte |   HailCast MAE_dBZ |   HailCast SSIM |   HailCast CSI35_pooled |   HailCast CSI55_pooled |   HailCast CSI35_mean |   HailCast CSI55_mean |   Persistencia MAE_dBZ |   Persistencia SSIM |   Persistencia CSI35_pooled |   Persistencia CSI55_pooled |   Persistencia CSI35_mean |   Persistencia CSI55_mean |
|------------:|-------------------:|----------------:|------------------------:|------------------------:|----------------------:|----------------------:|-----------------------:|--------------------:|----------------------------:|----------------------------:|--------------------------:|--------------------------:|
|           1 |              1.029 |           0.928 |                   0.459 |                   0.225 |                 0.435 |                 0.179 |                  1.07  |               0.934 |                       0.49  |                       0.322 |                     0.488 |                     0.327 |
|           2 |              1.064 |           0.928 |                   0.409 |                   0.16  |                 0.392 |                 0.131 |                  1.156 |               0.932 |                       0.37  |                       0.237 |                     0.373 |                     0.255 |
|           3 |              1.128 |           0.926 |                   0.364 |                   0.115 |                 0.352 |                 0.1   |                  1.25  |               0.929 |                       0.284 |                       0.188 |                     0.298 |                     0.226 |
|           4 |              1.195 |           0.924 |                   0.312 |                   0.101 |                 0.301 |                 0.086 |                  1.334 |               0.928 |                       0.233 |                       0.174 |                     0.253 |                     0.215 |
|           5 |              1.237 |           0.923 |                   0.259 |                   0.045 |                 0.254 |                 0.039 |                  1.387 |               0.926 |                       0.193 |                       0.159 |                     0.224 |                     0.187 |
|           6 |              1.324 |           0.922 |                   0.195 |                   0.015 |                 0.199 |                 0.02  |                  1.462 |               0.925 |                       0.166 |                       0.167 |                     0.194 |                     0.19  |
|           7 |              1.388 |           0.921 |                   0.144 |                   0.003 |                 0.152 |                 0.001 |                  1.527 |               0.923 |                       0.144 |                       0.158 |                     0.171 |                     0.179 |


## debil (n=15)

|   horizonte |   HailCast MAE_dBZ |   HailCast SSIM |   HailCast CSI35_pooled |   HailCast CSI55_pooled |   HailCast CSI35_mean |   HailCast CSI55_mean |   Persistencia MAE_dBZ |   Persistencia SSIM |   Persistencia CSI35_pooled |   Persistencia CSI55_pooled |   Persistencia CSI35_mean |   Persistencia CSI55_mean |
|------------:|-------------------:|----------------:|------------------------:|------------------------:|----------------------:|----------------------:|-----------------------:|--------------------:|----------------------------:|----------------------------:|--------------------------:|--------------------------:|
|           1 |              0.528 |           0.947 |                   0.311 |                   0.063 |                 0.262 |                 0.04  |                  0.509 |               0.954 |                       0.519 |                       0.358 |                     0.516 |                     0.35  |
|           2 |              0.565 |           0.945 |                   0.277 |                   0.04  |                 0.232 |                 0.026 |                  0.559 |               0.952 |                       0.439 |                       0.348 |                     0.452 |                     0.347 |
|           3 |              0.598 |           0.944 |                   0.239 |                   0.031 |                 0.198 |                 0.016 |                  0.603 |               0.952 |                       0.374 |                       0.366 |                     0.403 |                     0.357 |
|           4 |              0.664 |           0.943 |                   0.18  |                   0.011 |                 0.167 |                 0.005 |                  0.688 |               0.949 |                       0.268 |                       0.278 |                     0.307 |                     0.297 |
|           5 |              0.708 |           0.941 |                   0.161 |                   0.009 |                 0.137 |                 0.007 |                  0.711 |               0.949 |                       0.3   |                       0.344 |                     0.338 |                     0.349 |
|           6 |              0.726 |           0.942 |                   0.128 |                   0.004 |                 0.112 |                 0.006 |                  0.746 |               0.948 |                       0.255 |                       0.32  |                     0.32  |                     0.346 |
|           7 |              0.756 |           0.94  |                   0.107 |                   0.005 |                 0.087 |                 0.003 |                  0.773 |               0.946 |                       0.254 |                       0.335 |                     0.306 |                     0.328 |


## multicelda (n=21)

|   horizonte |   HailCast MAE_dBZ |   HailCast SSIM |   HailCast CSI35_pooled |   HailCast CSI55_pooled |   HailCast CSI35_mean |   HailCast CSI55_mean |   Persistencia MAE_dBZ |   Persistencia SSIM |   Persistencia CSI35_pooled |   Persistencia CSI55_pooled |   Persistencia CSI35_mean |   Persistencia CSI55_mean |
|------------:|-------------------:|----------------:|------------------------:|------------------------:|----------------------:|----------------------:|-----------------------:|--------------------:|----------------------------:|----------------------------:|--------------------------:|--------------------------:|
|           1 |              1.665 |           0.907 |                   0.512 |                   0.225 |                 0.482 |                 0.187 |                  1.815 |               0.909 |                       0.479 |                       0.258 |                     0.476 |                     0.252 |
|           2 |              1.742 |           0.904 |                   0.42  |                   0.147 |                 0.404 |                 0.122 |                  1.969 |               0.904 |                       0.334 |                       0.163 |                     0.332 |                     0.16  |
|           3 |              1.81  |           0.902 |                   0.374 |                   0.106 |                 0.357 |                 0.084 |                  2.078 |               0.902 |                       0.261 |                       0.137 |                     0.257 |                     0.147 |
|           4 |              1.95  |           0.898 |                   0.315 |                   0.051 |                 0.301 |                 0.037 |                  2.218 |               0.898 |                       0.205 |                       0.122 |                     0.204 |                     0.128 |
|           5 |              2.096 |           0.895 |                   0.255 |                   0.021 |                 0.241 |                 0.015 |                  2.353 |               0.896 |                       0.17  |                       0.134 |                     0.173 |                     0.15  |
|           6 |              2.265 |           0.893 |                   0.186 |                   0.012 |                 0.18  |                 0.011 |                  2.486 |               0.894 |                       0.147 |                       0.13  |                     0.146 |                     0.146 |
|           7 |              2.342 |           0.893 |                   0.139 |                   0.004 |                 0.128 |                 0.003 |                  2.534 |               0.892 |                       0.127 |                       0.126 |                     0.13  |                     0.137 |


## supercelda (n=21)

|   horizonte |   HailCast MAE_dBZ |   HailCast SSIM |   HailCast CSI35_pooled |   HailCast CSI55_pooled |   HailCast CSI35_mean |   HailCast CSI55_mean |   Persistencia MAE_dBZ |   Persistencia SSIM |   Persistencia CSI35_pooled |   Persistencia CSI55_pooled |   Persistencia CSI35_mean |   Persistencia CSI55_mean |
|------------:|-------------------:|----------------:|------------------------:|------------------------:|----------------------:|----------------------:|-----------------------:|--------------------:|----------------------------:|----------------------------:|--------------------------:|--------------------------:|
|           1 |              1.08  |           0.928 |                   0.519 |                   0.296 |                 0.503 |                 0.228 |                  1.141 |               0.932 |                       0.521 |                       0.324 |                     0.524 |                     0.31  |
|           2 |              1.117 |           0.927 |                   0.447 |                   0.221 |                 0.435 |                 0.164 |                  1.217 |               0.93  |                       0.395 |                       0.228 |                     0.406 |                     0.254 |
|           3 |              1.181 |           0.927 |                   0.393 |                   0.181 |                 0.389 |                 0.126 |                  1.31  |               0.929 |                       0.306 |                       0.161 |                     0.323 |                     0.194 |
|           4 |              1.216 |           0.925 |                   0.347 |                   0.149 |                 0.339 |                 0.095 |                  1.344 |               0.927 |                       0.263 |                       0.148 |                     0.275 |                     0.183 |
|           5 |              1.284 |           0.924 |                   0.293 |                   0.067 |                 0.288 |                 0.049 |                  1.412 |               0.926 |                       0.224 |                       0.119 |                     0.236 |                     0.153 |
|           6 |              1.352 |           0.922 |                   0.23  |                   0.022 |                 0.225 |                 0.019 |                  1.472 |               0.925 |                       0.185 |                       0.121 |                     0.197 |                     0.158 |
|           7 |              1.396 |           0.922 |                   0.181 |                   0.003 |                 0.189 |                 0.001 |                  1.519 |               0.924 |                       0.159 |                       0.105 |                     0.174 |                     0.15  |


## Bootstrap de la diferencia HailCast − Persistencia (1000 remuestreos de secuencias, IC 95 %)

Si el intervalo no incluye el cero, la diferencia es consistente en la muestra.


### todas (n=77)

- CSI35_pooled a t+1 (3.6 min): −0.012, IC 95 % [-0.037, +0.012]
- CSI35_pooled a t+4 (14.3 min): +0.077, IC 95 % [+0.054, +0.098]  ← no incluye el cero
- CSI35_pooled a t+7 (25.0 min): −0.004, IC 95 % [-0.024, +0.013]
- CSI55_pooled a t+1 (3.6 min): −0.081, IC 95 % [-0.125, -0.045]  ← no incluye el cero
- CSI55_pooled a t+4 (14.3 min): −0.075, IC 95 % [-0.126, -0.028]  ← no incluye el cero
- CSI55_pooled a t+7 (25.0 min): −0.151, IC 95 % [-0.181, -0.125]  ← no incluye el cero
- MAE_dBZ a t+1 (3.6 min): −0.064, IC 95 % [-0.086, -0.045]  ← no incluye el cero
- MAE_dBZ a t+4 (14.3 min): −0.149, IC 95 % [-0.181, -0.120]  ← no incluye el cero
- MAE_dBZ a t+7 (25.0 min): −0.125, IC 95 % [-0.155, -0.097]  ← no incluye el cero
- SSIM a t+1 (3.6 min): −0.005, IC 95 % [-0.005, -0.004]  ← no incluye el cero
- SSIM a t+4 (14.3 min): −0.003, IC 95 % [-0.004, -0.002]  ← no incluye el cero
- SSIM a t+7 (25.0 min): −0.002, IC 95 % [-0.003, -0.001]  ← no incluye el cero

### adveccion (n=20)

- CSI35_pooled a t+1 (3.6 min): −0.031, IC 95 % [-0.076, +0.014]
- CSI35_pooled a t+4 (14.3 min): +0.079, IC 95 % [+0.035, +0.123]  ← no incluye el cero
- CSI35_pooled a t+7 (25.0 min): +0.000, IC 95 % [-0.032, +0.027]
- CSI55_pooled a t+1 (3.6 min): −0.097, IC 95 % [-0.171, -0.018]  ← no incluye el cero
- CSI55_pooled a t+4 (14.3 min): −0.074, IC 95 % [-0.155, +0.007]
- CSI55_pooled a t+7 (25.0 min): −0.155, IC 95 % [-0.197, -0.117]  ← no incluye el cero
- MAE_dBZ a t+1 (3.6 min): −0.041, IC 95 % [-0.077, -0.012]  ← no incluye el cero
- MAE_dBZ a t+4 (14.3 min): −0.138, IC 95 % [-0.197, -0.096]  ← no incluye el cero
- MAE_dBZ a t+7 (25.0 min): −0.139, IC 95 % [-0.195, -0.090]  ← no incluye el cero

### debil (n=15)

- CSI35_pooled a t+1 (3.6 min): −0.208, IC 95 % [-0.268, -0.157]  ← no incluye el cero
- CSI35_pooled a t+4 (14.3 min): −0.088, IC 95 % [-0.183, -0.026]  ← no incluye el cero
- CSI35_pooled a t+7 (25.0 min): −0.146, IC 95 % [-0.251, -0.066]  ← no incluye el cero
- CSI55_pooled a t+1 (3.6 min): −0.295, IC 95 % [-0.366, -0.229]  ← no incluye el cero
- CSI55_pooled a t+4 (14.3 min): −0.268, IC 95 % [-0.340, -0.205]  ← no incluye el cero
- CSI55_pooled a t+7 (25.0 min): −0.330, IC 95 % [-0.383, -0.289]  ← no incluye el cero
- MAE_dBZ a t+1 (3.6 min): +0.019, IC 95 % [-0.013, +0.043]
- MAE_dBZ a t+4 (14.3 min): −0.024, IC 95 % [-0.063, +0.012]
- MAE_dBZ a t+7 (25.0 min): −0.017, IC 95 % [-0.063, +0.024]

### multicelda (n=21)

- CSI35_pooled a t+1 (3.6 min): +0.033, IC 95 % [-0.002, +0.065]
- CSI35_pooled a t+4 (14.3 min): +0.109, IC 95 % [+0.078, +0.136]  ← no incluye el cero
- CSI35_pooled a t+7 (25.0 min): +0.011, IC 95 % [-0.020, +0.041]
- CSI55_pooled a t+1 (3.6 min): −0.033, IC 95 % [-0.103, +0.025]
- CSI55_pooled a t+4 (14.3 min): −0.072, IC 95 % [-0.120, -0.029]  ← no incluye el cero
- CSI55_pooled a t+7 (25.0 min): −0.123, IC 95 % [-0.164, -0.084]  ← no incluye el cero
- MAE_dBZ a t+1 (3.6 min): −0.150, IC 95 % [-0.195, -0.113]  ← no incluye el cero
- MAE_dBZ a t+4 (14.3 min): −0.269, IC 95 % [-0.329, -0.209]  ← no incluye el cero
- MAE_dBZ a t+7 (25.0 min): −0.192, IC 95 % [-0.270, -0.115]  ← no incluye el cero

### supercelda (n=21)

- CSI35_pooled a t+1 (3.6 min): −0.002, IC 95 % [-0.033, +0.023]
- CSI35_pooled a t+4 (14.3 min): +0.084, IC 95 % [+0.043, +0.118]  ← no incluye el cero
- CSI35_pooled a t+7 (25.0 min): +0.023, IC 95 % [-0.008, +0.057]
- CSI55_pooled a t+1 (3.6 min): −0.028, IC 95 % [-0.093, +0.024]
- CSI55_pooled a t+4 (14.3 min): +0.000, IC 95 % [-0.120, +0.100]
- CSI55_pooled a t+7 (25.0 min): −0.102, IC 95 % [-0.151, -0.070]  ← no incluye el cero
- MAE_dBZ a t+1 (3.6 min): −0.060, IC 95 % [-0.085, -0.038]  ← no incluye el cero
- MAE_dBZ a t+4 (14.3 min): −0.128, IC 95 % [-0.169, -0.086]  ← no incluye el cero
- MAE_dBZ a t+7 (25.0 min): −0.123, IC 95 % [-0.163, -0.080]  ← no incluye el cero
