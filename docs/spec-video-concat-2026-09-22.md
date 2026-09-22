Última modificación: 2026-09-22

# Spec — Video Concat

## Objetivo

Aplicación de escritorio en Python para concatenar vídeos MP4 con ffmpeg, interfaz PySide6 profesional, dockerizada, con aceleración NVIDIA cuando la recodificación sea necesaria.

## Comportamiento

1. El usuario añade, reordena y elimina ficheros MP4.
2. `ffprobe` analiza cada fichero (códec, resolución, fps, audio, duración).
3. **Modo automático**
   - Compatible (mismo códec vídeo/audio, resolución, fps, timebase razonable) → `concat` demuxer + `-c copy`.
   - Incompatible → normalización + recodificación.
     - Preferencia: decodificación CUDA + `h264_nvenc` si la GPU está disponible.
     - Fallback: `libx264` + `aac`.
4. Barra de progreso basada en `-progress` de ffmpeg.
5. Cancelación limpia del proceso hijo.

## No objetivos (v1)

- Otros contenedores (mkv, mov) salvo que se añadan después.
- Edición de cortes, transiciones o filtros creativos.
- Subida a la nube.

## Criterios de aceptación

- [ ] Concatenar ≥2 MP4 idénticos en <5 s para ~1 min de material (modo copy).
- [ ] Detectar incompatibilidad y mostrar el modo elegido antes de ejecutar.
- [ ] Con GPU NVIDIA visible, usar NVENC en modo recodificación.
- [ ] Sin GPU / error NVENC → fallback a CPU sin crashear.
- [ ] `make build && make run` abre la UI vía Wayland/X11.
- [ ] `make test` pasa.

## Stack

- Python 3.12, PySide6, ffmpeg/ffprobe
- Docker + nvidia-container-toolkit
- Makefile: nativo + Docker
