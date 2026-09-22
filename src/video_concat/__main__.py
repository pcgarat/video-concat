"""Punto de entrada de Video Concat."""

from __future__ import annotations

import os
import sys


def main() -> int:
    # Preferir Wayland si está disponible; permitir override por entorno.
    if not os.environ.get("QT_QPA_PLATFORM"):
        if os.environ.get("WAYLAND_DISPLAY"):
            os.environ.setdefault("QT_QPA_PLATFORM", "wayland")
        elif os.environ.get("DISPLAY"):
            os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

    from PySide6.QtWidgets import QApplication

    from video_concat.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Video Concat")
    app.setOrganizationName("tools")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
