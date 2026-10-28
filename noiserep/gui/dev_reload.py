"""Opt-in development reloading, independent of Bluetooth and band code."""
from pathlib import Path
import time
from PySide6 import QtCore


def source_snapshot(root):
    return {p: (p.stat().st_mtime_ns, p.stat().st_size)
            for p in root.rglob('*') if p.is_file()
            and p.suffix in {'.py', '.png', '.svg', '.qss'}
            and '__pycache__' not in p.parts}


class DevReloader(QtCore.QObject):
    def __init__(self, window, root=None):
        super().__init__(window)
        self.window = window
        self.root = root or Path(__file__).resolve().parents[1]
        self.snapshot = source_snapshot(self.root)
        self.changed_at = None
        self.pending = set()
        self.requested = False
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self.check)
        self.timer.start()

    def check(self):
        try:
            current = source_snapshot(self.root)
        except OSError:
            return  # an editor may be replacing a file atomically
        if current != self.snapshot:
            self.pending.update(p for p in current if current[p] != self.snapshot.get(p))
            self.snapshot = current
            self.changed_at = time.monotonic()
            return
        if self.changed_at is None or time.monotonic() - self.changed_at < 0.8:
            return
        try:
            for path in self.pending:
                if path.suffix == '.py' and path.exists():
                    compile(path.read_bytes(), str(path), 'exec')
        except (SyntaxError, OSError) as exc:
            self.window._set_status(f"Auto-reload waiting: {exc}")
            return
        self.timer.stop()
        settings = QtCore.QSettings('NoiseREP', 'Development')
        settings.setValue('geometry', self.window.saveGeometry())
        settings.setValue('page', self.window._home)
        self.requested = True
        self.window.close()  # existing application shutdown, unchanged
