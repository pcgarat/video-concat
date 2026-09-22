"""
# Video Concat

Concatenador de MP4 con interfaz gráfica PySide6, estrategia automática
**copy / NVENC**, dockerizado y listo para tu RTX.

## Idea clave

Si los clips son compatibles (mismo códec, resolución, fps y audio), se usa
`ffmpeg concat` con `-c copy`: no hay recodificación ni uso de GPU — es lo más
rápido y sin pérdida.

Si no lo son, se normaliza y recodifica. Con NVIDIA disponible usa `h264_nvenc`;
si falla, cae a `libx264` sin tumbar la app.

## Requisitos

- Python 3.11+
- ffmpeg / ffprobe (con nvenc si quieres GPU)
- Docker + nvidia-container-toolkit (para `make run`)
- Sesión gráfica Wayland o X11

## Uso rápido (nativo)

```bash
cd video-concat
make install-dev
make run-local
```

## Uso con Docker (GPU + GUI)

Por defecto monta `$HOME/Videos` en `/media` del contenedor.

```bash
cd video-concat
make build
make gpu-check    # opcional: verifica NVENC en el contenedor
make run
```

Si la ventana no aparece:

```bash
make xhost
QT_QPA_PLATFORM=xcb make run
```

Volumen custom:

```bash
make run MEDIA_DIR=/ruta/a/tus/videos
```

## Makefile

| Objetivo        | Qué hace                          |
|-----------------|-----------------------------------|
| `make help`     | Lista objetivos                   |
| `make install`  | venv + app editable               |
| `make run-local`| UI en el host                     |
| `make build`    | Imagen Docker                     |
| `make run`      | UI en contenedor con GPU          |
| `make gpu-check`| Prueba NVENC en contenedor        |
| `make test`     | pytest                            |
| `make lint`     | ruff                              |
| `make clean`    | Limpia artefactos                 |

## Estructura

```
src/video_concat/
  core/     probe, compatibilidad, concat, GPU
  ui/       PySide6 (mesa de montaje)
```
