"""
workbench_style.py
===================
Dark palette + ttk theme + small widget builders for the Pendulastic
Workbench UI (TrialLoadPanel, WorkbenchView). Palette values are copied
from pendulastic_viewer.py's _C dict (not imported -- pendulastic_workbench.py
must not pull in pendulastic_viewer.py's cv2/mediapipe/ultralytics
dependency chain just for six color strings; pendulastic_viewer.py itself
is not modified by this module).

See docs/superpowers/specs/2026-08-04-workbench-viewer-style-and-csv-export-design.md.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

PALETTE = {
    "BG":      "#0B1928",
    "SURFACE": "#112040",
    "PANEL":   "#0D2238",
    "BTN":     "#1A3A5C",
    "BTN_ACT": "#2A6090",
    "FG":      "#C8E0F5",
    "FG2":     "#5A8AB0",
    "FG3":     "#2E5070",
    "BORDER":  "#1C3A5E",
    "MONO":    "Consolas",
}

FONT_TITLE = ("Segoe UI", 14, "bold")
FONT_SECTION = ("Segoe UI", 8, "bold")
FONT_BODY = ("Segoe UI", 9)
FONT_SMALL = ("Segoe UI", 7)


def apply_ttk_theme(root: tk.Misc) -> None:
    """Configure a clam-based ttk.Style so Scale/OptionMenu/PanedWindow/
    Scrollbar/Treeview widgets pick up the dark palette. Plain ttk widgets
    ignore bg=/fg= entirely -- they need explicit style.configure(...),
    the same mechanism pendulastic_viewer.py's _HistoryWindow already uses
    for its "Dash.Treeview" style."""
    style = ttk.Style(root)
    style.theme_use("clam")

    style.configure("TPanedwindow", background=PALETTE["BG"])
    style.configure("TScrollbar", background=PALETTE["PANEL"],
                    troughcolor=PALETTE["BG"], bordercolor=PALETTE["BORDER"],
                    arrowcolor=PALETTE["FG2"])
    style.configure("Horizontal.TScale", background=PALETTE["BG"],
                    troughcolor=PALETTE["SURFACE"])
    style.configure("TMenubutton", background=PALETTE["BTN"],
                    foreground=PALETTE["FG"], font=FONT_BODY)

    style.configure("Workbench.Treeview", background=PALETTE["SURFACE"],
                    foreground=PALETTE["FG"], fieldbackground=PALETTE["SURFACE"],
                    rowheight=22, font=FONT_BODY)
    style.configure("Workbench.Treeview.Heading", background=PALETTE["PANEL"],
                    foreground=PALETTE["FG2"], font=FONT_SECTION)
    style.map("Workbench.Treeview", background=[("selected", PALETTE["BTN"])],
              foreground=[("selected", PALETTE["FG"])])


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
