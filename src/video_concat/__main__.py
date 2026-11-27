"""Punto de entrada de Video Concat."""

from __future__ import annotations

import os
import sys

from video_concat.logutil import get_logger, setup_logging
from video_concat.qt_runtime import reexec_if_needed


def main() -> int:
    # Cursor/conda inyectan LD_LIBRARY_PATH con Qt del sistema → hay que re-exec.
    reexec_if_needed()
    setup_logging()

    # Preferir Wayland si está disponible; permitir override por entorno.
    if not os.environ.get("QT_QPA_PLATFORM"):
        if os.environ.get("WAYLAND_DISPLAY"):
            os.environ.setdefault("QT_QPA_PLATFORM", "wayland")
        elif os.environ.get("DISPLAY"):
            os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

    from PySide6.QtWidgets import QApplication

    from video_concat.ui.main_window import MainWindow
    from video_concat.ui.styles import apply_theme

    log = get_logger("main")
    log.info("Arrancando Video Concat")

    app = QApplication(sys.argv)
    app.setApplicationName("Video Concat")
    app.setOrganizationName("tools")
    apply_theme(app)
    window = MainWindow()
    window.show()
    log.info("Ventana visible (platform=%s)", app.platformName())
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
