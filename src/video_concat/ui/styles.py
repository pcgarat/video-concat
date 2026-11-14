"""Tokens visuales — estética 'mesa de montaje'."""

# Paleta: pizarra fría + acento ámbar de claqueta (no cream, no purple, no dark-mode genérico).
COLORS = {
    "bg": "#D9E0E6",
    "surface": "#F4F7F9",
    "surface_raised": "#FFFFFF",
    "ink": "#14212B",
    "ink_muted": "#5A6B78",
    "line": "#B7C4CE",
    "accent": "#C45C26",
    "accent_soft": "#F3D9C8",
    "ok": "#1F6F5B",
    "ok_soft": "#D5EDE4",
    "warn": "#9A6B00",
    "warn_soft": "#F5E8C0",
    "danger": "#9B2C2C",
    "danger_soft": "#F5D6D6",
    "timeline_track": "#C5D0D8",
    "clip_a": "#1E3A4C",
    "clip_b": "#2F5A6E",
    "clip_c": "#3D7A8C",
}

APP_STYLE = f"""
QWidget {{
    background-color: {COLORS["bg"]};
    color: {COLORS["ink"]};
    font-family: "IBM Plex Sans", "Source Sans 3", "Noto Sans", "Segoe UI", sans-serif;
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background-color: {COLORS["bg"]};
}}
QLabel#Brand {{
    font-family: "IBM Plex Sans Condensed", "IBM Plex Sans", "Source Sans 3", sans-serif;
    font-size: 28px;
    font-weight: 600;
    letter-spacing: 1px;
    color: {COLORS["ink"]};
}}
QLabel#Tagline {{
    color: {COLORS["ink_muted"]};
    font-size: 12px;
    letter-spacing: 0.4px;
}}
QLabel#SectionTitle {{
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1.4px;
    text-transform: uppercase;
    color: {COLORS["ink_muted"]};
}}
QLabel#StatusPill {{
    padding: 6px 12px;
    border-radius: 4px;
    font-weight: 600;
    font-size: 12px;
}}
QFrame#Panel {{
    background-color: {COLORS["surface"]};
    border: 1px solid {COLORS["line"]};
    border-radius: 6px;
}}
QFrame#TimelineFrame {{
    background-color: {COLORS["surface_raised"]};
    border: 1px solid {COLORS["line"]};
    border-radius: 6px;
}}
QListWidget {{
    background-color: {COLORS["surface_raised"]};
    border: 1px solid {COLORS["line"]};
    border-radius: 6px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    padding: 10px 12px;
    border-radius: 4px;
    margin: 2px 0;
}}
QListWidget::item:selected {{
    background-color: {COLORS["accent_soft"]};
    color: {COLORS["ink"]};
}}
QListWidget::item:hover {{
    background-color: #E8EEF2;
}}
QPushButton {{
    background-color: {COLORS["surface_raised"]};
    border: 1px solid {COLORS["line"]};
    border-radius: 5px;
    padding: 8px 14px;
    font-weight: 600;
    color: {COLORS["ink"]};
}}
QPushButton:hover {{
    border-color: {COLORS["ink_muted"]};
    background-color: #EEF3F6;
}}
QPushButton:pressed {{
    background-color: #E2E9EE;
}}
QPushButton:disabled {{
    color: #8A9AA6;
    background-color: #E4EAEF;
}}
QPushButton#Primary {{
    background-color: {COLORS["ink"]};
    color: {COLORS["surface_raised"]};
    border: 1px solid {COLORS["ink"]};
    padding: 10px 18px;
}}
QPushButton#Primary:hover {{
    background-color: #1E3240;
}}
QPushButton#Primary:disabled {{
    background-color: #8A9AA6;
    border-color: #8A9AA6;
    color: #D5DDE3;
}}
QPushButton#Danger {{
    background-color: {COLORS["danger_soft"]};
    border-color: {COLORS["danger"]};
    color: {COLORS["danger"]};
}}
QPushButton#Variant {{
    background-color: {COLORS["surface_raised"]};
    border: 1px solid {COLORS["line"]};
    border-radius: 12px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
    color: {COLORS["ink_muted"]};
}}
QPushButton#Variant:hover {{
    color: {COLORS["ink"]};
    border-color: {COLORS["ink_muted"]};
}}
QFrame#ClipRow {{
    background-color: {COLORS["surface_raised"]};
    border: 1px solid {COLORS["line"]};
    border-left: 4px solid {COLORS["line"]};
    border-radius: 6px;
}}
QFrame#ClipRow:hover {{
    border-color: {COLORS["ink_muted"]};
}}
QFrame#ClipRow[selected="true"] {{
    border-left: 4px solid {COLORS["accent"]};
    background-color: #FFFDFA;
}}
QFrame#ClipRow[dropTarget="true"] {{
    border-top: 2px solid {COLORS["accent"]};
}}
QLabel#RowHandle {{
    color: {COLORS["line"]};
    font-size: 16px;
}}
QLabel#RowTitle {{
    font-weight: 600;
    font-size: 13px;
    color: {COLORS["ink"]};
}}
QLabel#RowDetail {{
    color: {COLORS["ink_muted"]};
    font-size: 11px;
}}
QLabel#RowTags {{
    font-size: 11px;
    font-weight: 600;
}}
QScrollArea {{
    background-color: transparent;
    border: none;
}}
QScrollArea > QWidget > QWidget {{
    background-color: transparent;
}}
QLineEdit {{
    background-color: {COLORS["surface_raised"]};
    border: 1px solid {COLORS["line"]};
    border-radius: 5px;
    padding: 8px 10px;
    selection-background-color: {COLORS["accent_soft"]};
}}
QLineEdit:focus {{
    border-color: {COLORS["accent"]};
}}
QProgressBar {{
    border: 1px solid {COLORS["line"]};
    border-radius: 4px;
    background-color: {COLORS["timeline_track"]};
    text-align: center;
    height: 18px;
    color: {COLORS["ink"]};
    font-weight: 600;
}}
QProgressBar::chunk {{
    background-color: {COLORS["accent"]};
    border-radius: 3px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px 2px;
}}
QScrollBar::handle:vertical {{
    background: {COLORS["line"]};
    border-radius: 4px;
    min-height: 24px;
}}
QToolTip {{
    background-color: {COLORS["ink"]};
    color: {COLORS["surface_raised"]};
    border: none;
    padding: 6px 8px;
}}
"""
