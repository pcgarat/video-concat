"""Aislamiento de Qt/PySide6 frente a LD_LIBRARY_PATH hostiles (Cursor, conda, etc.)."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_READY_FLAG = "VIDEO_CONCAT_QT_READY"


def _hostile_lib_dir(path: str) -> bool:
    lower = path.lower()
    return any(
        token in lower
        for token in (
            "/usr/lib",
            ".mount_cursor",
            "/conda",
            "miniconda",
            "anaconda",
        )
    )


def _hostile_plugin_path(value: str) -> bool:
    lower = value.lower()
    return any(
        token in lower
        for token in (".mount_cursor", "qt4/plugins", "qt5/plugins", "/conda")
    )


def prepare_qt_environment() -> bool:
    """
    Prepara un entorno seguro para cargar el Qt embebido de PySide6.

    Devuelve True si hay que re-ejecutar el proceso (LD_LIBRARY_PATH solo
    es fiable si está fijado antes del arranque del intérprete).
    """
    if os.environ.get(_READY_FLAG) == "1":
        return False

    spec = importlib.util.find_spec("PySide6")
    if spec is None or not spec.origin:
        os.environ[_READY_FLAG] = "1"
        return False

    pyside_root = Path(spec.origin).resolve().parent
    qt_lib = pyside_root / "Qt" / "lib"
    shiboken = pyside_root.parent / "shiboken6"

    # Plugins del host (p. ej. AppImage de Cursor) rompen Qt6.
    for key in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"):
        value = os.environ.get(key, "")
        if value and _hostile_plugin_path(value):
            os.environ.pop(key, None)

    preferred: list[str] = []
    if qt_lib.is_dir():
        preferred.append(str(qt_lib))
    if shiboken.is_dir():
        preferred.append(str(shiboken))

    existing = [
        part
        for part in os.environ.get("LD_LIBRARY_PATH", "").split(":")
        if part and not _hostile_lib_dir(part)
    ]
    os.environ["LD_LIBRARY_PATH"] = ":".join([*preferred, *existing])
    os.environ[_READY_FLAG] = "1"
    return True


def reexec_if_needed() -> None:
    """Re-lanza el proceso con el entorno Qt ya saneado."""
    if not prepare_qt_environment():
        return
    os.execv(sys.executable, [sys.executable, "-m", "video_concat", *sys.argv[1:]])
