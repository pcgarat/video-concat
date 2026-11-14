Última modificación: 2026-09-22

# Spec — Video Concat

## Objetivo

Aplicación de escritorio en Python para concatenar vídeos MP4 con ffmpeg, interfaz PySide6 profesional, dockerizada, con aceleración NVIDIA cuando la recodificación sea necesaria.

## Comportamiento

1. El usuario añade ficheros MP4 (arrastrar o diálogo).
2. `ffprobe` analiza cada fichero (códec, resolución, fps, audio, duración).
3. `ffmpeg` extrae el **primer y último fotograma** de cada clip y se firman como
   rejilla 32×32 RGB normalizada. `similarity` es `1 - RMSE` sobre esa rejilla,
   acotada en `[0, 1]`.
4. **Filas por fotograma** (no listado plano):
   - Se agrupan en una sola fila los clips cuyo **primer y último fotograma**
     coinciden o casi coinciden (umbral configurable, por defecto `0.93`).
   - Dos clips que solo comparten uno de los dos extremos **no** se agrupan: evita
     falsos positivos entre clips consecutivos de una misma secuencia.
   - Orden inicial: se encadena por continuidad (`último(A) ≈ primero(B)`).
     Si el último de A coincide con el último de B, A va justo antes de B.
   - El usuario puede reordenar arrastrando filas; ese orden manual manda y no
     lo sobrescribe el encadenado automático.
5. **Una versión por fila**: cada fila tiene exactamente un clip activo. Se
   selecciona haciendo clic en la fila; si la fila agrupa varias variantes, se
   alterna con el botón de variante.
6. Cada fila muestra miniatura del primer fotograma y un reproductor con play
   inline (reproductor único compartido, re-parentado a la fila activa).
7. Al concatenar se respeta el orden de las filas y se toma el clip activo de
   cada una.
8. **Modo automático**
   - Compatible → `concat` demuxer + `-c copy`.
   - Incompatible → normalización + recodificación, con cascada:
     1. **CUDA full**: NVDEC (`-hwaccel cuda`) + `scale_cuda` + NVENC.
        `pad`/`fps`/`concat` en CPU (limitación de ffmpeg: el filtro concat no opera sobre frames CUDA).
     2. **NVENC**: decode/scale CPU + `h264_nvenc`.
     3. **CPU**: `libx264` + `aac`.
9. Barra de progreso basada en `-progress` de ffmpeg.
10. Cancelación limpia del proceso hijo.

## Decisiones de diseño

- **Agrupar exige que coincidan ambos extremos.** Medido sobre material real
  (6 clips de una cadena): ningún par compartía primero *y* último, mientras que
  `último→primero` daba 0.976–0.995. Con umbral `0.85` empezaban a fusionarse
  clips distintos (falso positivo), así que el defecto es `0.93`.
- **Un reproductor compartido** en vez de uno por fila: un solo decodificador,
  sin audio solapado y sin N hilos de vídeo.
- **Umbral en la UI** porque "casi igual" es una decisión del usuario, no del código.

## No objetivos (v1)

- Otros contenedores (mkv, mov) salvo que se añadan después.
- Edición de cortes, transiciones o filtros creativos.
- Subida a la nube.
- Comparación de fotogramas intermedios (solo los extremos).

## Criterios de aceptación

- [x] Concatenar ≥2 MP4 idénticos en modo copy.
- [x] Detectar incompatibilidad y mostrar el modo/pipeline elegido.
- [x] Con GPU NVIDIA usable, preferir CUDA full; fallback NVENC → CPU.
- [x] Agrupar en una fila los clips con primer y último fotograma equivalentes.
- [x] Orden inicial por continuidad y reordenado manual por arrastre.
- [x] Un único clip activo por fila; concatenar en orden de filas.
- [x] Miniatura del primer fotograma y reproducción inline por fila.
- [x] `make build && make run` / `make run-local`.
- [x] `make test` pasa.

## Stack

- Python 3.12, PySide6 (QtWidgets + QtMultimedia), ffmpeg/ffprobe
- Docker + nvidia-container-toolkit
- Makefile: nativo + Docker
