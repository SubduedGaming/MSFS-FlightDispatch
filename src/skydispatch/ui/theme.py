"""Application look & feel (dark + light), built from a few palette tokens."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    bg: str
    surface: str
    surface2: str
    border: str
    text: str
    muted: str
    accent: str
    accent_text: str
    good: str
    warn: str
    bad: str
    sidebar: str


DARK = Palette("#0f141b", "#161d27", "#1d2633", "#2a3545", "#e6edf5", "#8b9bb0", "#3da5ff", "#06121f",
               "#3ecf8e", "#f5b84a", "#ff6b6b", "#0b1017")
LIGHT = Palette("#f3f6fa", "#ffffff", "#eef2f7", "#d3dbe6", "#17202c", "#5d6b7e", "#1a73e8", "#ffffff",
                "#1e9e63", "#c77c0a", "#d93636", "#e6ecf4")

_current = DARK


def palette() -> Palette:
    return _current


def set_theme(name: str) -> Palette:
    global _current
    _current = LIGHT if name == "light" else DARK
    return _current


def stylesheet(p: Palette | None = None) -> str:
    p = p or _current
    return f"""
* {{ font-family: "Segoe UI", "SF Pro Text", "Inter", "Noto Sans", "Helvetica Neue", Arial, sans-serif; font-size: 13px; }}
QMainWindow, QDialog, QWizard, QWidget#page {{ background: {p.bg}; color: {p.text}; }}
QWidget {{ color: {p.text}; }}
QLabel {{ background: transparent; }}
QLabel#h1 {{ font-size: 22px; font-weight: 700; }}
QLabel#h2 {{ font-size: 15px; font-weight: 600; }}
QLabel#muted {{ color: {p.muted}; }}
QLabel#statValue {{ font-size: 22px; font-weight: 700; }}
QLabel#statLabel {{ color: {p.muted}; font-size: 11px; text-transform: uppercase; letter-spacing: 1px; }}
QLabel#good {{ color: {p.good}; }} QLabel#warn {{ color: {p.warn}; }} QLabel#bad {{ color: {p.bad}; }}
QFrame#card {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px; }}
QFrame#sidebar {{ background: {p.sidebar}; border-right: 1px solid {p.border}; }}
QPushButton#nav {{ text-align: left; padding: 10px 16px; border: none; border-radius: 8px; background: transparent;
    color: {p.muted}; font-size: 14px; }}
QPushButton#nav:hover {{ background: {p.surface2}; color: {p.text}; }}
QPushButton#nav:checked {{ background: {p.surface2}; color: {p.accent}; font-weight: 600; }}
QPushButton {{ background: {p.surface2}; border: 1px solid {p.border}; border-radius: 7px; padding: 7px 16px; }}
QPushButton:hover {{ border-color: {p.accent}; }}
QPushButton:pressed {{ background: {p.border}; }}
QPushButton:disabled {{ color: {p.muted}; background: {p.surface}; border-color: {p.border}; }}
QPushButton#primary {{ background: {p.accent}; color: {p.accent_text}; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: {p.accent}; border: 1px solid {p.text}; }}
QPushButton#primary:disabled {{ background: {p.border}; color: {p.muted}; }}
QPushButton#danger {{ background: transparent; color: {p.bad}; border: 1px solid {p.bad}; }}
QPushButton#danger:hover {{ background: {p.bad}; color: {p.accent_text}; }}
QPushButton#talk {{ background: {p.surface2}; border-radius: 20px; padding: 10px 18px; font-weight: 600; }}
QPushButton#talk:pressed, QPushButton#talk[live="true"] {{ background: {p.bad}; color: white; border-color: {p.bad}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {p.surface}; border: 1px solid {p.border}; border-radius: 7px; padding: 6px 8px;
    selection-background-color: {p.accent}; selection-color: {p.accent_text}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {p.accent}; }}
QComboBox QAbstractItemView {{ background: {p.surface}; border: 1px solid {p.border}; selection-background-color: {p.accent};
    selection-color: {p.accent_text}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QListWidget::item {{ padding: 9px 12px; border-radius: 6px; margin: 1px 3px; }}
QListWidget::item:selected {{ background: {p.accent}; color: {p.accent_text}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {p.border}; background: {p.surface}; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; }}
QTableView, QTableWidget, QListWidget, QTreeWidget {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 8px;
    gridline-color: {p.border}; alternate-background-color: {p.surface2}; selection-background-color: {p.accent};
    selection-color: {p.accent_text}; }}
QHeaderView::section {{ background: {p.surface2}; color: {p.muted}; padding: 7px 8px; border: none;
    border-bottom: 1px solid {p.border}; font-weight: 600; }}
QTableView::item, QTableWidget::item {{ padding: 5px 8px; }}
QTabWidget::pane {{ border: 1px solid {p.border}; border-radius: 8px; top: -1px; background: {p.surface}; }}
QTabBar::tab {{ padding: 9px 18px; background: transparent; color: {p.muted}; border: none; }}
QTabBar::tab:selected {{ color: {p.accent}; border-bottom: 2px solid {p.accent}; }}
QProgressBar {{ background: {p.surface2}; border: 1px solid {p.border}; border-radius: 6px; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 5px; }}
QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; }}
QScrollBar::handle:horizontal {{ background: {p.border}; border-radius: 5px; min-width: 30px; }}
QStatusBar {{ background: {p.sidebar}; color: {p.muted}; border-top: 1px solid {p.border}; }}
QMenuBar {{ background: {p.sidebar}; color: {p.text}; }}
QMenuBar::item:selected {{ background: {p.surface2}; }}
QMenu {{ background: {p.surface}; border: 1px solid {p.border}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {p.accent}; color: {p.accent_text}; }}
QToolTip {{ background: {p.surface2}; color: {p.text}; border: 1px solid {p.border}; padding: 4px; }}
QGroupBox {{ border: 1px solid {p.border}; border-radius: 8px; margin-top: 14px; padding: 14px 10px 10px 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {p.muted}; font-weight: 600; }}
QSplitter::handle {{ background: {p.border}; }}
"""
