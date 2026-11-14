"""
# Video Concat

Concatenador de MP4 con interfaz gráfica PySide6, estrategia automática
**copy / CUDA full / NVENC / CPU**, dockerizado y listo para tu RTX.

## Idea clave

Si los clips son compatibles (mismo códec, resolución, fps y audio), se usa
`ffmpeg concat` con `-c copy`: no hay recodificación ni uso de GPU — es lo más
rápido y sin pérdida.

Si no lo son, se normaliza y recodifica con esta cascada:

1. **CUDA full** — NVDEC + `scale_cuda` + `h264_nvenc`
2. **NVENC** — decode/scale CPU + `h264_nvenc`
3. **CPU** — `libx264`

> El filtro `concat` de ffmpeg no trabaja con frames CUDA: `pad`/`fps`/`concat`
> siguen en CPU tras un `hwdownload` mínimo. Aun así, decode y scale (lo caro
> en resolución alta) ocurren en la GPU.

## Filas por fotograma

En vez de un listado plano, los clips se agrupan en **filas**:

- Una fila reúne los clips cuyo **primer y último fotograma** coinciden o casi
  coinciden (umbral ajustable, `0.93` por defecto). Son variantes del mismo plano.
- El orden inicial encadena por continuidad: `último(A) ≈ primero(B)`. Si dos
  clips acaban igual, el primero va justo antes del otro.
- Arrastra las filas para reordenar; ese orden manda sobre el automático.
- Cada fila tiene **un solo clip activo** (clic en la fila; botón de variante si
  agrupa varios). Se muestra miniatura del primer fotograma y se reproduce en la
  propia fila con el play.
- La concatenación respeta el orden de las filas y usa el clip activo de cada una.

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
  core/     probe, frames (fotogramas), grouping (filas), compatibilidad, concat, GPU
  ui/       PySide6 (mesa de montaje: filas arrastrables + reproductor inline)
```
