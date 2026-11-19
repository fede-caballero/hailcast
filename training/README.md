# 🧠 Estrategia de Entrenamiento: ConvLSTM3D (Enhanced)

Este documento detalla la estrategia de entrenamiento multietapa diseñada para el modelo **ConvLSTM3D_Enhanced**.

El objetivo principal es superar las limitaciones arquitectónicas de las redes recurrentes convolucionales (tendencia al "difuminado" o *blurriness*) y forzar el aprendizaje de vectores de advección (movimiento) reales, aprovechando la infraestructura de cómputo de alto rendimiento.

---

## ⚙️ Configuración del Entorno y Datos

Para maximizar la estabilidad de los gradientes y la capacidad de generalización, se ha redefinido la configuración base del experimento:

| Componente | Configuración | Justificación |
| :--- | :--- | :--- |
| **Dataset** | **2000 Secuencias** | Dataset masivo balanceado (Advección, Multicelda, Supercelda) para reducir la varianza. |
| **Ratio I/O** | **8 Entrada / 7 Salida** | Relación ~1:1. Provee suficiente contexto histórico (24-32 min) para inferir aceleración. |
| **Resolución** | **250 x 250 x 18** | **Downsampling 2x**. Reduce el consumo de VRAM x4, permitiendo *Batch Sizes* grandes. |
| **Batch Size** | **16 – 32** | Crítico para que `GroupNorm` estabilice los gradientes y evite mínimos locales. |
| **Hardware** | **NVIDIA H200 (141GB)** | Permite alojar el grafo computacional 5D con lotes grandes. |

---

## 📉 Pipeline de Pre-procesamiento y Post-procesamiento

La estrategia se basa en la **optimización dimensional**: entrenar rápido en resolución media y visualizar en alta resolución.

1.  **Ingesta:** NetCDF Original (500x500) $\to$ `AveragePooling2D` $\to$ Tensor (250x250).
2.  **Entrenamiento:** El modelo aprende sobre la grilla de 250px (suficiente para capturar la macro-física de la tormenta).
3.  **Inferencia:** Predicción (250x250) $\to$ `Bicubic Upsampling` $\to$ NetCDF (500x500) $\to$ **TITAN**.

---

## 🚀 Plan de Entrenamiento en 3 Fases

El entrenamiento no es monolítico. Se utiliza **Curriculum Learning** para guiar al modelo desde la detección de intensidad hasta el refinamiento morfológico.

### 🥇 Fase 1: "Burn-in" Físico (Intensidad y Ubicación)
**Objetivo:** Forzar al modelo a predecir núcleos convectivos fuertes en la ubicación correcta, ignorando por ahora la forma exacta.

* **Duración:** 0 $\to$ 25 Épocas (o hasta estabilizar `Val Loss`).
* **Optimizador:** AdamW (`lr=1e-3` con Warmup).

| Hiperparámetro | Valor | Razón Técnica |
| :--- | :---: | :--- |
| `HIGH_PENALTY_WEIGHT` | **100** | Penalización masiva. Obliga a la red a "activarse" ante ecos >40 dBZ. |
| `SSIM_WEIGHT` | **0.1** | Despreciable. Evita que el modelo colapse intentando perfeccionar bordes prematuramente. |
| `LOSS_FUNCTION` | Huber + Weighted | Prioridad a minimizar el error numérico bruto. |

---

### 🥈 Fase 2: Corrección de Advección (El "Anti-Engorde")
**Objetivo Crítico:** Transformar la "mancha que crece" en una "celda que se desplaza". Aquí atacamos el problema del difuminado.

* **Duración:** 26 $\to$ 75 Épocas.
* **Optimizador:** `lr=1e-4` (Scheduler: `ReduceLROnPlateau`, paciencia=4).

| Hiperparámetro | Valor | Razón Técnica |
| :--- | :---: | :--- |
| `HIGH_PENALTY_WEIGHT` | **50** | Se reduce la presión sobre el valor del píxel individual. |
| `SSIM_WEIGHT` | **20** | **Aumento agresivo (x200).** El SSIM penaliza severamente las manchas borrosas. |
| **Efecto Esperado** | Agudeza | Una celda nítida en movimiento tiene mejor *Loss* que una mancha estática. |

> 💡 **Nota:** En esta fase es donde el modelo "aprende a mover" la tormenta. Si el modelo sigue "engordando" las celdas, incrementar `SSIM_WEIGHT` a 30.

---

### 🥉 Fase 3: Refinamiento "High-Frequency"
**Objetivo:** Limpiar artefactos de fondo (ruido en capas bajas) y recuperar picos extremos de granizo.

* **Duración:** 76 $\to$ 100+ Épocas.
* **Optimizador:** `lr=1e-5` (Scheduler: `CosineAnnealing` para convergencia suave).

| Hiperparámetro | Valor | Razón Técnica |
| :--- | :---: | :--- |
| `HIGH_PENALTY_WEIGHT` | **25** | Balance final. |
| `SSIM_WEIGHT` | **30** | Prioridad máxima a la estructura y textura de la tormenta. |
| `GRADIENT_CLIP` | **0.1** | *Clipping* agresivo para evitar saltos bruscos que dañen los pesos finos. |

---

## 📊 Métricas de Éxito (KPIs)

El éxito del entrenamiento no se mide solo por la *Loss* global. Monitorear:

1.  **CSI (Critical Success Index) @ 35 dBZ:** Debe superar el 0.4 para ser operativo.
2.  **Visualización Cualitativa:**
    * ¿La celda se desplaza o se estira?
    * ¿Se conservan los núcleos rojos (>50 dBZ) en $t+15$?
3.  **Comparativa vs. TITAN:** La predicción debe mostrar deformación no lineal, superando la extrapolación de elipsoides rígidos.

---

## 🛠 Comandos de Ejecución

```bash
# Fase 1
python train.py --config configs/phase1_burnin.yaml --batch_size 32

# Fase 2 (Cargando pesos de Fase 1)
python train.py --config configs/phase2_advection.yaml --resume_from checkpoints/phase1_best.pth

# Inferencia y Conversión para TITAN
python predict_pipeline.py --model_path checkpoints/phase3_best.pth --upsample True
```

---

## 📏 Evaluación: HailCast vs. persistencia

El baseline de persistencia ("dentro de k scans el radar se ve igual que ahora") se mide sobre las
mismas secuencias, los mismos 7 targets observados, en dBZ, con la misma máscara y el mismo código de
métricas que el modelo (`evaluate_cases.compute_metrics`). Umbrales de CSI: 35 y 55 dBZ con `>=`.
CSI **pooled** (se suman aciertos, falsas alarmas y fallos de todas las secuencias y se divide una vez).

Scripts (en `training/`):

| Script | Qué hace |
| :--- | :--- |
| `evaluate_cases.py` | Define `CASES` (secuencias de validación) y las funciones de métricas. Corre las métricas del modelo. |
| `prepare_validation_cases.py` | `convert`: MDV → NetCDF con LROSE (mismo `.params` y nombres que el story mode). `predict`: re-inferencia del modelo de producción sobre la ventana de 8 frames de cada caso, con las funciones del worker. |
| `baseline_persistencia.py` | Persistencia vs. HailCast por horizonte y tipología, bootstrap (IC 95 %), tablas CSV, figuras PNG y `resumen.md`. |

Reproducir desde cero (local, sin tocar el contenedor; hace falta LROSE y el entorno `tesis-convlstm-3.10`):

```bash
# 1. MDV de las stories (en el VPS: /root/app_convlstm/data/stories_mdv/<story_id>) -> data/validation_cases/stories_mdv/
# 2. MDV -> NC
python training/prepare_validation_cases.py convert
# 3. Predicciones del modelo (config.py exige los secretos aunque acá no se usen)
SECRET_KEY=$(openssl rand -hex 32) INGEST_SECRET_KEY=$(openssl rand -hex 32) \
    python training/prepare_validation_cases.py predict
# 4. Métricas
python training/baseline_persistencia.py --stories-nc data/validation_cases/stories_nc \
    --predictions data/validation_cases/output_predictions --out-dir training/results/baseline
```

Resultados (13 secuencias: 4 multicelda, 4 supercelda, 5 advección; cadencia 3,45 min/scan) en
[`results/baseline/`](results/baseline/): `resumen.md`, `baseline_vs_hailcast.csv`, `bootstrap_ic95.csv`,
`csi35_por_horizonte.png`, `metricas_por_horizonte.png`, `csi35_por_tipologia.png`.

Notas de interpretación:
- La persistencia se evalúa con el mismo post-procesado que el producto del modelo (todo lo < 30 dBZ
  pasa a −29 dBZ). También se guarda la variante sin umbral (`Persistencia_sin_umbral`); solo cambia MAE y SSIM.
- El producto del modelo pasa por 500→250→500 px (bilineal + bicúbico); la persistencia es el frame nativo de 500 px.
- Dos casos de `CASES` (20251211 y 20251214) tienen el último frame de entrada en el 9.º scan de la story
  y quedan con 6 observados posteriores, por lo que se omiten (como hacía el script original).
