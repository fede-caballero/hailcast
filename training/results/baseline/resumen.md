# HailCast vs persistencia — 13 secuencias de validación

Generado 2026-11-12 18:05 por training/baseline_persistencia.py.

**Criterio del CSI:** pooled (se suman aciertos, falsas alarmas y fallos de todas las secuencias y se divide una vez), operador `>=`, umbrales 35 y 55 dBZ. Se incluye también el promedio por secuencia (`*_mean`, ignora indefinidos) para comparar con evaluate_cases.py.

**Persistencia:** último frame observado de la ventana de entrada repetido 7 veces, con el mismo post-procesado que el producto del modelo (< 30 dBZ → -29 dBZ). Mismas secuencias, mismos 7 targets, dBZ, misma máscara (NaN → −29 dBZ, recorte a [−29, 65]) y mismo código de métricas (`evaluate_cases.compute_metrics`).

Cadencia media del radar en estas secuencias: 3.45 min por scan (t+1 … t+7 = 3.4 … 24.1 min).

Secuencias por tipología: {'adveccion': 5, 'multicelda': 4, 'supercelda': 4}.


## todas (n=13)

|   horizonte |   HailCast MAE_dBZ |   HailCast SSIM |   HailCast CSI35_pooled |   HailCast CSI55_pooled |   HailCast CSI35_mean |   HailCast CSI55_mean |   Persistencia MAE_dBZ |   Persistencia SSIM |   Persistencia CSI35_pooled |   Persistencia CSI55_pooled |   Persistencia CSI35_mean |   Persistencia CSI55_mean |
|------------:|-------------------:|----------------:|------------------------:|------------------------:|----------------------:|----------------------:|-----------------------:|--------------------:|----------------------------:|----------------------------:|--------------------------:|--------------------------:|
|           1 |              1.153 |           0.925 |                   0.496 |                   0.234 |                 0.48  |                 0.192 |                  1.222 |               0.929 |                       0.522 |                       0.322 |                     0.538 |                     0.325 |
|           2 |              1.245 |           0.922 |                   0.418 |                   0.165 |                 0.421 |                 0.15  |                  1.37  |               0.925 |                       0.364 |                       0.177 |                     0.382 |                     0.202 |
|           3 |              1.287 |           0.921 |                   0.382 |                   0.132 |                 0.386 |                 0.11  |                  1.427 |               0.923 |                       0.313 |                       0.137 |                     0.335 |                     0.173 |
|           4 |              1.365 |           0.919 |                   0.337 |                   0.116 |                 0.341 |                 0.101 |                  1.529 |               0.92  |                       0.237 |                       0.13  |                     0.258 |                     0.169 |
|           5 |              1.434 |           0.917 |                   0.28  |                   0.057 |                 0.297 |                 0.067 |                  1.597 |               0.92  |                       0.199 |                       0.134 |                     0.233 |                     0.182 |
|           6 |              1.575 |           0.915 |                   0.206 |                   0.025 |                 0.237 |                 0.036 |                  1.706 |               0.917 |                       0.166 |                       0.124 |                     0.193 |                     0.161 |
|           7 |              1.61  |           0.914 |                   0.169 |                   0.002 |                 0.193 |                 0.001 |                  1.748 |               0.916 |                       0.143 |                       0.128 |                     0.173 |                     0.167 |


## multicelda (n=4)

|   horizonte |   HailCast MAE_dBZ |   HailCast SSIM |   HailCast CSI35_pooled |   HailCast CSI55_pooled |   HailCast CSI35_mean |   HailCast CSI55_mean |   Persistencia MAE_dBZ |   Persistencia SSIM |   Persistencia CSI35_pooled |   Persistencia CSI55_pooled |   Persistencia CSI35_mean |   Persistencia CSI55_mean |
|------------:|-------------------:|----------------:|------------------------:|------------------------:|----------------------:|----------------------:|-----------------------:|--------------------:|----------------------------:|----------------------------:|--------------------------:|--------------------------:|
|           1 |              1.384 |           0.916 |                   0.502 |                   0.152 |                 0.476 |                 0.113 |                  1.496 |               0.919 |                       0.521 |                       0.307 |                     0.503 |                     0.321 |
|           2 |              1.594 |           0.909 |                   0.378 |                   0.061 |                 0.37  |                 0.043 |                  1.75  |               0.912 |                       0.344 |                       0.17  |                     0.33  |                     0.171 |
|           3 |              1.505 |           0.912 |                   0.364 |                   0.07  |                 0.346 |                 0.051 |                  1.686 |               0.913 |                       0.32  |                       0.168 |                     0.309 |                     0.189 |
|           4 |              1.637 |           0.907 |                   0.318 |                   0.058 |                 0.301 |                 0.039 |                  1.853 |               0.908 |                       0.236 |                       0.18  |                     0.22  |                     0.193 |
|           5 |              1.699 |           0.907 |                   0.248 |                   0.018 |                 0.234 |                 0.015 |                  1.922 |               0.91  |                       0.181 |                       0.199 |                     0.182 |                     0.25  |
|           6 |              1.995 |           0.902 |                   0.172 |                   0.007 |                 0.171 |                 0.006 |                  2.136 |               0.905 |                       0.165 |                       0.185 |                     0.163 |                     0.239 |
|           7 |              1.906 |           0.904 |                   0.143 |                   0.006 |                 0.127 |                 0.005 |                  2.064 |               0.906 |                       0.145 |                       0.204 |                     0.142 |                     0.257 |


## supercelda (n=4)

|   horizonte |   HailCast MAE_dBZ |   HailCast SSIM |   HailCast CSI35_pooled |   HailCast CSI55_pooled |   HailCast CSI35_mean |   HailCast CSI55_mean |   Persistencia MAE_dBZ |   Persistencia SSIM |   Persistencia CSI35_pooled |   Persistencia CSI55_pooled |   Persistencia CSI35_mean |   Persistencia CSI55_mean |
|------------:|-------------------:|----------------:|------------------------:|------------------------:|----------------------:|----------------------:|-----------------------:|--------------------:|----------------------------:|----------------------------:|--------------------------:|--------------------------:|
|           1 |              1.072 |           0.93  |                   0.506 |                   0.205 |                 0.519 |                 0.22  |                  1.115 |               0.934 |                       0.586 |                       0.307 |                     0.594 |                     0.306 |
|           2 |              1.127 |           0.929 |                   0.457 |                   0.193 |                 0.47  |                 0.194 |                  1.184 |               0.933 |                       0.467 |                       0.241 |                     0.469 |                     0.247 |
|           3 |              1.196 |           0.928 |                   0.406 |                   0.149 |                 0.427 |                 0.145 |                  1.249 |               0.932 |                       0.395 |                       0.185 |                     0.397 |                     0.189 |
|           4 |              1.253 |           0.926 |                   0.35  |                   0.151 |                 0.374 |                 0.146 |                  1.307 |               0.929 |                       0.318 |                       0.176 |                     0.323 |                     0.185 |
|           5 |              1.306 |           0.925 |                   0.32  |                   0.136 |                 0.342 |                 0.13  |                  1.359 |               0.929 |                       0.273 |                       0.171 |                     0.278 |                     0.183 |
|           6 |              1.32  |           0.924 |                   0.282 |                   0.05  |                 0.295 |                 0.044 |                  1.355 |               0.928 |                       0.237 |                       0.167 |                     0.24  |                     0.179 |
|           7 |              1.412 |           0.923 |                   0.237 |                   0     |                 0.244 |                 0     |                  1.45  |               0.926 |                       0.198 |                       0.141 |                     0.205 |                     0.158 |


## adveccion (n=5)

|   horizonte |   HailCast MAE_dBZ |   HailCast SSIM |   HailCast CSI35_pooled |   HailCast CSI55_pooled |   HailCast CSI35_mean |   HailCast CSI55_mean |   Persistencia MAE_dBZ |   Persistencia SSIM |   Persistencia CSI35_pooled |   Persistencia CSI55_pooled |   Persistencia CSI35_mean |   Persistencia CSI55_mean |
|------------:|-------------------:|----------------:|------------------------:|------------------------:|----------------------:|----------------------:|-----------------------:|--------------------:|----------------------------:|----------------------------:|--------------------------:|--------------------------:|
|           1 |              1.034 |           0.927 |                   0.483 |                   0.298 |                 0.453 |                 0.232 |                  1.087 |               0.932 |                       0.485 |                       0.341 |                     0.522 |                     0.342 |
|           2 |              1.062 |           0.927 |                   0.447 |                   0.248 |                 0.422 |                 0.201 |                  1.215 |               0.928 |                       0.329 |                       0.153 |                     0.354 |                     0.19  |
|           3 |              1.186 |           0.922 |                   0.39  |                   0.162 |                 0.386 |                 0.13  |                  1.363 |               0.924 |                       0.257 |                       0.093 |                     0.307 |                     0.148 |
|           4 |              1.238 |           0.922 |                   0.351 |                   0.14  |                 0.346 |                 0.115 |                  1.448 |               0.923 |                       0.193 |                       0.069 |                     0.237 |                     0.137 |
|           5 |              1.326 |           0.919 |                   0.29  |                   0.05  |                 0.312 |                 0.058 |                  1.528 |               0.92  |                       0.174 |                       0.074 |                     0.237 |                     0.126 |
|           6 |              1.442 |           0.918 |                   0.203 |                   0.031 |                 0.243 |                 0.053 |                  1.641 |               0.918 |                       0.125 |                       0.048 |                     0.179 |                     0.083 |
|           7 |              1.531 |           0.916 |                   0.154 |                   0     |                 0.204 |                 0     |                  1.734 |               0.916 |                       0.109 |                       0.064 |                     0.172 |                     0.101 |


## Bootstrap de la diferencia HailCast − Persistencia (1000 remuestreos de secuencias, IC 95 %)

Si el intervalo no incluye el cero, la diferencia es consistente en la muestra.

- CSI35_pooled a t+1 (3.4 min): −0.025, IC 95 % [-0.076, +0.019]
- CSI35_pooled a t+4 (13.8 min): +0.100, IC 95 % [+0.047, +0.148]  ← no incluye el cero
- CSI35_pooled a t+7 (24.1 min): +0.026, IC 95 % [-0.010, +0.056]
- CSI55_pooled a t+1 (3.4 min): −0.088, IC 95 % [-0.176, -0.013]  ← no incluye el cero
- CSI55_pooled a t+4 (13.8 min): −0.014, IC 95 % [-0.128, +0.076]
- CSI55_pooled a t+7 (24.1 min): −0.125, IC 95 % [-0.190, -0.078]  ← no incluye el cero
- MAE_dBZ a t+1 (3.4 min): −0.068, IC 95 % [-0.113, -0.028]  ← no incluye el cero
- MAE_dBZ a t+4 (13.8 min): −0.164, IC 95 % [-0.259, -0.093]  ← no incluye el cero
- MAE_dBZ a t+7 (24.1 min): −0.139, IC 95 % [-0.220, -0.065]  ← no incluye el cero
- SSIM a t+1 (3.4 min): −0.004, IC 95 % [-0.006, -0.003]  ← no incluye el cero
- SSIM a t+4 (13.8 min): −0.002, IC 95 % [-0.003, -0.000]  ← no incluye el cero
- SSIM a t+7 (24.1 min): −0.002, IC 95 % [-0.003, +0.000]

## Secuencias usadas

| tipo       |   story_id | pred_subdir     |   last_input_ts |   cadencia_min |   n_inputs_disponibles |
|:-----------|-----------:|:----------------|----------------:|---------------:|-----------------------:|
| multicelda |   20251213 | 20251213-230638 |  20251213230638 |           3.45 |                      8 |
| multicelda |   20251219 | 20251219-220704 |  20251219220704 |           3.47 |                      8 |
| multicelda |   20251221 | 20251221-012431 |  20251221012431 |           3.44 |                      8 |
| multicelda |   20251222 | 20251222-215700 |  20251222215700 |           3.45 |                      8 |
| supercelda |   20251204 | 20251204-205445 |  20251204205445 |           3.47 |                      8 |
| supercelda |   20251226 | 20251226-182621 |  20251226182621 |           3.45 |                     21 |
| supercelda |   20251229 | 20251229-215525 |  20251229215525 |           3.45 |                      8 |
| supercelda |   20260108 | 20260108-032734 |  20260108032734 |           3.43 |                      8 |
| adveccion  |   20260106 | 20260106-174252 |  20260106174252 |           3.45 |                      8 |
| adveccion  |   20251210 | 20251210-201602 |  20251210201602 |           3.46 |                      8 |
| adveccion  |   20260110 | 20260110-191629 |  20260110191629 |           3.44 |                      8 |
| adveccion  |   20260111 | 20260111-231927 |  20260111231927 |           3.44 |                      8 |
| adveccion  |   20260113 | 20260113-030142 |  20260113030142 |           3.44 |                      8 |
