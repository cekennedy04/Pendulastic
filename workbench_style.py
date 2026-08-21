"""
workbench_style.py
===================
Light palette + ttk theme + small widget builders for the Pendulastic
Workbench UI (TrialLoadPanel, WorkbenchView). White/bright background with
strong-contrast text and a vivid blue accent, for easier readability than
the original dark theme.

See docs/superpowers/specs/2026-08-04-workbench-viewer-style-and-csv-export-design.md.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

PALETTE = {
    "BG":      "#F4F6F9",
    "SURFACE": "#FFFFFF",
    "PANEL":   "#F5F8FC",
    "BTN":     "#DCEAFE",
    "BTN_ACT": "#2563EB",
    "FG":      "#0F172A",
    "FG2":     "#475569",
    "FG3":     "#64748B",
    "BORDER":  "#CBD5E1",
    "MONO":    "Consolas",
}

FONT_TITLE = ("Segoe UI", 14, "bold")
FONT_SECTION = ("Segoe UI", 8, "bold")
FONT_BODY = ("Segoe UI", 9)
FONT_SMALL = ("Segoe UI", 7)

FONT_TILE_TITLE = ("Segoe UI", 11, "bold")
FONT_TILE_SUB = ("Segoe UI", 8)
FONT_HERO_TITLE = ("Segoe UI", 14, "bold")
FONT_HERO_SUB = ("Segoe UI", 9)


# ttk style names owned by this module. Every one is prefixed "Workbench."
# so that applying the theme only affects widgets that explicitly opt in via
# style=; no default style name (TScale/TScrollbar/TMenubutton/Treeview/...)
# is touched, and the root's base ttk theme is left alone. That is what makes
# apply_ttk_theme() safe to call from pendulastic_app.py, whose other panels
# (ModeSelectView/UploadMetaView/AcquisitionPanel/PostProcessingPanel) use
# ttk.Combobox and ttk.Separator and must keep their native look.
STYLE_TREEVIEW = "Workbench.Treeview"
STYLE_SCALE = "Workbench.Horizontal.TScale"
STYLE_SCROLLBAR = "Workbench.Vertical.TScrollbar"
STYLE_MENUBUTTON = "Workbench.TMenubutton"
STYLE_PANEDWINDOW = "Workbench.TPanedwindow"

# (custom element name, source element name in clam). Borrowing clam's
# drawing elements under private names is what lets the dark colors actually
# render under a native base theme: on Windows the default "vista" theme
# draws Treeview.field and the Treeheading elements with native OS routines
# that silently ignore -fieldbackground/-background, so a plain
# style.configure() left the metrics tables with white column headers and a
# white empty area below the rows. clam's elements honor the options, and
# `element create ... from clam ...` copies them into the *current* theme
# without switching it.
_BORROWED_ELEMENTS = (
    ("Workbench.Treeview.field", "Treeview.field"),
    ("Workbench.Treeheading.cell", "Treeheading.cell"),
    ("Workbench.Treeheading.border", "Treeheading.border"),
    ("Workbench.Scale.trough", "Horizontal.Scale.trough"),
    ("Workbench.Scale.slider", "Horizontal.Scale.slider"),
    ("Workbench.Scrollbar.trough", "Vertical.Scrollbar.trough"),
    ("Workbench.Scrollbar.thumb", "Vertical.Scrollbar.thumb"),
    ("Workbench.Scrollbar.uparrow", "Vertical.Scrollbar.uparrow"),
    ("Workbench.Scrollbar.downarrow", "Vertical.Scrollbar.downarrow"),
    ("Workbench.Menubutton.border", "Menubutton.border"),
    ("Workbench.Menubutton.indicator", "Menubutton.indicator"),
)


def _borrow_clam_elements(style: ttk.Style) -> None:
    """Copy the clam elements listed in _BORROWED_ELEMENTS into the root's
    current theme under private names. Idempotent: re-creating an existing
    element raises TclError, which is exactly the "already borrowed in this
    interpreter" case (apply_ttk_theme may be called more than once per
    process, e.g. by pendulastic_app.App and again by a test root)."""
    for name, source in _BORROWED_ELEMENTS:
        try:
            style.element_create(name, "from", "clam", source)
        except tk.TclError:
            pass


def apply_ttk_theme(root: tk.Misc) -> None:
    """Register the dark "Workbench.*" ttk styles on `root`.

    Deliberately does NOT call style.theme_use(): the base ttk theme is
    global to the root, so switching it to clam here would restyle every
    other ttk widget in whatever application is hosting the Workbench
    panels. Only widgets that pass one of the STYLE_* names above are
    affected, so this is safe to call from pendulastic_app.App (which embeds
    TrialLoadPanel, WorkbenchView, and DashboardView alongside panels that
    must not change appearance) as well as from test roots.

    Plain ttk widgets ignore bg=/fg= entirely -- they need explicit
    style.configure(...), the same mechanism pendulastic_viewer.py's
    _HistoryWindow already uses for its "Dash.Treeview" style."""
    style = ttk.Style(root)
    _borrow_clam_elements(style)

    style.layout(STYLE_PANEDWINDOW, [("Panedwindow.background", {"sticky": ""})])
    style.configure(STYLE_PANEDWINDOW, background=PALETTE["BG"])

    style.layout(STYLE_SCROLLBAR, [
        ("Workbench.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Workbench.Scrollbar.uparrow", {"side": "top", "sticky": ""}),
            ("Workbench.Scrollbar.downarrow", {"side": "bottom", "sticky": ""}),
            ("Workbench.Scrollbar.thumb", {"sticky": "nswe"}),
        ]}),
    ])
    style.configure(STYLE_SCROLLBAR, background=PALETTE["PANEL"],
                    troughcolor=PALETTE["BG"], bordercolor=PALETTE["BORDER"],
                    arrowcolor=PALETTE["FG2"])

    style.layout(STYLE_SCALE, [
        ("Horizontal.Scale.focus", {"sticky": "nswe", "children": [
            ("Horizontal.Scale.padding", {"sticky": "nswe", "children": [
                ("Workbench.Scale.trough", {"sticky": "nswe", "children": [
                    ("Workbench.Scale.slider", {"side": "left", "sticky": ""}),
                ]}),
            ]}),
        ]}),
    ])
    style.configure(STYLE_SCALE, background=PALETTE["BG"],
                    troughcolor=PALETTE["SURFACE"],
                    bordercolor=PALETTE["BORDER"], darkcolor=PALETTE["BTN"],
                    lightcolor=PALETTE["BTN"], gripcount=0)

    style.layout(STYLE_MENUBUTTON, [
        ("Workbench.Menubutton.border", {"sticky": "nswe", "children": [
            ("Menubutton.focus", {"sticky": "nswe", "children": [
                ("Workbench.Menubutton.indicator", {"side": "right", "sticky": ""}),
                ("Menubutton.padding", {"sticky": "we", "children": [
                    ("Menubutton.label", {"side": "left", "sticky": ""}),
                ]}),
            ]}),
        ]}),
    ])
    style.configure(STYLE_MENUBUTTON, background=PALETTE["BTN"],
                    foreground=PALETTE["FG"], arrowcolor=PALETTE["FG"],
                    bordercolor=PALETTE["BORDER"], darkcolor=PALETTE["BTN"],
                    lightcolor=PALETTE["BTN"], relief="flat", font=FONT_BODY)
    style.map(STYLE_MENUBUTTON,
              background=[("active", PALETTE["BTN_ACT"])],
              foreground=[("active", "#FFFFFF")])

    style.layout(STYLE_TREEVIEW, [
        ("Workbench.Treeview.field", {"sticky": "nswe", "border": "1", "children": [
            ("Treeview.padding", {"sticky": "nswe", "children": [
                ("Treeview.treearea", {"sticky": "nswe"}),
            ]}),
        ]}),
    ])
    style.layout(STYLE_TREEVIEW + ".Heading", [
        ("Workbench.Treeheading.cell", {"sticky": "nswe"}),
        ("Workbench.Treeheading.border", {"sticky": "nswe", "children": [
            ("Treeheading.padding", {"sticky": "nswe", "children": [
                ("Treeheading.image", {"side": "right", "sticky": ""}),
                ("Treeheading.text", {"sticky": "we"}),
            ]}),
        ]}),
    ])
    style.configure(STYLE_TREEVIEW, background=PALETTE["SURFACE"],
                    foreground=PALETTE["FG"], fieldbackground=PALETTE["SURFACE"],
                    bordercolor=PALETTE["BORDER"], lightcolor=PALETTE["SURFACE"],
                    darkcolor=PALETTE["SURFACE"], rowheight=22, font=FONT_BODY)
    style.configure(STYLE_TREEVIEW + ".Heading", background=PALETTE["PANEL"],
                    foreground=PALETTE["FG2"], bordercolor=PALETTE["BORDER"],
                    lightcolor=PALETTE["PANEL"], darkcolor=PALETTE["PANEL"],
                    relief="flat", font=FONT_SECTION)
    style.map(STYLE_TREEVIEW, background=[("selected", PALETTE["BTN"])],
              foreground=[("selected", PALETTE["FG"])])
    style.map(STYLE_TREEVIEW + ".Heading",
              background=[("active", PALETTE["PANEL"])])


def card_frame(parent: tk.Misc, title: str = "") -> tk.Frame:
    """A padded, panel-colored card frame with an optional bold section
    label packed at its top. Caller packs/grids the returned frame into
    its own parent, then packs content into it directly."""
    card = tk.Frame(parent, bg=PALETTE["PANEL"], padx=10, pady=8,
                    highlightbackground=PALETTE["BORDER"], highlightthickness=1)
    if title:
        tk.Label(card, text=title, bg=PALETTE["PANEL"], fg=PALETTE["FG3"],
                 font=FONT_SECTION).pack(anchor="w", pady=(0, 6))
    return card


def primary_button(parent: tk.Misc, text: str, command) -> tk.Button:
    return tk.Button(parent, text=text, command=command,
                     bg=PALETTE["BTN_ACT"], fg="#FFFFFF",
                     activebackground="#1A5080", activeforeground="#FFFFFF",
                     relief="flat", bd=0, padx=10, pady=4,
                     font=FONT_BODY, cursor="hand2")


def secondary_button(parent: tk.Misc, text: str, command) -> tk.Button:
    return tk.Button(parent, text=text, command=command,
                     bg=PALETTE["BTN"], fg=PALETTE["FG"],
                     activebackground=PALETTE["BTN_ACT"], activeforeground="#FFFFFF",
                     relief="flat", bd=0, padx=10, pady=4,
                     font=FONT_BODY, cursor="hand2")


def _round_rect_points(x1: float, y1: float, x2: float, y2: float, r: float) -> list:
    """Point list for create_polygon(..., smooth=True) tracing a rounded
    rectangle. Smoothing interpolates a curve through each corner pair, so
    listing the two points flanking a corner (rather than an arc) is enough
    to read as rounded once Tk splines it."""
    return [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]


class Tile(tk.Canvas):
    """A rounded, elevated action card: icon + title + subtitle, with hover
    feedback and a click command. Plain ttk/tk buttons can't do rounded
    corners or a true hover repaint, so this draws itself on a Canvas
    instead — used for the ModeSelectView landing tiles."""

    _ICON_SIZE = 9

    def __init__(self, parent: tk.Misc, title: str, subtitle: str, command,
                 icon: str = "record", width: int = 220, height: int = 96,
                 primary: bool = False) -> None:
        super().__init__(parent, width=width, height=height, bg=PALETTE["BG"],
                          highlightthickness=0, cursor="hand2")
        self._title = title
        self._subtitle = subtitle
        self._command = command
        self._icon = icon
        self._tw, self._th = width, height
        self._primary = primary
        self._hover = False
        self._redraw()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)

    def _palette(self):
        if self._primary:
            fill = "#1D4ED8" if self._hover else PALETTE["BTN_ACT"]
            return dict(fill=fill, border=fill, title="#FFFFFF",
                        sub="#EAF2FF", icon="#FFFFFF", border_w=2)
        return dict(
            fill=PALETTE["SURFACE"],
            border=PALETTE["BTN_ACT"] if self._hover else PALETTE["BORDER"],
            title=PALETTE["FG"], sub=PALETTE["FG3"], icon=PALETTE["BTN_ACT"],
            border_w=2 if self._hover else 1,
        )

    def _redraw(self) -> None:
        self.delete("all")
        c = self._palette()
        pts = _round_rect_points(2, 2, self._tw - 2, self._th - 2, 14)
        self.create_polygon(pts, smooth=True, fill=c["fill"],
                             outline=c["border"], width=c["border_w"])
        icon_cx = 34 if self._primary else 30
        self._draw_icon(icon_cx, self._th / 2, c["icon"])
        text_x = icon_cx + 26
        title_font = FONT_HERO_TITLE if self._primary else FONT_TILE_TITLE
        sub_font = FONT_HERO_SUB if self._primary else FONT_TILE_SUB
        self.create_text(text_x, self._th / 2 - 11, text=self._title,
                          anchor="w", fill=c["title"], font=title_font)
        self.create_text(text_x, self._th / 2 + 13, text=self._subtitle,
                          anchor="w", fill=c["sub"], font=sub_font,
                          width=self._tw - text_x - 14)

    def _draw_icon(self, cx: float, cy: float, color: str) -> None:
        s = self._ICON_SIZE
        kind = self._icon
        if kind == "record":
            self.create_oval(cx - s, cy - s, cx + s, cy + s, outline=color, width=2)
            self.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill=color, outline="")
        elif kind == "upload":
            self.create_line(cx, cy + s, cx, cy - s + 2, fill=color, width=2)
            self.create_line(cx - 6, cy - s + 8, cx, cy - s + 2, cx + 6, cy - s + 8,
                              fill=color, width=2, joinstyle="round", capstyle="round")
            self.create_line(cx - s, cy + s + 3, cx + s, cy + s + 3, fill=color, width=2)
        elif kind == "compare":
            self.create_rectangle(cx - s, cy - 6, cx - 2, cy + 6, outline=color, width=2)
            self.create_rectangle(cx + 2, cy - 6, cx + s, cy + 6, outline=color, width=2)
            self.create_line(cx - 2, cy, cx + 2, cy, fill=color, width=2)
        elif kind == "chart":
            for bx, h in ((cx - 8, 8), (cx, 15), (cx + 8, 11)):
                self.create_rectangle(bx - 3, cy + s - h, bx + 3, cy + s,
                                       fill=color, outline="")
        elif kind == "checklist":
            self.create_rectangle(cx - s, cy - s, cx + s, cy + s, outline=color, width=2)
            self.create_line(cx - 4, cy, cx - 1, cy + 4, cx + 5, cy - 5, fill=color,
                              width=2, joinstyle="round", capstyle="round")

    def _on_enter(self, _evt=None) -> None:
        self._hover = True
        self._redraw()

    def _on_leave(self, _evt=None) -> None:
        self._hover = False
        self._redraw()

    def _on_click(self, _evt=None) -> None:
        if self._command:
            self._command()


def tile(parent: tk.Misc, title: str, subtitle: str, command, icon: str = "record",
         width: int = 220, height: int = 96, primary: bool = False) -> Tile:
    return Tile(parent, title, subtitle, command, icon=icon,
                width=width, height=height, primary=primary)


def brand_mark(parent: tk.Misc, size: int = 52) -> tk.Canvas:
    """A small circular pendulum glyph — pivot dot, swing line, weighted
    bob — as the app's monogram next to the wordmark on the landing screen."""
    c = tk.Canvas(parent, width=size, height=size, bg=PALETTE["BG"],
                  highlightthickness=0)
    accent = PALETTE["BTN_ACT"]
    pad = size * 0.08
    c.create_oval(pad, pad, size - pad, size - pad, outline=accent, width=2.5)
    cx, top = size / 2, pad + size * 0.14
    bob_x, bob_y = cx + size * 0.2, size - pad - size * 0.2
    c.create_line(cx, top, bob_x, bob_y, fill=accent, width=2.5,
                  capstyle="round")
    bob_r = size * 0.09
    c.create_oval(bob_x - bob_r, bob_y - bob_r, bob_x + bob_r, bob_y + bob_r,
                  fill=accent, outline="")
    pivot_r = size * 0.045
    c.create_oval(cx - pivot_r, top - pivot_r, cx + pivot_r, top + pivot_r,
                  fill=accent, outline="")
    return c
