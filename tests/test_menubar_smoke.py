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
    assert format_title(None, False) == "♥ —"


def test_icon_is_drawn_without_assets():
    _app()
    for connected in (False, True):
        icon = make_icon(connected)
        assert not icon.isNull()
        assert not icon.pixmap(22, 22).isNull()


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

        mb._connected = False
        mb._render(force=True)
        assert mb.header_action.text() == "♥ —"      # disconnected hides the value
        assert mb.source_action.text() == "Source: —"
    finally:
        mb.store.close()
