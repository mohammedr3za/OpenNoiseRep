"""Apple-inspired light (default) and dark themes for the NoiseREP window.

Token names are part of the GUI's minimal API and stay stable for other
modules (``BG``, ``PANEL``, ``BORDER``, ``FG``, ``FG_MUTED``, ``ACCENT``,
``ACCENT_HOVER``, ``ON_ACCENT``, ``SERIES``, ``QSS``).

Soft off-white / near-black surfaces, hairline borders, no shadows, and Apple's health colours:
each metric owns one hue (``HEART``, ``STRAIN``, ``SLEEP``...) so a colour
means the same thing everywhere.  ``FOCUS`` stays reserved for keyboard focus.
"""
from __future__ import annotations

import sys

LIGHT = {
    "BG": "#F5F5F7", "PANEL": "#FFFFFF", "PANEL_HOVER": "#F0F0F3",
    "BORDER": "#E5E5EA", "FG": "#1D1D1F", "FG_MUTED": "#6E6E73",
    "ACCENT": "#007AFF", "ACCENT_HOVER": "#0A6BDB", "ON_ACCENT": "#FFFFFF",
    "ERROR": "#FF3B30", "FOCUS": "#007AFF",
    "HEART": "#FF2D55", "STRAIN": "#007AFF", "SLEEP": "#AF52DE",
    "RECOVERY_GOOD": "#34C759", "RECOVERY_MID": "#FFB000",
    "RECOVERY_LOW": "#FF3B30", "STRESS": "#FF9500", "OXYGEN": "#32ADE6",
    "STEPS": "#34C759", "ENERGY": "#FF9500",
    "ACCENT_FILL": "rgba(0,122,255,0.10)", "ACCENT_LINE": "rgba(0,122,255,0.6)",
    "INFO_BG": "#EAF3FF", "INFO_BORDER": "#CFE2FA", "INFO_FG": "#1F4A7A",
    "ERROR_BG": "#FFECEA", "ERROR_BORDER": "#F6C9C4", "ERROR_TEXT": "#B3261E",
}
DARK = {
    "BG": "#000000", "PANEL": "#1C1C1E", "PANEL_HOVER": "#2C2C2E",
    "BORDER": "#2C2C2E", "FG": "#F5F5F7", "FG_MUTED": "#98989D",
    "ACCENT": "#0A84FF", "ACCENT_HOVER": "#409CFF", "ON_ACCENT": "#FFFFFF",
    "ERROR": "#FF453A", "FOCUS": "#64D2FF",
    "HEART": "#FF375F", "STRAIN": "#0A84FF", "SLEEP": "#BF5AF2",
    "RECOVERY_GOOD": "#30D158", "RECOVERY_MID": "#FFD60A",
    "RECOVERY_LOW": "#FF453A", "STRESS": "#FF9F0A", "OXYGEN": "#64D2FF",
    "STEPS": "#32D74B", "ENERGY": "#FF9F0A",
    "ACCENT_FILL": "rgba(10,132,255,0.16)", "ACCENT_LINE": "rgba(10,132,255,0.6)",
    "INFO_BG": "#16263A", "INFO_BORDER": "#1F3A5A", "INFO_FG": "#C9DDF5",
    "ERROR_BG": "#3A1715", "ERROR_BORDER": "#5C2420", "ERROR_TEXT": "#FF9F97",
}

# Layout: the content column is centred; each side gutter is the larger of
# GUTTER_FRAC of the window width and whatever keeps content <= CONTENT_MAX.
GUTTER_FRAC = 0.15
CONTENT_MAX = 1600
CONTENT_MIN = 720
GUTTER_MIN = 24

mode = "light"
ACCENT_DEEP = ACCENT_DEEP_HOVER = SERIES = PALETTE = None  # set by apply()


def gutter(width: int) -> int:
    """Side gutter in px for a window ``width`` wide."""
    g = max(width * GUTTER_FRAC, (width - CONTENT_MAX) / 2)
    return int(max(GUTTER_MIN, min(g, (width - CONTENT_MIN) / 2)))


def apply(new_mode: str) -> None:
    """Switch the palette ("light" or "dark") and rebuild ``QSS``.

    Modules read ``theme.X`` at use time, so the switch takes effect on the
    next paint / ``app.setStyleSheet(theme.QSS)``.
    """
    g = globals()
    g["mode"] = new_mode
    g.update(DARK if new_mode == "dark" else LIGHT)
    g["ACCENT_DEEP"], g["ACCENT_DEEP_HOVER"] = g["ACCENT"], g["ACCENT_HOVER"]
    g["SERIES"] = [g[k] for k in ("HEART", "STRAIN", "SLEEP", "STEPS", "STRESS", "OXYGEN")]
    g["PALETTE"] = {
        "bg": g["BG"], "panel": g["PANEL"], "panel_hover": g["PANEL_HOVER"],
        "border": g["BORDER"], "fg": g["FG"], "fg_muted": g["FG_MUTED"],
        "accent": g["ACCENT"],
    }
    if "QSS" in g:
        g["QSS"] = build_qss()


FONT_FAMILY = ('"SF Pro Display", "SF Pro Text", "Segoe UI Variable Display", '
               '"Segoe UI Variable Text", "Segoe UI", "Helvetica Neue", '
               'system-ui, sans-serif')


def is_macos(platform: str | None = None) -> bool:
    """True when the app runs on macOS and AppKit should draw the controls.

    ``platform`` is injectable so tests can exercise both branches without
    leaving the host platform.
    """
    return (sys.platform if platform is None else platform) == "darwin"


def build_qss(macos: bool | None = None) -> str:
    """Return the window stylesheet for the host (or the requested) platform.

    On macOS the date-range combo, its popup, menus and the Export control are
    left entirely to AppKit: the only rules that mention them are omitted.
    Other platforms keep the custom rounded look.  ``QSS`` below is the final
    string for the host platform.
    """
    native = is_macos() if macos is None else macos
    glass = "rgba(28,28,30,245)" if mode == "dark" else "rgba(255,255,255,245)"

    # Any bare QWidget rule also matches QComboBox/QToolButton and switches
    # them to Qt's stylesheet renderer, losing AppKit's chrome.  On macOS it
    # is omitted; the same text properties are still set on the concrete
    # non-native widget types below.  Elsewhere the bare rule is what gives
    # every page a transparent background.
    widget_rule = (
        "" if native else
        f"QWidget {{ background: transparent; color: {FG}; "
        f"font-size: 13px; }}"
    )
    root_rule = f"QMainWindow, QWidget#root {{ background: {BG}; }}"

    qss = f"""
* {{ font-family: {FONT_FAMILY}; }}

{widget_rule}
{root_rule}

QLabel {{ background: transparent; color: {FG}; font-size: 13px; }}
QLabel#muted {{ color: {FG_MUTED}; font-size: 12px; }}
QLabel#bodyText {{ color: {FG}; font-size: 13px; }}
QLabel#hint {{ color: {FG_MUTED}; font-size: 12px; }}
QLabel#status {{ color: {FG_MUTED}; font-size: 12px; }}
QLabel#sectionLabel {{ color: {FG_MUTED}; font-size: 10px; letter-spacing: 1.4px; }}
QLabel#rangeLabel {{ color: {FG_MUTED}; font-size: 12px; }}
QLabel#shortcuts {{ color: {FG_MUTED}; font-size: 10px; }}

/* app bar */
QFrame#appbar {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
QLabel#appName {{ font-size: 18px; font-weight: 700; color: {FG};
    letter-spacing: -0.3px; }}
QLabel#appTag {{ font-size: 10px; font-weight: 600; color: {FG_MUTED};
    letter-spacing: 1.4px; }}
QLabel#brandDot {{ font-size: 18px; color: {ACCENT}; }}
QLabel#devicePill {{ color: {FG_MUTED}; font-size: 11px; }}
QFrame#devicePill {{ background: {PANEL_HOVER}; border: none;
    border-radius: 13px; }}
QLabel#bandDot {{ font-size: 11px; color: {FG_MUTED}; }}
QLabel#bandDot[state="live"] {{ color: {RECOVERY_GOOD}; }}
QLabel#bandDot[state="charging"] {{ color: {RECOVERY_MID}; }}
QLabel#bandDot[state="searching"] {{ color: {FOCUS}; }}
QLabel#bandDot[state="idle"] {{ color: {FG_MUTED}; }}
QLabel#bandState {{ color: {FG}; font-size: 12px; font-weight: 600; }}
QFrame#liveStrip {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
QLabel#liveHeart {{ color: {HEART}; font-size: 26px; }}
QLabel#liveValue {{ color: {FG}; font-size: 22px; font-weight: 650; }}
QLabel#liveUnit {{ color: {FG_MUTED}; font-size: 11px; padding-bottom: 3px; }}
QLabel#liveCaption {{ color: {FG_MUTED}; font-size: 10px; font-weight: 600;
    letter-spacing: 0.8px; }}

/* Today page */
QWidget#todayHost {{ background: {BG}; }}
QFrame#todayCard {{ background: {PANEL}; border: 1px solid {BORDER};
    border-radius: 16px; }}
QPushButton#pillBtn {{ background: {PANEL}; color: {FG}; border: 1px solid {BORDER};
    border-radius: 15px; padding: 5px 14px; font-size: 13px; font-weight: 600; }}
QPushButton#pillBtn:hover {{ background: {PANEL_HOVER}; }}
QFrame#segmented {{ background: {PANEL_HOVER}; border-radius: 12px; }}
QPushButton#navTab {{ background: transparent; color: {FG_MUTED}; border: none;
    border-radius: 9px; padding: 6px 18px; font-size: 13px; font-weight: 600; }}
QPushButton#navTab:checked {{ background: {PANEL}; color: {FG}; }}
QPushButton#navTab:hover {{ color: {FG}; }}
QPushButton#navTab:focus {{ border: none; }}
QPushButton#liveBtn, QPushButton#themeBtn {{ background: transparent;
    color: {FG}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 7px 13px; font-size: 13px; }}
QPushButton#liveBtn:hover, QPushButton#themeBtn:hover {{ background: {PANEL_HOVER}; }}
QPushButton#liveBtn[active="true"] {{ background: {ACCENT_FILL}; color: {HEART};
    border: 1px solid {HEART}; font-weight: 600; }}
QPushButton#themeBtn {{ padding: 0; border-radius: 19px; font-size: 16px;
    background: {PANEL_HOVER}; border: none; }}
QPushButton#themeBtn:hover {{ background: {BORDER}; }}
QLabel#syncPct {{ color: {ACCENT}; font-size: 12px; font-weight: 600; }}

/* icon guide */
QFrame#guideCard {{ background: {PANEL}; border: 1px solid {BORDER};
    border-radius: 16px; }}
QLabel#guidePill {{ background: {BG}; border: 1px solid {BORDER};
    border-radius: 14px; padding: 6px 16px; font-size: 17px; font-weight: 600;
    color: {FG}; }}
QLabel#guidePill[on="true"] {{ color: {HEART}; }}
QLabel#guideTitle {{ font-size: 30px; font-weight: 700; color: {FG}; }}
QLabel#guideLead {{ font-size: 15px; color: {FG_MUTED}; }}
QLabel#guideState {{ font-size: 16px; font-weight: 600; color: {FG}; }}
QLabel#guideBody {{ font-size: 13px; color: {FG_MUTED}; }}
QLabel#guideNum {{ background: {BG}; border-radius: 11px; color: {FG_MUTED};
    font-size: 12px; font-weight: 600; min-width: 22px; max-width: 22px;
    min-height: 22px; max-height: 22px; }}

/* typography */
QLabel#greeting {{ font-size: 30px; font-weight: 700; color: {FG}; }}
QLabel#metricTitle {{ font-size: 21px; font-weight: 700; color: {FG}; }}
QLabel#metricUnit {{ font-size: 15px; color: {FG_MUTED}; }}
QLabel#heroName {{ font-size: 12px; font-weight: 600; color: {FG_MUTED};
    letter-spacing: 0.6px; }}
QLabel#heroValue {{ font-size: 64px; font-weight: 700; color: {FG}; }}
QLabel#heroUnit {{ font-size: 17px; color: {FG_MUTED}; }}
QLabel#tileName {{ font-size: 11px; font-weight: 600; color: {FG_MUTED};
    letter-spacing: 1px; }}
QLabel#tileValue {{ font-size: 28px; font-weight: 700; color: {FG}; }}
QLabel#tileUnit {{ font-size: 12px; color: {FG_MUTED}; }}
QLabel#statNum {{ font-size: 26px; font-weight: 700; color: {FG}; }}
QLabel#statNumAccent {{ font-size: 26px; font-weight: 700; color: {ACCENT_DEEP}; }}
QLabel#statLabel {{ color: {FG_MUTED}; font-size: 10px; letter-spacing: 1.2px; }}
QLabel#chipValue {{ font-size: 16px; font-weight: 600; color: {FG}; }}
QLabel#chevron {{ font-size: 20px; color: {FG_MUTED}; }}
QLabel#guideGlyph {{ font-size: 30px; color: {ACCENT}; }}
QLabel#guideGlyphMuted {{ font-size: 30px; color: {FG_MUTED}; }}

/* cards */
QFrame#card {{ background: {PANEL}; border: 1px solid {BORDER};
    border-radius: 16px; }}
QFrame#hero {{ background: {PANEL}; border: 1px solid {BORDER};
    border-radius: 18px; }}
QFrame#hero:hover {{ border: 1px solid {ACCENT_HOVER}; }}
QFrame#hero:focus {{ border: 2px solid {FOCUS}; }}
QFrame#tile {{ background: {PANEL}; border: 1px solid {BORDER};
    border-radius: 14px; }}
QFrame#tile:hover {{ border: 1px solid {ACCENT_HOVER}; background: {PANEL_HOVER}; }}
QFrame#tile:focus {{ border: 2px solid {FOCUS}; }}
QFrame#statCard {{ background: {PANEL}; border: 1px solid {BORDER};
    border-radius: 14px; }}
QFrame#statCard:hover {{ border: 1px solid {ACCENT_HOVER}; }}
QFrame#vitalCard, QFrame#goalCard {{ background: {PANEL};
    border: 1px solid {BORDER}; border-radius: 14px; }}
QLabel#vitalLabel, QLabel#goalLabel {{ color: {FG_MUTED}; font-size: 10px;
    letter-spacing: 1.1px; }}
QLabel#vitalValue, QLabel#goalValue {{ color: {FG}; font-size: 24px;
    font-weight: 700; }}
QLabel#vitalUnit, QLabel#goalUnit {{ color: {FG_MUTED}; font-size: 11px; }}
QLabel#vitalBadge {{ color: {ACCENT_DEEP}; font-size: 9px; font-weight: 600;
    letter-spacing: 0.5px; }}
QLabel#goalMeta {{ color: {FG_MUTED}; font-size: 10px; }}
QFrame#divider {{ background: {BORDER}; min-height: 1px; max-height: 1px;
    border: none; }}

/* empty state */
QFrame#emptyCard {{ background: {PANEL}; border: 1px solid {BORDER};
    border-radius: 20px; }}
QLabel#emptyGlyph {{ font-size: 40px; color: {ACCENT}; }}
QLabel#emptyTitle {{ font-size: 21px; font-weight: 700; color: {FG}; }}
QFrame#emptyChart {{ background: transparent; border: none; }}

/* info / error banners */
QFrame#infoBanner {{ background: {INFO_BG}; border: 1px solid {INFO_BORDER};
    border-radius: 12px; }}
QLabel#infoIcon {{ color: {ACCENT_DEEP}; font-weight: 700; font-size: 14px; }}
QLabel#infoText {{ color: {INFO_FG}; font-size: 12px; }}
QFrame#errorBanner {{ background: {ERROR_BG}; border: 1px solid {ERROR_BORDER};
    border-radius: 12px; }}
QLabel#errorIcon {{ color: {ERROR_TEXT}; font-weight: 700; font-size: 12px;
    letter-spacing: 0.6px; }}
QLabel#errorText {{ color: {FG}; font-size: 12px; }}
QPushButton#errorDismiss {{ background: transparent; color: {ERROR_TEXT};
    border: 1px solid {ERROR_BORDER}; border-radius: 7px; padding: 4px 10px; }}
QPushButton#errorDismiss:hover {{ background: {PANEL_HOVER}; }}
QPushButton#errorDismiss:focus {{ border: 1px solid {FOCUS}; }}

/* buttons */
QPushButton {{ background: {PANEL}; color: {FG}; font-size: 13px;
    border: 1px solid {BORDER}; border-radius: 10px; padding: 8px 14px; }}
QPushButton:hover {{ background: {PANEL_HOVER}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {FG_MUTED}; background: {BG}; }}
QPushButton:focus {{ border: 2px solid {FOCUS}; }}

QPushButton#primary {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
    stop:0 {ACCENT_HOVER}, stop:1 {ACCENT}); color: {ON_ACCENT};
    border: none; font-weight: 600; font-size: 13px; padding: 9px 20px;
    border-radius: 19px; }}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#primary:pressed {{ background: {ACCENT}; padding-top: 10px;
    padding-bottom: 8px; }}
QPushButton#primary:disabled {{ background: {PANEL_HOVER};
    border-color: {BORDER}; color: {FG_MUTED}; }}
QPushButton#primary:focus {{ border: 2px solid {FOCUS}; }}

/* settings */
QLabel#pageTitle {{ font-size: 34px; font-weight: 700; color: {FG}; }}
QFrame#settingsCard {{ background: {PANEL}; border: 1px solid {BORDER};
    border-radius: 18px; }}
QLabel#settingsHead {{ font-size: 16px; font-weight: 600; color: {FG}; }}
QLabel#settingsNote {{ font-size: 13px; color: {FG_MUTED}; }}
QLabel#settingsStep {{ font-size: 13px; color: {FG}; padding: 2px 0; }}
QLabel#settingValue {{ font-size: 13px; color: {FG}; font-weight: 600; }}
QFrame#settingsCard QPushButton, QFrame#settingsCard QToolButton {{
    background: {PANEL_HOVER}; color: {FG}; border: none; border-radius: 17px;
    padding: 8px 18px; font-size: 13px; font-weight: 600; }}
QFrame#settingsCard QPushButton:hover, QFrame#settingsCard QToolButton:hover {{
    background: {BORDER}; }}
QFrame#settingsCard QPushButton[active="true"] {{ background: {HEART};
    color: #FFFFFF; }}
QFrame#settingsCard QToolButton::menu-indicator {{ image: none; width: 0; }}

QPushButton#backBtn {{ background: transparent; border: 1px solid transparent;
    color: {FG}; padding: 6px 12px; text-align: left; border-radius: 9px;
    font-size: 14px; }}
QPushButton#backBtn:hover {{ background: {PANEL_HOVER}; }}
QPushButton#backBtn:focus {{ border: 1px solid {FOCUS}; }}

QPushButton#linkBtn {{ background: transparent; border: 1px solid transparent;
    color: {ACCENT_DEEP}; padding: 4px 4px; text-align: left; font-weight: 600; }}
QPushButton#linkBtn:hover {{ color: {ACCENT_DEEP_HOVER}; }}
QPushButton#linkBtn:focus {{ border: 1px solid {FOCUS}; }}

"""

    if not native:
        qss += f"""
/* menus and dropdowns: AppKit draws these on macOS */
QMenu {{ background: {glass}; border: 1px solid {BORDER}; padding: 7px;
    border-radius: 10px; }}
QMenu::item {{ padding: 7px 24px 7px 14px; border-radius: 7px; color: {FG}; }}
QMenu::item:selected {{ background: {PANEL_HOVER}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}

QComboBox {{ background: {PANEL}; color: {FG}; border: 1px solid {BORDER};
    border-radius: 10px; padding: 6px 9px; min-height: 20px; }}
QComboBox:focus {{ border: 2px solid {FOCUS}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {glass}; color: {FG};
    selection-background-color: {PANEL_HOVER}; border: 1px solid {BORDER};
    outline: 0; padding: 6px; border-radius: 10px; }}

/* the Export menu button gets a custom frame only off macOS */
QToolButton#ghost {{ background: transparent; color: {FG};
    border: 1px solid {BORDER}; border-radius: 10px; padding: 7px 13px; }}
QToolButton#ghost:hover {{ background: {PANEL_HOVER}; }}
QToolButton#ghost:focus {{ border: 2px solid {FOCUS}; }}
QToolButton#ghost::menu-indicator {{ image: none; width: 0; }}
"""

    # The From/To date row keeps its custom look on every platform.
    qss += f"""
QDateEdit {{ background: {PANEL}; color: {FG}; font-size: 13px;
    border: 1px solid {BORDER}; border-radius: 9px; padding: 6px 9px; }}
QDateEdit:focus {{ border: 2px solid {FOCUS}; }}
QDateEdit::drop-down {{ border: none; width: 20px; }}

/* scrolling */
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px;
    min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {FG_MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 4px;
    min-width: 24px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QToolTip {{ background: {glass}; color: {FG}; font-size: 12px;
    border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 11px; }}

/* progress */
QProgressBar {{ background: {PANEL_HOVER}; border: none; border-radius: 2px;
    min-height: 4px; max-height: 4px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}
"""
    return qss


#: Final stylesheet for the host platform.
apply("light")
QSS = build_qss()
