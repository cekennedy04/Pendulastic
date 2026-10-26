"""Shared figure style for the Pendulastic decks and the publication set.
Deck mode (default): PNG at 200 dpi next to the calling script.
PUB=1: also writes <name>.png at 300 dpi and <name>.pdf (vector, fonts embedded as Type 42)
       into PUB_DIR, with bold panel letters on multi-panel figures.
"""
import os, sys, string
import matplotlib.pyplot as plt

TEAL = "#028090"; SEA = "#00A896"; NAVY = "#21295C"; CORAL = "#E4572E"; GREY = "#8894a0"
INK = "#1E2733"; PANEL = "#eef4f6"; GOLD = "#E9B44C"
PUB = os.environ.get("PUB") == "1"
PUB_DIR = os.environ.get("PUB_DIR", r"C:\Users\cladi\Pendulastic\figures\publication_2026-09")

def setup():
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 10.5 if PUB else 11.5,
        "axes.edgecolor": "#c9d2da", "axes.titleweight": "bold", "axes.titlecolor": NAVY,
        "axes.titlesize": 11.5 if PUB else 13, "axes.labelsize": 10.5 if PUB else 11.5,
        "legend.fontsize": 9.5 if PUB else 10, "xtick.labelsize": 9.5 if PUB else 10.5, "ytick.labelsize": 9.5 if PUB else 10.5,
        "figure.titlesize": 13 if PUB else 14, "figure.titleweight": "bold",
        "pdf.fonttype": 42, "ps.fonttype": 42, "savefig.facecolor": "white",
    })

def style(ax, y=True):
    ax.grid(True, axis="both" if y else "x", color="#eef2f5"); [ax.spines[s].set_visible(False) for s in ("top", "right")]

def note(ax, txt, loc="lower right", fs=None):
    fs = fs or (9 if PUB else 9.5)
    x, ha = (0.985, "right") if "right" in loc else (0.015, "left"); yy, va = (0.05, "bottom") if "lower" in loc else (0.95, "top")
    ax.text(x, yy, txt, transform=ax.transAxes, ha=ha, va=va, fontsize=fs, color=NAVY, bbox=dict(boxstyle="round,pad=0.4", fc=PANEL, ec="#c9d2da"))

def letters(fig):
    axes = [a for a in fig.axes if a.get_visible() and a.get_label() != "<colorbar>"]
    if len(axes) < 2: return
    for a, L in zip(axes, string.ascii_lowercase):
        a.text(-0.06, 1.08, L, transform=a.transAxes, fontsize=12, fontweight="bold", color=INK, ha="right", va="bottom")

def save(fig, name, out_dir=None):
    """name with or without .png. Deck PNG always; publication PNG+PDF when PUB=1."""
    name = name[:-4] if name.endswith(".png") else name
    fig.tight_layout()
    if PUB:
        os.makedirs(PUB_DIR, exist_ok=True); letters(fig)
        fig.savefig(os.path.join(PUB_DIR, name + ".png"), dpi=300, bbox_inches="tight")
        fig.savefig(os.path.join(PUB_DIR, name + ".pdf"), bbox_inches="tight")
    else:
        out_dir = out_dir or os.path.dirname(os.path.abspath(sys.argv[0]))
        fig.savefig(os.path.join(out_dir, name + ".png"), dpi=200, bbox_inches="tight")
    plt.close(fig); print("  wrote", name, "(pub)" if PUB else "")
