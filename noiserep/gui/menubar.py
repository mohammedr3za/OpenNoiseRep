"""macOS menu-bar heart rate for the Noise REP band (PySide6 + qasync).

A ``QSystemTrayIcon`` shows ``♥ 72`` (or ``♥ —`` when disconnected) and a menu
with the current value, its source, connect/disconnect, a one-shot sync, the
main window, and quit. All BLE work runs on the qasync event loop; the UI never
blocks.

Live data is attempted over the experimental realtime stream (type 8 subtype
45/46/47, see ``noiserep.protocol.realtime``). When the band does not support
it, or goes silent, the app degrades to polling the confirmed type-113 metric-2
heart-rate series every ``poll_seconds``. Commands are throttled: one poll per
interval, one traffic-light render per ``min_update_interval``.

The band accepts one central at a time. Opening the main window therefore
releases the menu bar's connection so the window's own sync can run.
"""
from __future__ import annotations

import asyncio
import datetime
import os
import subprocess
import sys
import time

import qasync
from PySide6 import QtCore, QtGui, QtWidgets

from ..errors import BandNotFound, TransportError
from ..protocol import commands, realtime
from ..store import Store, default_db_path
from ..sync import run_sync

DEFAULT_POLL_SECONDS = 30.0
# How long to wait for the first realtime event before falling back to polling.
FIRST_EVENT_TIMEOUT = 8.0
# After the stream has started, stop it once this long passes with no event.
REALTIME_SILENCE = 20.0
# How long to wait for the reply to one poll before trying again next tick.
POLL_REPLY_TIMEOUT = 6.0
# Consecutive polls with no heart rate before the band counts as removed.
REMOVED_AFTER_POLLS = 2
# Grace period before a lost band locks the Mac (opt-in feature).
AWAY_GRACE_SECONDS = 15.0


def format_title(bpm: int | None, connected: bool,
                 removed: bool = False) -> str:
    """The menu/tooltip value: ``♥ 72``, ``♥ ~`` (removed) or ``♥ —``."""
    if not connected:
        return "♥ —"
    if bpm is None:
        return "♥ ~" if removed else "♥ —"
    return f"♥ {int(bpm)}"


def make_icon(connected: bool, bpm: int | None = None,
              removed: bool = False, size: int = 18) -> QtGui.QIcon:
    """A monochrome heart + number template, drawn with QPainter.

    macOS shows only the tray *icon*, not ``setTitle`` text, so the number is
    painted beside the heart. The heart is filled only while a real BPM is
    known; when the band is off-wrist it is outlined and the label is ``~``.
    ``setIsMask`` makes macOS treat it as a template image that adapts to the
    menu bar's light/dark appearance.
    """
    ratio = 2
    width = size + 40
    pix = QtGui.QPixmap(width * ratio, size * ratio)
    pix.setDevicePixelRatio(ratio)
    pix.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pix)
    painter.setRenderHint(QtGui.QPainter.Antialiasing)
    s = float(size)
    path = QtGui.QPainterPath()
    path.moveTo(s * 0.50, s * 0.88)
    path.cubicTo(s * 0.10, s * 0.58, s * 0.08, s * 0.34, s * 0.25, s * 0.24)
    path.cubicTo(s * 0.38, s * 0.16, s * 0.48, s * 0.23, s * 0.50, s * 0.32)
    path.cubicTo(s * 0.52, s * 0.23, s * 0.62, s * 0.16, s * 0.75, s * 0.24)
    path.cubicTo(s * 0.92, s * 0.34, s * 0.90, s * 0.58, s * 0.50, s * 0.88)
    path.closeSubpath()

    filled = connected and bpm is not None
    if filled:
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(0, 0, 0))
    else:
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 170), 1.6))
    painter.drawPath(path)

    label = (str(int(bpm)) if filled else
             ("~" if removed else "—"))
    font = QtGui.QFont("Helvetica Neue")
    font.setPixelSize(13)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QtGui.QColor(0, 0, 0))
    painter.drawText(
        QtCore.QRectF(s + 3, 0, width - s - 3, s),
        QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, label)
    painter.end()

    icon = QtGui.QIcon(pix)
    if hasattr(icon, "setIsMask"):
        icon.setIsMask(True)          # macOS template rendering
    return icon


class MenubarApp(QtCore.QObject):
    """Tray icon, menu, and the BLE session feeding them.

    Construct with ``with_tray=False`` for headless tests: the menu and actions
    are always built, but no ``QSystemTrayIcon`` is created or shown.
    """

    def __init__(self, app: QtWidgets.QApplication, band_name: str | None = None,
                 db_path: str | None = None,
                 poll_seconds: float = DEFAULT_POLL_SECONDS,
                 first_event_timeout: float = FIRST_EVENT_TIMEOUT,
                 realtime_silence: float = REALTIME_SILENCE,
                 reply_timeout: float = POLL_REPLY_TIMEOUT,
                 min_update_interval: float = 1.0,
                 with_tray: bool = True,
                 lock_on_away: bool = False,
                 away_seconds: float = AWAY_GRACE_SECONDS):
        super().__init__()
        self.app = app
        self.band_name = band_name            # None -> auto-discover Noise_REP_*
        self.poll_seconds = max(1.0, float(poll_seconds))
        self.first_event_timeout = first_event_timeout
        self.realtime_silence = realtime_silence
        self.reply_timeout = reply_timeout
        self.min_update_interval = max(0.0, float(min_update_interval))
        self.lock_on_away = bool(lock_on_away)
        self.away_seconds = max(0.0, float(away_seconds))
        self.store = Store(db_path or default_db_path())

        self._hr: int | None = None
        self._source: str | None = None
        self._connected = False
        self._connecting = False
        self._syncing = False
        self._quitting = False
        self._realtime_active = False
        self._status = "Disconnected — press Connect"
        self._band = None
        self._ctx = None
        self._worker_task: asyncio.Task | None = None
        self._connect_task: asyncio.Task | None = None
        self._away_task: asyncio.Task | None = None
        self._had_connection = False
        self._removed = False
        self._hr_misses = 0
        self._op_lock = asyncio.Lock()
        self._window: QtWidgets.QMainWindow | None = None
        self._last_render = 0.0
        self._dirty = False
        self._render_timer = QtCore.QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.timeout.connect(self._on_render_timer)

        self._build_menu()
        self.tray: QtWidgets.QSystemTrayIcon | None = None
        if with_tray and QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QtWidgets.QSystemTrayIcon(make_icon(False), self)
            self.tray.setContextMenu(self.menu)
            self.tray.activated.connect(self._on_tray_activated)
        self._render(force=True)

    # -- menu ------------------------------------------------------------
    def _build_menu(self) -> None:
        self.menu = QtWidgets.QMenu()

        self.header_action = QtGui.QAction("♥ —", self)
        font = self.header_action.font()
        font.setBold(True)
        self.header_action.setFont(font)
        self.header_action.setEnabled(False)
        self.menu.addAction(self.header_action)

        self.source_action = QtGui.QAction("Source: —", self)
        self.source_action.setEnabled(False)
        self.menu.addAction(self.source_action)

        self.status_action = QtGui.QAction(self._status, self)
        self.status_action.setEnabled(False)
        self.menu.addAction(self.status_action)
        self.menu.addSeparator()

        self.connect_action = QtGui.QAction("Connect", self)
        self.connect_action.triggered.connect(self._on_connect_action)
        self.menu.addAction(self.connect_action)

        self.sync_action = QtGui.QAction("Sync now", self)
        self.sync_action.triggered.connect(self.sync_now)
        self.menu.addAction(self.sync_action)

        self.lock_action = QtGui.QAction(
            "Lock Mac when band goes away", self)
        self.lock_action.setCheckable(True)
        self.lock_action.setChecked(self.lock_on_away)
        self.lock_action.toggled.connect(self._set_lock_on_away)
        self.menu.addAction(self.lock_action)
        self.menu.addSeparator()

        self.open_action = QtGui.QAction("Open main window", self)
        self.open_action.triggered.connect(self.open_main_window)
        self.menu.addAction(self.open_action)

        self.quit_action = QtGui.QAction("Quit", self)
        self.quit_action.triggered.connect(self.quit)
        self.menu.addAction(self.quit_action)

    def _source_text(self) -> str:
        if not self._connected:
            return "Source: —"
        return f"Source: {self._source or 'waiting'}"

    def _tooltip(self) -> str:
        if not self._connected:
            return "NoiseREP — not connected"
        if self._removed:
            return "NoiseREP — band removed or no heart rate"
        if self._hr is None:
            return "NoiseREP — waiting for heart rate"
        return f"NoiseREP — ♥ {self._hr} bpm ({self._source or 'live'})"

    def _render(self, force: bool = False) -> None:
        """Update the menu/tray, throttled unless ``force``."""
        now = time.monotonic()
        elapsed = now - self._last_render
        if not force and elapsed < self.min_update_interval:
            self._dirty = True
            if not self._render_timer.isActive():
                delay = max(1, int((self.min_update_interval - elapsed) * 1000))
                self._render_timer.start(delay)
            return
        self._last_render = now
        self._dirty = False
        if self._render_timer.isActive():
            self._render_timer.stop()

        title = format_title(self._hr, self._connected, self._removed)
        self.header_action.setText(title)
        self.source_action.setText(self._source_text())
        self.status_action.setText(self._status)

        if self._connecting:
            self.connect_action.setText("Connecting…")
        elif self._connected:
            self.connect_action.setText("Disconnect")
        else:
            self.connect_action.setText("Connect")
        self.connect_action.setEnabled(not self._connecting and not self._syncing)

        self.sync_action.setText("Syncing…" if self._syncing else "Sync now")
        self.sync_action.setEnabled(self._connected and not self._syncing)
        self.quit_action.setEnabled(not self._quitting)

        if self.tray is not None:
            self.tray.setIcon(
                make_icon(self._connected, self._hr, self._removed))
            self.tray.setToolTip(self._tooltip())

    def _on_render_timer(self) -> None:
        if self._dirty:
            self._render(force=True)
        else:
            self._render_timer.stop()

    # -- state helpers ---------------------------------------------------
    def _apply_hr(self, bpm: int, source: str) -> None:
        value = int(bpm)
        if value <= 0:
            return
        self._hr = value
        self._source = source
        self._removed = False
        self._hr_misses = 0
        self._status = "Live" if source == "live" else "Polled"
        self._render()

    def _mark_no_hr(self) -> None:
        """Count a poll with no BPM; after two, show the removed state."""
        if not self._connected:
            return
        self._hr_misses += 1
        if self._hr_misses >= REMOVED_AFTER_POLLS and not self._removed:
            self._removed = True
            self._hr = None
            self._source = None
            self._status = "Band removed or no heart rate"
            self._render(force=True)

    def _set_lock_on_away(self, enabled: bool) -> None:
        self.lock_on_away = bool(enabled)
        self._status = ("Will lock the Mac if the band goes away"
                        if self.lock_on_away else "Away-lock off")
        self._render(force=True)

    def _schedule_away_lock(self) -> None:
        """After an unexpected disconnect, lock the Mac if opted in."""
        if (not self.lock_on_away or not self._had_connection
                or self._quitting):
            return
        if self._away_task is not None and not self._away_task.done():
            return
        self._away_task = self._spawn(self._lock_after_grace())

    async def _lock_after_grace(self) -> None:
        seconds = max(0.0, self.away_seconds)
        self._status = (f"Band away — locking Mac in {int(seconds)}s"
                        if seconds else "Band away — locking Mac")
        self._render(force=True)
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            raise
        if self._connected or self._quitting:
            return
        self._lock_screen()
        self._status = "Locked the Mac because the band went away"
        self._render(force=True)

    @staticmethod
    def _lock_screen():
        """Lock the Mac locally. Returns the command used, or None."""
        candidates = [
            ["/System/Library/CoreServices/Menu Extras/User.menu/Contents/"
             "Resources/CGSession", "-suspend"],
            ["/usr/bin/pmset", "displaysleepnow"],
            ["/usr/bin/open", "-a", "ScreenSaverEngine"],
        ]
        for command in candidates:
            if command[0] != "/usr/bin/open" and not os.path.exists(command[0]):
                continue
            try:
                subprocess.run(command, timeout=5, check=False)
                return command[0]
            except (OSError, subprocess.SubprocessError):
                continue
        return None

    def _spawn(self, coro) -> asyncio.Task | None:
        try:
            return asyncio.ensure_future(coro)
        except RuntimeError:
            coro.close()
            self._status = "No asyncio loop running"
            self._render(force=True)
            return None

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self.tray is not None:
            self.tray.show()
        self._render(force=True)
        self.connect()

    def connect(self) -> None:
        if self._connected or self._connecting:
            return
        self._connecting = True
        self._status = "Scanning for band…"
        self._render(force=True)
        self._connect_task = self._spawn(self._do_connect())

    async def _do_connect(self) -> None:
        from ..ble.connection import connect
        try:
            ctx = connect(self.band_name)
            band = await ctx.__aenter__()
        except BandNotFound as exc:
            self._connecting = False
            self._status = f"{exc} — put the band on its charger"
            self._render(force=True)
            return
        except TransportError as exc:
            self._connecting = False
            self._status = f"Transport error: {exc}"
            self._render(force=True)
            return
        except asyncio.CancelledError:
            self._connecting = False
            raise
        except Exception as exc:                 # any BLE/OS failure degrades
            self._connecting = False
            self._status = f"Could not connect: {exc}"
            self._render(force=True)
            return
        self._ctx, self._band = ctx, band
        self._connected = True
        self._connecting = False
        self._had_connection = True
        self._removed = False
        self._hr_misses = 0
        self._cancel_away_lock()
        self._status = "Connected"
        self._render(force=True)
        self._start_worker()

    def _start_worker(self) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._worker_task = self._spawn(self._worker())

    async def _worker(self) -> None:
        """Own ``band.recv``: realtime first, then confirmed polling."""
        try:
            live = await self._realtime_phase()
            if not self._connected:
                return
            self._status = ("Live stream ended — polling" if live
                            else "Polling heart rate")
            self._render(force=True)
            await self._poll_phase()
        except asyncio.CancelledError:
            raise
        except (TransportError, OSError) as exc:
            await self._teardown()
            self._status = f"Connection lost: {exc}"
            self._render(force=True)
            self._schedule_away_lock()
        except Exception as exc:                 # never kill the tray silently
            await self._teardown()
            self._status = f"Live update stopped: {exc}"
            self._render(force=True)
            self._schedule_away_lock()

    async def _teardown(self) -> None:
        ctx, self._ctx = self._ctx, None
        self._band = None
        self._connected = False
        self._connecting = False
        self._realtime_active = False
        self._hr = None
        self._source = None
        self._removed = False
        self._hr_misses = 0
        if ctx is not None:
            try:
                await asyncio.wait_for(ctx.__aexit__(None, None, None), timeout=5.0)
            except Exception:
                pass

    def _cancel_away_lock(self) -> None:
        task, self._away_task = self._away_task, None
        if task is not None and not task.done():
            task.cancel()

    async def _do_disconnect(self, status: str = "Disconnected") -> None:
        self._cancel_away_lock()
        worker, self._worker_task = self._worker_task, None
        if (worker is not None and worker is not asyncio.current_task()
                and not worker.done()):
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        connect_task, self._connect_task = self._connect_task, None
        if (connect_task is not None and connect_task is not asyncio.current_task()
                and not connect_task.done()):
            connect_task.cancel()
            await asyncio.gather(connect_task, return_exceptions=True)
        await self._teardown()
        self._status = status
        self._render(force=True)

    # -- realtime (experimental) ----------------------------------------
    async def _realtime_phase(self) -> bool:
        """Try the realtime stream; return True if any sample arrived."""
        band = self._band
        if band is None:
            return False
        try:
            async with self._op_lock:
                await band.send(realtime.start_frame())
        except (TransportError, asyncio.TimeoutError):
            return False                     # NACK/timeout: poll instead
        self._realtime_active = True

        loop = asyncio.get_running_loop()
        got = False
        last = loop.time()
        first_deadline = last + self.first_event_timeout
        try:
            while self._connected:
                try:
                    payload = await band.recv(timeout=5.0)
                except asyncio.TimeoutError:
                    now = loop.time()
                    if not got and now >= first_deadline:
                        break
                    if got and now - last >= self.realtime_silence:
                        break
                    continue
                bpm = realtime.parse_bpm(payload)
                if bpm is None:
                    continue                 # status echoes, other traffic
                got = True
                last = loop.time()
                self._apply_hr(bpm, "live")
        finally:
            try:
                async with self._op_lock:
                    await asyncio.wait_for(
                        band.send(realtime.stop_frame()), timeout=3.0)
                self._realtime_active = False
            except asyncio.CancelledError:
                raise                         # pause_worker will retry the stop
            except Exception:
                self._realtime_active = False  # stop attempted; best effort
        return got

    # -- confirmed polling fallback -------------------------------------
    async def _poll_phase(self) -> None:
        while self._connected and not self._quitting:
            try:
                bpm = await self._poll_once()
            except (TransportError, OSError) as exc:
                await self._teardown()
                self._status = f"Connection lost: {exc}"
                self._render(force=True)
                self._schedule_away_lock()
                return
            if bpm is not None:
                self._apply_hr(bpm, "polled")
            else:
                self._mark_no_hr()
            await asyncio.sleep(self.poll_seconds)

    async def _poll_once(self) -> int | None:
        band = self._band
        if band is None:
            return None
        # Use the whole device timeline: the band's RTC may be unset (~2002).
        since = datetime.datetime(2000, 1, 1)
        async with self._op_lock:
            await band.send(commands.series(113, since, realtime.HEART_RATE_METRIC))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.reply_timeout
        for _ in range(6):
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                payload = await band.recv(timeout=remaining)
            except asyncio.TimeoutError:
                break
            bpm = realtime.latest_series_bpm(payload)
            if bpm is not None:
                return bpm
        return None

    # -- actions ---------------------------------------------------------
    @qasync.asyncSlot()
    async def _on_connect_action(self) -> None:
        if self._connected or self._connecting:
            await self._do_disconnect()
        else:
            self.connect()

    def _on_tray_activated(self, reason) -> None:
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick:
            self.open_main_window()

    @qasync.asyncSlot()
    async def sync_now(self) -> None:
        """One-shot historical sync: run_sync(today's midnight) + Store.ingest."""
        if not self._connected or self._band is None:
            self._status = "Connect to the band first"
            self._render(force=True)
            return
        if self._syncing:
            return
        self._syncing = True
        self._status = "Syncing…"
        self._render(force=True)
        await self._pause_worker()
        try:
            since = datetime.datetime(2000, 1, 1)
            result = await run_sync(self._band, since,
                                    progress=self._on_sync_progress)
            counts = self.store.ingest(result, note="menubar sync")
            self._status = (f"Synced +{counts['samples']} samples, "
                            f"+{counts['files']} files")
        except BandNotFound as exc:
            self._status = f"Sync failed: {exc}"
        except TransportError as exc:
            self._status = f"Sync transport error: {exc}"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._status = f"Sync failed: {exc}"
        finally:
            self._syncing = False
            if self._connected:
                self._start_worker()
            self._render(force=True)

    def _on_sync_progress(self, done: int, total: int, stage: str) -> None:
        self._status = f"Syncing — {stage}"
        self._render()

    async def _pause_worker(self) -> None:
        worker, self._worker_task = self._worker_task, None
        if worker is not None and not worker.done():
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        # Cancel may have interrupted the realtime stop frame; send it now,
        # bounded, so the band does not keep streaming into the sync.
        band = self._band
        if band is not None and self._realtime_active:
            try:
                async with self._op_lock:
                    await asyncio.wait_for(
                        band.send(realtime.stop_frame()), timeout=3.0)
            except Exception:
                pass
            self._realtime_active = False

    def open_main_window(self) -> None:
        """Open the full app. The band allows one central, so release ours."""
        if self._connected or self._connecting:
            self._spawn(self._do_disconnect(status="Paused (main window open)"))
        if self._window is None:
            try:
                from .app import MainWindow
                self._window = MainWindow(self.store, self.band_name)
            except Exception as exc:
                self._status = f"Could not open main window: {exc}"
                self._render(force=True)
                return
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._spawn(self._shutdown())

    async def _shutdown(self) -> None:
        await self._do_disconnect(status="Quitting")
        try:
            self.store.close()
        except Exception:
            pass
        if self.tray is not None:
            self.tray.hide()
        self.app.quit()


def main(argv: list[str] | None = None, band_name: str | None = None,
         db_path: str | None = None,
         poll_seconds: float = DEFAULT_POLL_SECONDS,
         lock_on_away: bool = False,
         away_seconds: float = AWAY_GRACE_SECONDS) -> int:
    """Run the menu-bar app until Quit. ``band_name=None`` auto-discovers."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(
        list(argv) if argv is not None else sys.argv)
    app.setApplicationName("NoiseREP")
    app.setQuitOnLastWindowClosed(False)
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    menubar = MenubarApp(app, band_name=band_name, db_path=db_path,
                         poll_seconds=poll_seconds,
                         lock_on_away=lock_on_away,
                         away_seconds=away_seconds)
    if menubar.tray is None:
        print("menubar: no system tray available on this desktop", file=sys.stderr)
        return 1
    menubar.start()
    with loop:
        loop.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
