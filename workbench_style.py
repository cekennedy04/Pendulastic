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
