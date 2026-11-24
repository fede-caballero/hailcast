# Qué cambió de 13 a 77 secuencias

Comparación entre `training/results/baseline/` (13 secuencias, publicado) y `training/results/baseline_ampliado/`
(77 secuencias: 21 multicelda, 21 supercelda, 20 advección, 15 débil; no se armó la tipología `decaimiento`).
Los horizontes son scans; la cadencia media pasó de 3,45 a 3,57 min, así que t+4 ≈ 14 min y t+7 ≈ 25 min.

1. **La ventaja de HailCast en CSI 35 entre 7 y 21 min se sostiene.** A t+4 la diferencia es +0,077 (IC 95 % [+0,054, +0,098]) contra +0,100 [+0,047, +0,148] con 13; el intervalo tiene la mitad de ancho y sigue sin incluir el cero. De t+2 a t+6 HailCast va arriba (0,41 vs 0,37 a 7 min; 0,19 vs 0,17 a 21 min).
2. **En los extremos empata:** a t+1 −0,012 [−0,037, +0,012] y a t+7 −0,004 [−0,024, +0,013]. Con 13 el t+7 daba +0,026 y parecía una ventaja; no lo era.
3. **La ventaja en CSI 35 ya es consistente en las tres tipologías principales**, no solo en advección: a t+4 multicelda +0,109, supercelda +0,084, advección +0,079, las tres con IC sin cero. Con 4–5 casos por tipología solo advección lo era.
4. **La desventaja en CSI 55 se confirma y se agrava con el horizonte:** −0,081 a t+1, −0,075 a t+4 y −0,151 a t+7, todas con IC sin cero (con 13, t+4 era inconcluyente). HailCast cae a 0,004 a t+7 mientras la persistencia se mantiene en 0,15.
5. **Esa meseta de la persistencia no la explican los ecos fijos:** los píxeles ≥ 55 dBZ presentes en los 8 frames de entrada son el 2–7 % de los observados ≥ 55 dBZ a t+7. Los núcleos reales persisten en su lugar 25 min y el modelo los diluye.
6. **MAE:** HailCast mejor en todos los horizontes y en las tres tipologías principales (−0,15 dBZ a t+4, IC sin cero). **SSIM:** persistencia apenas mejor (0,003–0,005), igual que antes.
7. **Débil (n = 15) es donde la persistencia gana claro:** CSI 35 −0,21 a t+1 y −0,15 a t+7, CSI 55 −0,30, MAE empate. El post-procesado a 30 dBZ y el suavizado del modelo borran los ecos débiles; la persistencia los conserva. Es el comportamiento que esperabas para decaimiento, que no se muestreó.
8. **La regla automática de los 45 dBZ no discrimina a nivel píxel:** los 15 casos débiles tienen entre 80 y 450 píxeles ≥ 45 dBZ (móviles, sin ecos fijos) en el último frame. La etiqueta `debil` queda como clasificación del analista en TITAN y así hay que decirlo.
9. **Cadencias nuevas:** entraron casos a 3,0 min (noviembre 2025) y a 4,6 min; el eje en minutos usa la media. Ningún hueco supera 4,9 min.
10. **Mensaje para el concurso:** con 77 casos el resultado de los 13 no fue un artefacto de muestra chica. HailCast supera a la persistencia en CSI 35 entre 7 y 21 min y en MAE en todo el rango; pierde en intensidad de núcleos (CSI 55) y en convección débil.
