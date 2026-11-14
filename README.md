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
- **Cada clip de la fila se muestra en la misma línea**, con su primer fotograma
  y su último fotograma agrupados (etiquetas «primero» y «último») y un botón ▶.
- El ancho de las tarjetas se reparte según cuántos clips tenga el grupo: las
  miniaturas se encogen hasta `52 px` para que todos quepan; a partir de ahí la
  fila se desplaza en horizontal, sin perder la línea única.
- El parecido se mide contra la referencia del grupo, no en cadena: si `A~B` y
  `B~C` superan el umbral pero `A~C` no, no se fusionan los tres. Con un solo
  clip activo por fila, una fusión así dejaría un clip que nunca podría
  concatenarse.
- El orden inicial encadena por continuidad: `último(A) ≈ primero(B)`. Las filas
  cuyo último fotograma coincide van seguidas, y esa regla actúa como desempate:
  no se cuela entre un par que encaja exactamente.
- Las filas sin firmas (clip ilegible) no arrancan la cadena: se colocan al final.
- Arrastra las filas por el asa `⠿` para reordenar; ese orden **persiste** y manda
  sobre el automático, incluso al añadir clips nuevos.
- Clic en un clip lo marca como **activo** de su fila; solo ese se concatena.
- Cada tarjeta lleva un botón **↓** que la saca de su fila y la deja sola en la
  fila inmediatamente inferior, para montarla como plano propio sin perder la
  variante. Vuelve a pulsarlo para devolverla a su fila. Es un interruptor: la
  separación se guarda como decisión manual y sobrevive a los recálculos del
  agrupado (umbral, ingesta). Un clip que ya está solo no muestra el botón activo.
- Cada tarjeta lleva un **interruptor «mostrar siguientes»**: al encenderlo, solo
  quedan a la vista los clips cuyo **primer fotograma coincide (o casi) con el
  último** del clip del interruptor, usando el umbral del desplegable. El clip del
  interruptor se mantiene siempre visible para poder apagarlo.
- Solo puede haber **un filtro activo**: encender otro interruptor mueve el filtro.
  El botón «Mostrar todo» de la cabecera lo quita.
- ▶ abre el clip en el **reproductor del sistema** (`QDesktopServices.openUrl`),
  sin decodificar dentro de la app.
- La concatenación respeta el orden de las filas y usa el clip activo de cada fila
  visible: lo filtrado no entra en la salida.

## Montaje

Sobre la lista hay un **montaje** del resultado: un tramo por clip en el orden de
concatenación, con su **primer y último fotograma** dibujados dentro.

- El ancho de cada tramo es proporcional a la **duración** del clip, con un suelo
  por tramo (`14 px`) para que un clip muy corto no desaparezca. Si hay tantos
  clips que el suelo no cabe, se encoge hasta que el montaje completo quepa en la
  franja: nunca hay scroll horizontal.
- Pasar el ratón por una fila **destaca su clip** en el montaje (borde de acento y
  fondo encendido). Al salir, el montaje vuelve a su estado normal.
- El montaje muestra exactamente lo que se va a concatenar: filas visibles, clip
  activo de cada una, mismo orden. Con el filtro «mostrar siguientes» activo, el
  montaje también se reduce a lo filtrado.
- Al pasar el ratón por una tarjeta concreta se resalta **esa** tarjeta, aunque no
  sea la activa de su fila; al salir de la tarjeta se vuelve a resaltar la activa,
  que es la que se concatenará.

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
  ui/       PySide6 (mesa de montaje: filas con sus clips en línea + play externo)
```
