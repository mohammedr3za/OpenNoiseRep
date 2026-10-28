"""Light, airy theme for the NoiseREP window (Anthropic-inspired palette).

Token names are part of the GUI's minimal API and stay stable for other
modules (``BG``, ``PANEL``, ``BORDER``, ``FG``, ``FG_MUTED``, ``ACCENT``,
``ACCENT_HOVER``, ``ON_ACCENT``, ``SERIES``, ``QSS``).

The palette is warm ivory + white cards, slate text, and one Book Cloth
terracotta accent.  ``ACCENT`` is used for charts and large numerals;
``ACCENT_DEEP`` is a slightly deeper shade of the same hue used wherever small
text sits on the accent so that it clears WCAG AA.  ``FOCUS`` is reserved for
keyboard focus rings so focus is never conveyed by colour alone.
"""
from __future__ import annotations

BG = "#FAFAF7"            # Ivory Light
PANEL = "#FFFFFF"         # White
PANEL_HOVER = "#F0F0EB"   # Ivory Medium
BORDER = "#E8E4DF"        # Ivory Dark (hairline)
FG = "#191919"            # Slate Dark
FG_MUTED = "#666663"      # Cloud Dark (>=5:1 on white / ivory)
ACCENT = "#CC785C"        # Book Cloth
ACCENT_HOVER = "#D4A27F"  # Kraft
ON_ACCENT = "#FFFFFF"
ERROR = "#BF4D43"
FOCUS = "#6A9BCC"
SERIES = ["#CC785C", "#D4A27F", "#6A9BCC", "#91918D"]

# Supporting shades (same hue families, tuned for text/AA and soft banners).
ACCENT_DEEP = "#A85B44"        # AA (4.6:1) with white, for small accent text
ACCENT_DEEP_HOVER = "#96503B"
ACCENT_FILL = "rgba(204,120,92,0.12)"
ACCENT_LINE = "rgba(204,120,92,0.55)"
INFO_BG = "#EBDBCF"            # Manilla — soft accent info banner
INFO_BORDER = "#E2CDBD"
INFO_FG = "#5A4638"
ERROR_BG = "#FBEDEB"
ERROR_BORDER = "#E9C6C0"
ERROR_TEXT = "#8E3A32"

# Kept for any legacy importer; values now point at the light palette.
PALETTE = {
    "ivory_light": BG, "white": PANEL, "ivory_medium": PANEL_HOVER,
    "ivory_dark": BORDER, "slate_dark": FG, "cloud_dark": FG_MUTED,
    "book_cloth": ACCENT, "kraft": ACCENT_HOVER, "manilla": INFO_BG,
    # legacy names
    "black": FG, "charcoal": FG_MUTED, "gray": BORDER, "pale_blue": FOCUS,
    "beige": ACCENT_HOVER, "taupe": ACCENT_DEEP, "soft_green": ACCENT,
}

FONT_FAMILY = ('"SF Pro Text", "Helvetica Neue", "Segoe UI", '
               'system-ui, sans-serif')

QSS = f"""
* {{ font-family: {FONT_FAMILY}; }}

QWidget {{ background: transparent; color: {FG}; font-size: 13px; }}
QMainWindow, QWidget#root {{ background: {BG}; }}

QLabel {{ background: transparent; }}
QLabel#muted {{ color: {FG_MUTED}; font-size: 12px; }}
QLabel#hint {{ color: {FG_MUTED}; font-size: 12px; }}
QLabel#status {{ color: {FG_MUTED}; font-size: 12px; }}
QLabel#sectionLabel {{ color: {FG_MUTED}; font-size: 10px; letter-spacing: 1.4px; }}
QLabel#rangeLabel {{ color: {FG_MUTED}; font-size: 12px; }}
QLabel#shortcuts {{ color: {FG_MUTED}; font-size: 10px; }}

/* app bar */
QFrame#appbar {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
QLabel#appName {{ font-size: 17px; font-weight: 700; color: {FG}; }}
QLabel#appTag {{ font-size: 10px; font-weight: 600; color: {FG_MUTED};
    letter-spacing: 1.4px; }}
QLabel#brandDot {{ font-size: 18px; color: {ACCENT}; }}
QLabel#devicePill {{ color: {FG_MUTED}; font-size: 11px; }}
QFrame#devicePill {{ background: {PANEL_HOVER}; border: 1px solid {BORDER};
    border-radius: 11px; }}

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
QPushButton {{ background: {PANEL}; color: {FG}; border: 1px solid {BORDER};
    border-radius: 10px; padding: 8px 14px; }}
QPushButton:hover {{ background: {PANEL_HOVER}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {FG_MUTED}; background: {BG}; }}
QPushButton:focus {{ border: 2px solid {FOCUS}; }}

QPushButton#primary {{ background: {ACCENT_DEEP}; color: {ON_ACCENT};
    border: 2px solid {ACCENT_DEEP}; font-weight: 600; padding: 9px 18px;
    border-radius: 11px; }}
QPushButton#primary:hover {{ background: {ACCENT_DEEP_HOVER};
    border-color: {ACCENT_DEEP_HOVER}; }}
QPushButton#primary:pressed {{ background: {ACCENT_DEEP_HOVER}; }}
QPushButton#primary:disabled {{ background: {PANEL_HOVER};
    border-color: {BORDER}; color: {FG_MUTED}; }}
QPushButton#primary:focus {{ border: 2px solid {FOCUS}; }}

QToolButton#ghost {{ background: transparent; color: {FG}; border: 1px solid {BORDER};
    border-radius: 10px; padding: 7px 13px; }}
QToolButton#ghost:hover {{ background: {PANEL_HOVER}; }}
QToolButton#ghost:focus {{ border: 2px solid {FOCUS}; }}
QToolButton#ghost::menu-indicator {{ image: none; width: 0; }}

QPushButton#backBtn {{ background: transparent; border: 1px solid transparent;
    color: {FG}; padding: 6px 12px; text-align: left; border-radius: 9px;
    font-size: 14px; }}
QPushButton#backBtn:hover {{ background: {PANEL_HOVER}; }}
QPushButton#backBtn:focus {{ border: 1px solid {FOCUS}; }}

QPushButton#linkBtn {{ background: transparent; border: 1px solid transparent;
    color: {ACCENT_DEEP}; padding: 4px 4px; text-align: left; font-weight: 600; }}
QPushButton#linkBtn:hover {{ color: {ACCENT_DEEP_HOVER}; }}
QPushButton#linkBtn:focus {{ border: 1px solid {FOCUS}; }}

/* inputs */
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; padding: 5px;
    border-radius: 10px; }}
QMenu::item {{ padding: 7px 24px 7px 14px; border-radius: 7px; color: {FG}; }}
QMenu::item:selected {{ background: {PANEL_HOVER}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}

QDateEdit {{ background: {PANEL}; color: {FG}; border: 1px solid {BORDER};
    border-radius: 9px; padding: 6px 9px; }}
QDateEdit:focus {{ border: 2px solid {FOCUS}; }}
QDateEdit::drop-down {{ border: none; width: 20px; }}
QComboBox {{ background: {PANEL}; color: {FG}; border: 1px solid {BORDER};
    border-radius: 10px; padding: 6px 9px; min-height: 20px; }}
QComboBox:focus {{ border: 2px solid {FOCUS}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; color: {FG};
    selection-background-color: {PANEL_HOVER}; border: 1px solid {BORDER};
    outline: 0; padding: 3px; }}

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
QToolTip {{ background: {PANEL}; color: {FG}; border: 1px solid {BORDER};
    padding: 5px 7px; }}

/* progress */
QProgressBar {{ background: {PANEL_HOVER}; border: none; border-radius: 2px;
    min-height: 4px; max-height: 4px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}
"""
