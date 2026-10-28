"""Restored windows retain desktop cards without the maximized side gutters."""
import pytest
from tests.test_gui_smoke import _app, _window
from PySide6 import QtTest
from noiserep.gui import theme


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_restored_pages_keep_desktop_layout_and_small_gutters(mode):
    app = _app()
    theme.apply(mode)
    app.setStyleSheet(theme.QSS)
    w = _window()
    try:
        w.showNormal()
        for width in (1120, 1000, 860):
            w.resize(width, 760)
            w._show_home("today")
            QtTest.QTest.qWait(120)
            assert w.today._gutter == 16
            assert w.today._cols == 12
            assert w.today.widget().width() == w.today.viewport().width()
            for left, right in ((w.today.rings_card, w.today.live_card),
                                (w.today.hr_card, w.today.vitals_card),
                                (w.today.sleep_card, w.today.sleep_stats)):
                assert left.geometry().right() < right.geometry().left()
            w._show_home("metrics")
            QtTest.QTest.qWait(120)
            assert w._trends_layout.contentsMargins().left() == 16
            assert w._trends_scroll.widget().width() == w._trends_scroll.viewport().width()
            w._show_home("settings")
            assert w._settings_layout.contentsMargins().left() == 16
    finally:
        w.close()
        theme.apply("light")
