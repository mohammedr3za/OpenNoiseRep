"""Headless smoke test for the menu-bar app: builds the menu and icon without
a system tray or a running event loop. Skips when the GUI deps are absent."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("qasync")
pytest.importorskip("pyqtgraph")   # noiserep.gui.__init__ imports the main app

from PySide6 import QtWidgets  # noqa: E402

from noiserep.gui.menubar import (  # noqa: E402
    MenubarApp, format_title, make_icon,
)


def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_format_title():
    assert format_title(72, True) == "♥ 72"
    assert format_title(72, False) == "♥ —"
    assert format_title(None, True) == "♥ —"
    assert format_title(None, True, removed=True) == "♥ ~"
    assert format_title(None, False) == "♥ —"


def test_icon_is_drawn_without_assets():
    _app()
    for connected, bpm, removed in ((False, None, False), (True, 72, False),
                                   (True, None, True)):
        icon = make_icon(connected, bpm, removed)
        assert not icon.isNull()
        assert not icon.pixmap(40, 18).isNull()


def test_menu_builds_without_a_tray_and_updates():
    _app()
    mb = MenubarApp(_app(), db_path=":memory:", with_tray=False,
                    min_update_interval=0)
    try:
        assert mb.tray is None                       # no tray required
        assert mb.header_action.text() == "♥ —"
        assert mb.source_action.text() == "Source: —"
        assert mb.connect_action.text() == "Connect"
        assert not mb.sync_action.isEnabled()        # nothing to sync yet

        mb._connected = True
        mb._apply_hr(72, "live")
        assert mb.header_action.text() == "♥ 72"
        assert mb.source_action.text() == "Source: live"
        assert mb.status_action.text() == "Live"
        assert mb.connect_action.text() == "Disconnect"
        assert mb.sync_action.isEnabled()

        mb._apply_hr(64, "polled")
        assert mb.header_action.text() == "♥ 64"
        assert mb.source_action.text() == "Source: polled"

        assert mb.lock_action.text() == "Lock Mac when band goes away"
        assert not mb.lock_action.isChecked()

        mb._connected = False
        mb._render(force=True)
        assert mb.header_action.text() == "♥ —"      # disconnected hides the value
        assert mb.source_action.text() == "Source: —"
    finally:
        mb.store.close()


def test_removed_state_shows_tilde_and_outlined_heart(monkeypatch):
    _app()
    mb = MenubarApp(_app(), db_path=":memory:", with_tray=False,
                    min_update_interval=0)
    try:
        mb._connected = True
        mb._apply_hr(80, "polled")
        assert mb.header_action.text() == "♥ 80"
        mb._mark_no_hr()
        assert not mb._removed                 # one miss is not enough
        mb._mark_no_hr()
        assert mb._removed
        assert mb.header_action.text() == "♥ ~"
        assert "removed" in mb.status_action.text().lower()
        mb._apply_hr(70, "polled")
        assert not mb._removed and mb.header_action.text() == "♥ 70"
    finally:
        mb.store.close()


def test_retry_calls_connect_when_still_disconnected():
    _app()
    mb = MenubarApp(_app(), db_path=":memory:", with_tray=False,
                    retry_seconds=0.01)
    calls = []
    try:
        mb.connect = lambda: calls.append("connect")
        import asyncio
        asyncio.run(mb._retry_once())
        assert calls == ["connect"]
    finally:
        mb.store.close()


def test_away_lock_runs_after_grace(monkeypatch):
    _app()
    calls = []
    monkeypatch.setattr(MenubarApp, "_lock_screen",
                        staticmethod(lambda: calls.append("locked")))
    mb = MenubarApp(_app(), db_path=":memory:", with_tray=False,
                    min_update_interval=0, lock_on_away=True,
                    away_seconds=0)
    try:
        mb._had_connection = True
        assert mb.lock_action.isChecked()
        import asyncio
        asyncio.run(mb._lock_after_grace())
        assert calls == ["locked"]
        assert "Locked the Mac" in mb.status_action.text()
    finally:
        mb.store.close()
