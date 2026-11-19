# HailCast 🌩️

**Deep Learning aplicado a la predicción de tormentas en imágenes de radar (nowcasting de granizo para Mendoza, Argentina).**

HailCast es un sistema integral de predicción inmediata (*nowcasting*) que anticipa la evolución a corto plazo (~15 minutos) de tormentas convectivas severas a partir de la reflectividad del radar meteorológico. Su núcleo es una red neuronal recurrente-convolucional **ConvLSTM3D_Enhanced** que, dados los últimos cuadros del radar, predice los próximos cuadros de evolución de la tormenta, superando la limitación de linealidad de los métodos tradicionales de extrapolación (TITAN / flujo óptico).

El proyecto abarca el ciclo completo de ingeniería de un sistema de ML: ingeniería de datos meteorológicos, entrenamiento del modelo, pipeline de inferencia en producción, backend de servicios y una aplicación web geoespacial para visualización y alertas.

> Trabajo Final de Grado — **Ingeniería en Informática**, Facultad de Ingeniería, **Universidad de Mendoza** (2025). Tesis defendida y aprobada.

---

## El problema

Mendoza sostiene una economía de agricultura intensiva (vitivinicultura y fruticultura) bajo la amenaza constante de tormentas convectivas severas y granizo. La mitigación y alerta han dependido históricamente de sistemas de **extrapolación lagrangiana como TITAN**, que asumen que las tormentas se desplazan sin cambiar de forma ni intensidad (persistencia lineal). Esto falla justo cuando más importa: en la **génesis explosiva** y el **decaimiento rápido** de las celdas, procesos termodinámicos no lineales que TITAN no puede modelar y para los que la habilidad de pronóstico decae más allá de los 30 minutos.

HailCast reformula el problema como un **pronóstico de secuencias espaciotemporales** (siguiendo a Shi et al., 2015): un modelo aprendido que captura la dinámica no lineal de crecimiento, advección y disipación a partir del archivo histórico de radar. Aplicación práctica directa: dar aviso temprano a los operativos de **lucha antigranizo** (aviones de siembra) cuando el modelo detecta la formación de una tormenta severa.

---

## Arquitectura

```
   Radar (MDV)                                          Aplicación Web
       │                                                      ▲
       ▼                                                      │
 ┌───────────┐   NetCDF   ┌──────────────┐  NetCDF  ┌──────────────────┐
 │  LROSE    │──────────► │ ConvLSTM3D    │────────► │  Backend (Flask) │
 │ Mdv2NetCDF│            │  _Enhanced    │          │  API + Worker    │
 └───────────┘            │  (PyTorch)    │          └──────────────────┘
   conversión             └──────────────┘            pre-render PNG+bounds
   y grillado             12 in → 5 out                 estado, auth, push
                                                              │
                                                              ▼
                                                     Next.js + MapLibre GL
```

**Flujo de inferencia:** ingesta de volúmenes de radar `.mdv` → conversión a NetCDF con **LROSE** (`Mdv2NetCDF`) → preprocesado (downsampling y normalización) → predicción con el modelo → post-procesado (umbralización física, y `NcGeneric2Mdv` para cerrar el ciclo hacia TITAN) → pre-renderizado de PNG georreferenciados → visualización en el frontend y despacho de alertas push.

Patrón desacoplado **productor–consumidor**: un *Pipeline Worker* detecta nuevos MDV, ejecuta la conversión e inferencia (pesado), y la **API Flask** sirve resultados (ligero). **SQLite en modo WAL** sincroniza estado entre ambos. Todo containerizado en Docker; el sistema corre en un VPS (inferencia en CPU) y el frontend en Vercel.

---

## Stack tecnológico

| Capa | Tecnologías |
| :--- | :--- |
| **Modelo / ML** | PyTorch, ConvLSTM3D_Enhanced, xarray, NetCDF4, NumPy |
| **Backend** | Python, Flask, worker de inferencia, JWT + Google OAuth, Web Push (VAPID), SQLite (WAL) |
| **Procesamiento radar** | [LROSE](https://github.com/NCAR/lrose-core) (Mdv2NetCDF, NcGeneric2Mdv), PyART; render con Matplotlib + Cartopy |
| **Frontend** | Next.js, TypeScript, React, **MapLibre GL JS** (WebGL), Tailwind CSS |
| **Infraestructura** | Docker / Docker Compose, Vast.ai (entrenamiento GPU), VPS (inferencia CPU), Vercel |

---

## El modelo: ConvLSTM3D_Enhanced

El modelo predice secuencias tomando **12 cuadros de entrada** (~36–48 min de historia) y prediciendo **5 cuadros de salida** (~15–20 min de futuro), con cadencia temporal de 3–4 min por cuadro.

**Representación de entrada:** en lugar de procesar el volumen vertical completo, el modelo trabaja sobre **composites de reflectividad de 250×250** (obtenidos por *downsampling* 2× desde la grilla nativa de 500×500), usando **el tiempo como tercera dimensión** de la convolución 3D. Así, las capas ConvLSTM/Conv3D aprenden simultáneamente la estructura espacial de la tormenta y su evolución temporal.

**Topología:** Encoder–Decoder apilado de **3 capas con 128 canales ocultos**, kernels `3×3×3`, **conexiones residuales (skip connections)** para estabilizar el entrenamiento profundo, **GroupNorm** para reducir la dependencia del tamaño de lote, y capa de salida `Conv3D` con activación **Sigmoid** consistente con la normalización de reflectividad.

**Función de pérdida híbrida (`CombinedLoss`):**
1. **Huber** — robustez ante la distribución de cola pesada de la reflectividad (outliers de granizo).
2. **Weighted Penalty** — penalización asimétrica (`high_penalty_weight`) sobre píxeles que superan el umbral normalizado 0.75 (≈ 41.5 dBZ), para priorizar núcleos convectivos severos frente al desbalance de clases.
3. **SSIM** — coherencia estructural, para recuperar la morfología nítida de las celdas y combatir el *difuminado*.

**Curriculum Learning:** entrenamiento por fases que primero prioriza **intensidad y ubicación** (penalización alta, SSIM moderado) y luego **refina la estructura y advección** (más peso a SSIM). Modelo final seleccionado por *early stopping* (época 31). Optimizador Adam (LR `1e-5`, `ReduceLROnPlateau`), *batch size* 2.

**Datos y entrenamiento:** archivo histórico del radar **San Rafael (La Llave)**, Banda S, de la DACC de Mendoza. Dominio restringido al período **2006–2008** para garantizar homogeneidad de metadatos físicos (`scale_factor`/`add_offset`). ~200 secuencias de 17 volúmenes, aumentadas con **ventanas deslizantes** (stride 5 y 8) y balanceadas por morfología (unicelda, multicelda, supercelda). Entrenamiento final en **NVIDIA H200 (141 GB VRAM)**.

> El pipeline completo de entrenamiento está documentado en [`training/README.md`](training/README.md).

---

## Estructura del repositorio

```
├── backend/        API Flask, worker de inferencia, servicios (auth, push, alertas)
│   ├── model/      arquitectura ConvLSTM3D_Enhanced y lógica de predicción
│   ├── lrose_params/  parámetros de conversión LROSE (Mdv2NetCDF, etc.)
│   └── worker/     pipeline de procesamiento MDV → predicción
├── frontend/       aplicación Next.js (mapa MapLibre, story mode, alertas)
├── training/       pipeline de entrenamiento del modelo (Vast.ai)
├── tools/          scripts de operación (bridge de radar, watchers de Drive)
├── scripts/        utilidades de despliegue y telemetría
├── Dockerfile      imagen GPU
├── Dockerfile.cpu  imagen CPU (VPS de producción)
└── docker-compose*.yml
```

---

## Cómo ejecutarlo

Requisitos: Docker y Docker Compose.

```bash
# 1. Configurar variables de entorno (ver backend/.env.example)
cp backend/.env.example backend/.env
#    editar SECRET_KEY, claves VAPID, etc.

# 2. Colocar el modelo entrenado (.pth) en ./model/

# 3. Levantar el stack en modo CPU (frontend + backend + inferencia)
docker compose -f docker-compose.cpu.yml up --build
```

- Frontend: http://localhost:3000
- API: http://localhost:8000

Para inferencia acelerada por GPU, usar `docker-compose.yml` (requiere NVIDIA Container Toolkit).

---

## Funcionalidades destacadas

- 🗺️ **Mapa interactivo** de Mendoza (MapLibre GL / WebGL) con la reflectividad del radar y las predicciones superpuestas como capas georreferenciadas de opacidad variable.
- ⏱️ **Línea de tiempo continua** que fusiona el pasado observado con el futuro predicho; el indicador cambia de color (amarillo → verde) al pasar a los cuadros generados por IA, señalando que son estimación y no observación.
- ✈️ **Story mode** con replay sincronizado de la telemetría de los aviones antigranizo, mostrando cómo el modelo detecta la tormenta justo cuando despegan.
- 🔔 **Alertas push** (Web Push / VAPID) ante detección de tormentas severas.
- 🔐 Autenticación con JWT y Google OAuth, con roles (usuarios / administradores).

---

## Resultados

La validación fue cualitativa, reconvirtiendo las predicciones a MDV para compararlas "lado a lado" contra la observación real y los pronósticos de TITAN. El modelo demostró capacidad para **predecir cambios de forma y extensión** de las celdas (no solo trasladar un contorno rígido), **anticipar advección no lineal** con posición de núcleos coincidente con la realidad, y **reconstruir coherentemente la estructura del núcleo** de tormenta. En sistemas multicelulares complejos persisten algunos artefactos de ruido en niveles bajos, mitigables por filtro de elevación — línea de trabajo futura.

---

## Autor y contexto académico

**Federico Raúl Caballero**
Trabajo Final de Grado — Ingeniería en Informática, Facultad de Ingeniería, Universidad de Mendoza (2025).
Asesor especialista: Ing. Gabriel Federico Pulido · Profesora a cargo: Mgter. María Marta Arrieta Guevara.

Datos históricos de radar cedidos por el Dr. Ing. Horacio Gabriel Pessano. Sistema desarrollado para la provincia de Mendoza, Argentina.
