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

## La ventana

- **Izquierda**: un **reproductor** que previsualiza el montaje completo seguido,
  centrado en el hueco libre. Ocupa más o menos sitio según esté visible el panel.
- **Derecha**: el **panel de clips**, que se muestra u oculta con el botón de la
  cabecera. Arriba lleva dos botones de icono —añadir vídeos y vaciar la lista— y
  un deslizador que escala las tarjetas de cada fila.
- **Abajo**: el **timeline** del montaje, con su cursor, y bajo él la barra de
  salida: ruta de destino, botón para elegirla y **Concatenar**.
- Durante una concatenación el mismo botón pasa a **Detener**: no hay un botón de
  cancelar aparte, pero el trabajo siempre se puede interrumpir.
- Tema oscuro completo (Fusion + paleta propia), pensado para verse en penumbra
  junto al resto de la sala de montaje.

## Filas por fotograma

En vez de un listado plano, los clips se agrupan en **filas**:

- Una fila reúne los clips cuyo **primer y último fotograma** coinciden o casi
  coinciden (umbral ajustable, `0.93` por defecto). Son variantes del mismo plano.
- Cada clip es una **tarjeta horizontal**: sus dos fotogramas extremos a la
  izquierda (con una flecha `›` entre ellos) y la ficha a la derecha con nombre,
  resolución, fps, duración, tamaño y las acciones.
- Las tarjetas de una fila se **envuelven en líneas** según el ancho del panel:
  con el panel estrecho va una por línea, y al ensancharlo caben dos o más.
- El parecido se mide contra la referencia del grupo, no en cadena: si `A~B` y
  `B~C` superan el umbral pero `A~C` no, no se fusionan los tres. Con un solo
  clip activo por fila, una fusión así dejaría un clip que nunca podría
  concatenarse.
- Con el orden automático **encendido**, el orden encadena por continuidad:
  `último(A) ≈ primero(B)`. Las filas cuyo último fotograma coincide van seguidas,
  y esa regla actúa como desempate: no se cuela entre un par que encaja exactamente.
- Las filas sin firmas (clip ilegible) no arrancan la cadena: se colocan al final, y
  conservan esa última posición también con el automático encendido.
- El interruptor **«Orden automático»** del panel decide quién manda en el orden:
  - **Apagado** (por defecto): manda el orden que fijes tú. Los clips entran al
    final en el orden en que los añades y los recálculos no reordenan nada.
  - **Encendido**: cada recálculo —añadir clips, cambiar el umbral— re-encadena
    por fotogramas; al encenderlo se descarta el orden manual y se re-encadena.
- Arrastra las filas por el asa `⠿` para reordenar; arrastrar (o sacar un clip con
  el botón ↓) **apaga el automático** si estaba encendido, porque es una decisión
  manual de montaje.
- Clic en una tarjeta la marca como **activa** de su fila; solo esa se concatena.
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
  El botón «Mostrar todo» del panel lo quita.
- El **▶ de la tarjeta** lleva ese clip al reproductor y lo reproduce. Para abrirlo
  en el reproductor del sistema, doble clic o menú contextual.
- La **✕** de la tarjeta quita ese clip; también sirve la tecla `Supr` sobre la
  fila seleccionada.
- La concatenación respeta el orden de las filas y usa el clip activo de cada fila
  visible: lo filtrado no entra en la salida.

## Timeline y previsualización

Bajo el reproductor hay un **timeline** del resultado: un tramo por clip en el
orden de concatenación, con su **primer fotograma a sangre** ocupando todo el
recuadro (recortado al centro, sin deformar).

- El ancho de cada tramo es proporcional a la **duración** del clip, con un suelo
  por tramo (`14 px`) para que un clip muy corto no desaparezca. Si hay tantos
  clips que el suelo no cabe, se encoge hasta que el montaje completo quepa en la
  franja: nunca hay scroll horizontal.
- **Reordenar desde el timeline**: arrastra un tramo y suéltalo en el hueco que
  quieras. Una línea vertical de acento marca dónde va a caer. Reordena la fila
  completa (con sus variantes), igual que arrastrar la fila en el panel; soltarlo
  donde ya estaba no hace nada.
- **Tramos con variantes**: si la fila tiene más de un clip, el tramo lleva
  **borde naranja** y una insignia `▾N`. Al pasar el ratón se abre **encima** una
  bandeja con las variantes; pulsar una **sustituye** el clip del timeline por
  esa y la marca como activa en su fila. La variante que ya está puesta aparece
  con borde naranja y no hace nada al pulsarla. La bandeja se cierra al salir.
- El **cursor** (playhead) recorre el timeline con la reproducción y tiñe de ámbar
  lo ya reproducido. Pulsar un punto del timeline mueve el cursor a ese instante y
  selecciona la fila que aporta el clip. La fila seleccionada lleva una barra
  inferior encendida.
- **Selección compartida**: panel y timeline hablan siempre del mismo clip.
  - Elegir un tramo del timeline selecciona esa fila en el panel, activa la
    variante correcta y desplaza la lista hasta dejarla a la vista.
  - Elegir una fila (o una variante) en el panel mueve el cursor del timeline al
    inicio de ese clip y marca su tramo.
  - Durante la reproducción, el clip que suena se va marcando en el panel y la
    lista se desplaza con él. Nunca hay dos filas seleccionadas a la vez.
- Pasar el ratón por una fila del panel **destaca su clip** en el timeline (sin
  cambiar la selección).
- El timeline muestra exactamente lo que se va a concatenar: filas visibles, clip
  activo de cada una, mismo orden. Con el filtro «mostrar siguientes» activo,
  también se reduce a lo filtrado.
- El reproductor reproduce **el montaje completo seguido**: al terminar un clip
  salta al siguiente. `QMediaPlayer` reproduce un fichero por vez, así que puede
  haber un salto mínimo entre clips; el cursor y la fila seleccionada se mueven
  con la reproducción.
- Clic en la imagen o en el botón central para play/pausa; `‹` y `›` saltan al
  clip anterior o siguiente.
- Al pasar el ratón por una tarjeta concreta se resalta **esa** tarjeta, aunque no
  sea la activa de su fila; al salir se vuelve a resaltar la activa, que es la que
  se concatenará.

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
  ui/       PySide6
    main_window.py  layout: reproductor + panel de clips + timeline + barra de salida
    player.py       previsualización encadenada del montaje (QtMultimedia)
    timeline.py     conversión tiempo global ↔ clip (lógica pura)
    widgets.py      tarjetas, filas, iconos pintados y timeline con cursor
    styles.py       tema oscuro (tokens + hoja de estilo + paleta Fusion)
```
