"""Theme-aware window chrome with native Windows edge resizing."""
import sys
from PySide6 import QtCore, QtGui, QtWidgets
from . import theme


class WindowButton(QtWidgets.QAbstractButton):
    def __init__(self, action, parent=None):
        super().__init__(parent)
        self.action = action
        self.setFixedSize(34, 32)
        self.setAccessibleName(action.capitalize())
        self.setToolTip(action.capitalize())
        self.setCursor(QtCore.Qt.PointingHandCursor)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        if self.underMouse() or self.isDown():
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor('#D93646' if self.action == 'close' else theme.PANEL_HOVER))
            p.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1), 8, 8)
        color = '#FFFFFF' if self.action == 'close' and self.underMouse() else theme.FG
        p.setPen(QtGui.QPen(QtGui.QColor(color), 1.4))
        x, y = self.width() / 2, self.height() / 2
        if self.action == 'minimize':
            p.drawLine(QtCore.QPointF(x-5,y), QtCore.QPointF(x+5,y))
        elif self.action == 'close':
            p.drawLine(QtCore.QPointF(x-4,y-4), QtCore.QPointF(x+4,y+4))
            p.drawLine(QtCore.QPointF(x+4,y-4), QtCore.QPointF(x-4,y+4))
        elif self.window().isMaximized():
            p.drawRect(QtCore.QRectF(x-3,y-5,8,8))
            p.fillRect(QtCore.QRectF(x-5,y-3,8,8), QtGui.QColor(theme.PANEL))
            p.drawRect(QtCore.QRectF(x-5,y-3,8,8))
        else:
            p.drawRect(QtCore.QRectF(x-4,y-4,8,8))


class TitleSurface(QtWidgets.QFrame):
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            window = self.window()
            window.showNormal() if window.isMaximized() else window.showMaximized()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class AppWindow(QtWidgets.QMainWindow):
    """Frameless shell; close still goes through MainWindow.closeEvent."""
    def __init__(self):
        super().__init__()
        self.setWindowFlag(QtCore.Qt.FramelessWindowHint)

    def nativeEvent(self, event_type, message):
        if sys.platform == 'win32' and not self.isMaximized() and not self.isFullScreen():
            import ctypes
            from ctypes import wintypes
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0084:  # WM_NCHITTEST
                x = ctypes.c_short(msg.lParam & 0xffff).value
                y = ctypes.c_short((msg.lParam >> 16) & 0xffff).value
                pt = self.mapFromGlobal(QtCore.QPoint(x, y))
                left, right = pt.x() < 6, pt.x() >= self.width()-6
                top, bottom = pt.y() < 6, pt.y() >= self.height()-6
                hit = (13 if left else 14 if right else 12) if top else (
                    (16 if left else 17 if right else 15) if bottom else
                    (10 if left else 11 if right else None))
                if hit is not None:
                    return True, hit
        return super().nativeEvent(event_type, message)
