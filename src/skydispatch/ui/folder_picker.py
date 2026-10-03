from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog

from ..sim.installed import candidate_roots


def pick_packages_folder(parent, title: str, current: str = "") -> str:
    """Folder picker that can enter the MSFS Store install (its LocalCache is a junction the native dialog refuses)."""
    start = current.strip().strip('"')
    if not start or not Path(start).is_dir():
        start = next((str(r) for r in candidate_roots() if r.is_dir()), str(Path.home()))
    return QFileDialog.getExistingDirectory(parent, title, start, QFileDialog.Option.ShowDirsOnly
                                            | QFileDialog.Option.DontUseNativeDialog)
