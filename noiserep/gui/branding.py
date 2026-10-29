"""Application identity and window/taskbar icon; no in-page branding."""
from pathlib import Path
import sys

from PySide6 import QtGui


def apply_app_icon(app):
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("NoiseRep.Desktop")
    icon = QtGui.QIcon(str(Path(__file__).with_name("assets") / "app-icon.png"))
    app.setWindowIcon(icon)
