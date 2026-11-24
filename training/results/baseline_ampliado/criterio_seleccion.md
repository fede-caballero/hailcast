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
