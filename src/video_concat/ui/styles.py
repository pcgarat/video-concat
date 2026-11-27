"""Tokens visuales — sala de montaje en penumbra."""

from __future__ import annotations

# Paleta: pizarra azulada profunda con cobre de claqueta. El acento viene de la
# identidad anterior (ámbar de claqueta) y se mantiene: es lo único encendido
# de la pantalla, así que el ojo siempre sabe dónde está el cursor.
COLORS = {
    "bg": "#0C1115",
    "surface": "#121A1F",
    "surface_raised": "#182229",
    "surface_hi": "#1F2B33",
    "stage": "#070B0E",
    "ink": "#E7EDF1",
    "ink_muted": "#8798A3",
    "line": "#26343D",
    "line_soft": "#1C272F",
    "accent": "#E08A4E",
    "accent_hi": "#F2A768",
    "accent_soft": "#33241A",
    "ok": "#5CC9A7",
    "ok_soft": "#123029",
    "warn": "#E0B263",
    "warn_soft": "#332812",
    "danger": "#DE716B",
    "danger_soft": "#361C1C",
    "timeline_track": "#1A262E",
    "playhead": "#F2A768",
}

FONT_SANS = (
    '"IBM Plex Sans", "Source Sans 3", "Noto Sans", "DejaVu Sans", "Segoe UI", sans-serif'
)
FONT_DISPLAY = (
    '"IBM Plex Sans Condensed", "DejaVu Sans Condensed", "Liberation Sans Narrow", '
    '"Noto Sans", sans-serif'
)
FONT_MONO = '"IBM Plex Mono", "JetBrains Mono", "DejaVu Sans Mono", "Liberation Mono", monospace'

APP_STYLE = f"""
QWidget {{
    background-color: {COLORS["bg"]};
    color: {COLORS["ink"]};
    font-family: {FONT_SANS};
    font-size: 13px;
}}
QMainWindow, QDialog, QMessageBox {{
    background-color: {COLORS["bg"]};
}}
QLabel {{
    background: transparent;
}}
QLabel#Brand {{
    font-family: {FONT_DISPLAY};
    font-size: 24px;
    font-weight: 600;
    letter-spacing: 2.5px;
    color: {COLORS["ink"]};
}}
QLabel#Tagline {{
    color: {COLORS["ink_muted"]};
    font-size: 11px;
    letter-spacing: 0.6px;
}}
QLabel#SectionTitle {{
    font-family: {FONT_DISPLAY};
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 1.8px;
    color: {COLORS["ink_muted"]};
}}
QLabel#Hint {{
    color: {COLORS["ink_muted"]};
    font-size: 11px;
}}
QLabel#Timecode {{
    font-family: {FONT_MONO};
    font-size: 12px;
    letter-spacing: 0.5px;
    color: {COLORS["ink"]};
}}
QLabel#StatusPill {{
    padding: 5px 10px;
    border-radius: 4px;
    font-weight: 600;
    font-size: 11px;
    letter-spacing: 0.4px;
}}
QFrame#Panel {{
    background-color: {COLORS["surface"]};
    border: 1px solid {COLORS["line_soft"]};
    border-radius: 8px;
}}
QFrame#TimelineFrame {{
    background-color: {COLORS["surface"]};
    border: 1px solid {COLORS["line_soft"]};
    border-radius: 8px;
}}
QFrame#BottomBar {{
    background-color: {COLORS["surface"]};
    border: 1px solid {COLORS["line_soft"]};
    border-radius: 8px;
}}
QSplitter::handle {{
    background: transparent;
}}
QSplitter::handle:hover {{
    background: {COLORS["line"]};
}}
QScrollArea {{
    background-color: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}
QPushButton {{
    background-color: {COLORS["surface_raised"]};
    border: 1px solid {COLORS["line"]};
    border-radius: 5px;
    padding: 7px 13px;
    font-weight: 600;
    color: {COLORS["ink"]};
}}
QPushButton:hover {{
    border-color: {COLORS["ink_muted"]};
    background-color: {COLORS["surface_hi"]};
}}
QPushButton:pressed {{
    background-color: {COLORS["line"]};
}}
QPushButton:disabled {{
    color: #57666F;
    background-color: #141C22;
    border-color: #1D282F;
}}
QPushButton#Primary {{
    background-color: {COLORS["accent"]};
    color: #14100C;
    border: 1px solid {COLORS["accent"]};
    padding: 9px 22px;
    font-size: 13px;
    letter-spacing: 0.3px;
}}
QPushButton#Primary:hover {{
    background-color: {COLORS["accent_hi"]};
    border-color: {COLORS["accent_hi"]};
}}
QPushButton#Primary:disabled {{
    background-color: #2A2119;
    border-color: #2A2119;
    color: #7A6A58;
}}
QPushButton#Danger {{
    background-color: {COLORS["danger_soft"]};
    border-color: {COLORS["danger"]};
    color: {COLORS["danger"]};
    padding: 9px 22px;
}}
QPushButton#Danger:hover {{
    background-color: #452322;
}}
QPushButton#Ghost {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 10px;
    padding: 3px 9px;
    font-size: 11px;
    font-weight: 600;
    color: {COLORS["ink_muted"]};
}}
QPushButton#Ghost:hover {{
    color: {COLORS["ink"]};
    border-color: {COLORS["line"]};
    background-color: {COLORS["surface_hi"]};
}}
QFrame#ClipRow {{
    background-color: {COLORS["surface_raised"]};
    border: 1px solid {COLORS["line_soft"]};
    border-left: 3px solid {COLORS["line"]};
    border-radius: 7px;
}}
QFrame#ClipRow:hover {{
    border-color: {COLORS["line"]};
    border-left-color: {COLORS["ink_muted"]};
}}
QFrame#ClipRow[selected="true"] {{
    border-left: 3px solid {COLORS["accent"]};
    background-color: #1B252C;
}}
QFrame#ClipRow[dropTarget="true"] {{
    border-top: 2px solid {COLORS["accent"]};
}}
QLabel#RowHandle {{
    color: #3C4D57;
    font-size: 15px;
}}
QLabel#RowHandle:hover {{
    color: {COLORS["ink_muted"]};
}}
QLabel#RowTitle {{
    font-weight: 600;
    font-size: 12px;
    color: {COLORS["ink"]};
}}
QLabel#RowTags {{
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.3px;
}}
QFrame#ClipTile {{
    background-color: {COLORS["surface"]};
    border: 1px solid {COLORS["line_soft"]};
    border-radius: 6px;
}}
QFrame#ClipTile:hover {{
    border-color: {COLORS["line"]};
    background-color: {COLORS["surface_raised"]};
}}
QFrame#ClipTile[active="true"] {{
    background-color: {COLORS["accent_soft"]};
    border: 1px solid {COLORS["accent"]};
}}
QLabel#TileName {{
    font-size: 12px;
    font-weight: 600;
    color: {COLORS["ink"]};
}}
QLabel#TileDetail, QLabel#TileStatus {{
    font-size: 10px;
    color: {COLORS["ink_muted"]};
}}
QPushButton#TileAction {{
    background-color: transparent;
    border: 1px solid {COLORS["line"]};
    border-radius: 10px;
    padding: 0px;
    font-size: 10px;
    font-weight: 600;
    color: {COLORS["ink_muted"]};
}}
QPushButton#TileAction:hover {{
    background-color: {COLORS["surface_hi"]};
    border-color: {COLORS["ink_muted"]};
    color: {COLORS["ink"]};
}}
QPushButton#TileAction:checked {{
    background-color: {COLORS["accent"]};
    border-color: {COLORS["accent"]};
    color: #14100C;
}}
QPushButton#TileAction:disabled {{
    color: #4B5A63;
    border-color: {COLORS["line_soft"]};
}}
QAbstractButton#FollowSwitch {{
    background: transparent;
    border: none;
}}
QLineEdit {{
    background-color: {COLORS["surface_raised"]};
    border: 1px solid {COLORS["line"]};
    border-radius: 5px;
    padding: 8px 10px;
    color: {COLORS["ink"]};
    font-family: {FONT_MONO};
    font-size: 12px;
    selection-background-color: {COLORS["accent"]};
    selection-color: #14100C;
}}
QLineEdit:focus {{
    border-color: {COLORS["accent"]};
}}
QComboBox {{
    background-color: {COLORS["surface_raised"]};
    border: 1px solid {COLORS["line"]};
    border-radius: 5px;
    padding: 5px 8px;
    color: {COLORS["ink"]};
    font-size: 11px;
}}
QComboBox:hover {{
    border-color: {COLORS["ink_muted"]};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    background-color: {COLORS["surface_raised"]};
    border: 1px solid {COLORS["line"]};
    color: {COLORS["ink"]};
    selection-background-color: {COLORS["accent_soft"]};
    selection-color: {COLORS["ink"]};
    outline: none;
}}
QSlider::groove:horizontal {{
    height: 3px;
    background: {COLORS["line"]};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {COLORS["accent"]};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {COLORS["ink"]};
    border: 3px solid {COLORS["bg"]};
    width: 10px;
    height: 10px;
    margin: -6px 0;
    border-radius: 8px;
}}
QSlider::handle:horizontal:hover {{
    background: {COLORS["accent_hi"]};
}}
QProgressBar {{
    border: none;
    background-color: {COLORS["line_soft"]};
    text-align: center;
    color: {COLORS["ink"]};
    font-weight: 600;
}}
QProgressBar::chunk {{
    background-color: {COLORS["accent"]};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #2C3C46;
    border-radius: 4px;
    min-height: 26px;
}}
QScrollBar::handle:vertical:hover {{
    background: #3A4E5A;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QToolTip {{
    background-color: {COLORS["surface_hi"]};
    color: {COLORS["ink"]};
    border: 1px solid {COLORS["line"]};
    padding: 6px 8px;
}}
"""


def apply_theme(app) -> None:
    """Fusion + paleta oscura: diálogos nativos y desplegables siguen el tema."""
    from PySide6.QtGui import QColor, QPalette

    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLORS["bg"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLORS["ink"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLORS["surface_raised"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLORS["surface"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLORS["ink"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLORS["surface_raised"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLORS["ink"]))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(COLORS["surface_hi"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(COLORS["ink"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLORS["accent"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#14100C"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(COLORS["ink_muted"]))
    app.setPalette(palette)
