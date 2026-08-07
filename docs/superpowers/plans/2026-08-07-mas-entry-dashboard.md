# MAS Score Entry + Live Validation Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a clinician enter a MAS score directly in `pendulastic_app.py`, append it to `mas_scores.csv`, and see the PT-score-vs-MAS validation dashboard update live, with an on-demand Export of the stats CSV + figure PNG.

**Architecture:** `mas_validation.py` gains a pure `append_mas_score()` CSV-append function and splits its existing figure builder into a pure `build_validation_figure()` + a thin `save_validation_figure()` I/O wrapper, plus a fix so importing it doesn't clobber the app's interactive matplotlib backend. `pendulastic_app.py` gains a new `MasEntryPanel(tk.Frame)` — entry form on top, a `FigureCanvasTkAgg`-embedded dashboard below — built up incrementally: skeleton + navigation, then the refresh/dashboard pipeline, then Save, then Export.

**Tech Stack:** Python, Tkinter, matplotlib (`FigureCanvasTkAgg`), pytest.

## Global Constraints

- Do not change how PT scores or MAS predictions are computed, and do not modify `pendulastic_pt_score.py` or the stats formulas in `mas_validation.compute_validation_stats`.
- No authentication/access control on the entry form.
- `mas_scores.csv` is append-only from this UI — no edit/delete of existing rows.
- The entry form's columns must match the **live** `mas_scores.csv` header exactly: `participant, leg, condition, diagnosis, mas_grade, assessed_by, assessed_date` (this already differs from the original 2026-08-06 spec, which had `notes` instead of `diagnosis` — target the live schema).
- `mas_validation.py`'s module-level `matplotlib.use("Agg")` must move behind `if __name__ == "__main__":` — importing it unconditionally from `pendulastic_app.py` (which needs the interactive `TkAgg` backend) would silently break every other embedded plot in the app.
- Follow existing file conventions: `ws.PALETTE`/`ws.FONT_BODY` styling, `tk.Entry`/`Radiobutton`/`ttk.Combobox` widgets, `ws.primary_button`/`ws.secondary_button` helpers, guarded imports with an `_XXX_AVAIL` flag (matching `_WORKBENCH_AVAIL`/`_PT_AVAIL`), and `tests/test_app.py`'s existing convention of testing panels only through a real `App()` instance (this file has no direct-panel-instantiation fixture — don't add one).

---

### Task 1: `append_mas_score()` in `mas_validation.py`

**Files:**
- Modify: `mas_validation.py:144-151` (right after `load_mas_scores`)
- Test: `tests/test_mas_validation.py`

**Interfaces:**
- Consumes: existing `_valid_grade(grade: str) -> bool`, `MAS_ORDER` (module-level list), `MAS_CSV` (module-level path constant) — all already defined in `mas_validation.py`.
- Produces: `append_mas_score(row: dict, csv_path=MAS_CSV) -> None`. Raises `ValueError` on an invalid `mas_grade`, otherwise appends one row to the CSV at `csv_path` using that file's own current header (via `csv.DictReader(f).fieldnames`), ignoring any keys in `row` not present in the header and writing `""` for any header column missing from `row`. Later tasks call this as `_mas_validation.append_mas_score(row)` (default `csv_path`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_mas_validation.py`, after the `main()` tests at the end of the file:

```python
# ── append_mas_score ─────────────────────────────────────────────────────────

def test_append_mas_score_writes_using_existing_header_order(tmp_path):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n")
    mv.append_mas_score(
        {"participant": "20", "leg": "left", "condition": "pre",
         "diagnosis": "multiple sclerosis", "mas_grade": "1+",
         "assessed_by": "VL", "assessed_date": "2026-08-07"},
        csv_path=str(csv_path))
    lines = csv_path.read_text().splitlines()
    assert lines[1] == "20,left,pre,multiple sclerosis,1+,VL,2026-08-07"


def test_append_mas_score_rejects_invalid_grade(tmp_path):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n")
    with pytest.raises(ValueError, match="invalid mas_grade"):
        mv.append_mas_score(
            {"participant": "20", "leg": "left", "condition": "pre",
             "diagnosis": "", "mas_grade": "5", "assessed_by": "", "assessed_date": ""},
            csv_path=str(csv_path))
    assert csv_path.read_text().splitlines() == [
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date"]


def test_append_mas_score_round_trips_through_load_mas_scores(tmp_path):
    csv_path = tmp_path / "mas_scores.csv"
    csv_path.write_text(
        "participant,leg,condition,diagnosis,mas_grade,assessed_by,assessed_date\n")
    mv.append_mas_score(
        {"participant": "20", "leg": "left", "condition": "pre",
         "diagnosis": "multiple sclerosis", "mas_grade": "1+",
         "assessed_by": "VL", "assessed_date": "2026-08-07"},
        csv_path=str(csv_path))
    rows = mv.load_mas_scores(str(csv_path))
    assert len(rows) == 1
    assert rows[0]["participant"] == "20"
    assert rows[0]["mas_grade"] == "1+"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mas_validation.py -k append_mas_score -v`
Expected: FAIL with `AttributeError: module 'mas_validation' has no attribute 'append_mas_score'`

- [ ] **Step 3: Implement `append_mas_score`**

In `mas_validation.py`, insert immediately after `load_mas_scores` (currently lines 144-151, right before the `_tokenize_condition` function):

```python
def append_mas_score(row: dict, csv_path=MAS_CSV) -> None:
    """Appends one clinician MAS assessment to csv_path. Raises ValueError
    (no write attempted) if row["mas_grade"] isn't one of MAS_ORDER. Reads
    the file's own current header rather than assuming a fixed column set,
    so this stays correct even if mas_scores.csv's schema drifts again the
    way it already has once (see module docstring)."""
    grade = row.get("mas_grade", "")
    if not _valid_grade(grade):
        raise ValueError(f"invalid mas_grade {grade!r} (must be one of {MAS_ORDER})")
    with open(csv_path, newline="", encoding="utf-8") as f:
        fieldnames = csv.DictReader(f).fieldnames
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writerow(row)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mas_validation.py -k append_mas_score -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full mas_validation test suite to confirm no regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mas_validation.py -v`
Expected: PASS (all tests, including the 3 new ones)

- [ ] **Step 6: Commit**

```bash
git add mas_validation.py tests/test_mas_validation.py
git commit -m "feat: add append_mas_score() to mas_validation.py"
```

---

### Task 2: Split figure builder and fix the matplotlib backend guard

**Files:**
- Modify: `mas_validation.py:41-43` (backend import), `mas_validation.py:202-267` (`make_validation_figure`), `mas_validation.py:325` (`main()`'s call site)
- Test: `tests/test_mas_validation.py`

**Interfaces:**
- Produces: `build_validation_figure(pairs: list, stats: dict) -> matplotlib.figure.Figure` (pure — no I/O, does not call `plt.close()`) and `save_validation_figure(pairs: list, stats: dict, out_path: str) -> None` (calls `build_validation_figure`, then `fig.savefig(...)`, `plt.close(fig)`, prints `"-> {out_path}"`). `make_validation_figure` is removed entirely — nothing else in the codebase calls it. Task 4 (`MasEntryPanel.refresh`) calls `build_validation_figure`; Task 6 (`MasEntryPanel._on_export_clicked`) calls `save_validation_figure`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_mas_validation.py`, after the `compute_validation_stats` test block (after `test_roc_auc_computed_when_class_balance_sufficient`):

```python
# ── build_validation_figure / save_validation_figure ────────────────────────

def test_build_validation_figure_returns_figure_with_two_panels_when_roc_omitted():
    pairs = _perfect_agreement_pairs()
    stats = mv.compute_validation_stats(pairs)
    assert stats["roc_auc"] is None
    fig = mv.build_validation_figure(pairs, stats)
    assert len(fig.axes) == 2


def test_build_validation_figure_returns_figure_with_three_panels_when_roc_present():
    pairs = [{"mas_grade": "0", "pt_score": 0.02 + i * 0.01, "predicted_mas": "0"} for i in range(3)] + [
        {"mas_grade": "2", "pt_score": 0.5 + i * 0.01, "predicted_mas": "2"} for i in range(3)
    ]
    stats = mv.compute_validation_stats(pairs)
    assert stats["roc_auc"] is not None
    fig = mv.build_validation_figure(pairs, stats)
    assert len(fig.axes) == 3


def test_save_validation_figure_writes_png(tmp_path):
    pairs = _perfect_agreement_pairs()
    stats = mv.compute_validation_stats(pairs)
    out_path = tmp_path / "fig.png"
    mv.save_validation_figure(pairs, stats, str(out_path))
    assert out_path.exists()
```

Note: there were no pre-existing tests for `make_validation_figure` to "update" (the approved spec assumed some existed) — these three are new coverage for the split.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mas_validation.py -k "build_validation_figure or save_validation_figure" -v`
Expected: FAIL with `AttributeError: module 'mas_validation' has no attribute 'build_validation_figure'`

- [ ] **Step 3: Split `make_validation_figure`**

In `mas_validation.py`, replace the entire `make_validation_figure` function (currently lines 202-267):

```python
def make_validation_figure(pairs, stats, out_path):
    n_panels = 3 if stats["roc_auc"] is not None else 2
    fig, axes = plt.subplots(1, n_panels, figsize=(6 * n_panels, 5.5), facecolor="white")

    # ── Panel 1: PT score distribution by MAS grade ─────────────────────────
    ax = axes[0]
    ax.set_facecolor("#f8f9fa")
    ax.grid(True, color=common.BG_GRID, linestyle="-", linewidth=0.8, axis="y")
    present = [(g, [p["pt_score"] for p in pairs if p["mas_grade"] == g]) for g in MAS_ORDER]
    present = [(g, d) for g, d in present if d]
    if present:
        labels = [g for g, _ in present]
        values = [d for _, d in present]
        bp = ax.boxplot(values, tick_labels=labels, patch_artist=True, showfliers=False)
        for patch, g in zip(bp["boxes"], labels):
            patch.set_facecolor(pt._MAS_COLOR.get(g, "#999999"))
            patch.set_alpha(0.6)
        rng = np.random.RandomState(13)
        for i, (g, d) in enumerate(present, start=1):
            xs = i + rng.uniform(-0.08, 0.08, size=len(d))
            ax.scatter(xs, d, color="#333333", s=18, alpha=0.6, zorder=3)
    ax.set_xlabel("Clinician MAS grade", fontsize=9)
    ax.set_ylabel("PT score (7-parameter)", fontsize=9)
    caveat = " (preliminary -- small n)" if stats["preliminary"] else ""
    rho_txt = (f"rho={stats['spearman_rho']:.2f}, p={stats['spearman_p']:.3f}"
              if stats["spearman_rho"] is not None else "rho=n/a")
    ax.set_title(f"PT score vs MAS grade (n={stats['n']}{caveat})\n{rho_txt}",
                fontsize=10, fontweight="bold")

    # ── Panel 2: agreement heatmap (actual x predicted) ─────────────────────
    ax = axes[1]
    mat = np.zeros((len(MAS_ORDER), len(MAS_ORDER)), dtype=int)
    for p in pairs:
        mat[MAS_RANK[p["mas_grade"]], MAS_RANK[p["predicted_mas"]]] += 1
    ax.imshow(mat, cmap="Blues")
    ax.set_xticks(range(len(MAS_ORDER))); ax.set_xticklabels(MAS_ORDER, fontsize=9)
    ax.set_yticks(range(len(MAS_ORDER))); ax.set_yticklabels(MAS_ORDER, fontsize=9)
    ax.set_xlabel("Predicted MAS (from PT score)", fontsize=9)
    ax.set_ylabel("Actual (clinician) MAS", fontsize=9)
    peak = mat.max() if mat.size else 0
    for i in range(len(MAS_ORDER)):
        for j in range(len(MAS_ORDER)):
            if mat[i, j]:
                ax.text(j, i, str(mat[i, j]), ha="center", va="center",
                       color="white" if peak and mat[i, j] > peak / 2 else "black", fontsize=9)
    kappa_txt = f"weighted kappa={stats['weighted_kappa']:.2f}" if stats["weighted_kappa"] is not None else "kappa=n/a"
    ax.set_title(f"Agreement: predicted vs actual MAS\n{kappa_txt}", fontsize=10, fontweight="bold")

    # ── Panel 3 (optional): ROC, MAS>=1 ("spastic") vs MAS==0 ───────────────
    if stats["roc_auc"] is not None:
        ax = axes[2]
        binary = np.array([0 if p["mas_grade"] == "0" else 1 for p in pairs])
        pt_scores = np.array([p["pt_score"] for p in pairs])
        fpr, tpr, _ = roc_curve(binary, pt_scores)
        ax.plot(fpr, tpr, color=common.COLORS["blue"], linewidth=2)
        ax.plot([0, 1], [0, 1], color="#cccccc", linestyle="--", linewidth=1)
        ax.set_xlabel("False positive rate", fontsize=9)
        ax.set_ylabel("True positive rate", fontsize=9)
        ax.set_title(f"Spastic (MAS>=1) vs not\nAUC={stats['roc_auc']:.2f}", fontsize=10, fontweight="bold")

    fig.suptitle("PT Score vs Clinician MAS -- Concurrent Validity", fontsize=12, y=1.03, color="#333333")
    plt.tight_layout()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    print(f"-> {out_path}")
```

with:

```python
def build_validation_figure(pairs, stats):
    n_panels = 3 if stats["roc_auc"] is not None else 2
    fig, axes = plt.subplots(1, n_panels, figsize=(6 * n_panels, 5.5), facecolor="white")

    # ── Panel 1: PT score distribution by MAS grade ─────────────────────────
    ax = axes[0]
    ax.set_facecolor("#f8f9fa")
    ax.grid(True, color=common.BG_GRID, linestyle="-", linewidth=0.8, axis="y")
    present = [(g, [p["pt_score"] for p in pairs if p["mas_grade"] == g]) for g in MAS_ORDER]
    present = [(g, d) for g, d in present if d]
    if present:
        labels = [g for g, _ in present]
        values = [d for _, d in present]
        bp = ax.boxplot(values, tick_labels=labels, patch_artist=True, showfliers=False)
        for patch, g in zip(bp["boxes"], labels):
            patch.set_facecolor(pt._MAS_COLOR.get(g, "#999999"))
            patch.set_alpha(0.6)
        rng = np.random.RandomState(13)
        for i, (g, d) in enumerate(present, start=1):
            xs = i + rng.uniform(-0.08, 0.08, size=len(d))
            ax.scatter(xs, d, color="#333333", s=18, alpha=0.6, zorder=3)
    ax.set_xlabel("Clinician MAS grade", fontsize=9)
    ax.set_ylabel("PT score (7-parameter)", fontsize=9)
    caveat = " (preliminary -- small n)" if stats["preliminary"] else ""
    rho_txt = (f"rho={stats['spearman_rho']:.2f}, p={stats['spearman_p']:.3f}"
              if stats["spearman_rho"] is not None else "rho=n/a")
    ax.set_title(f"PT score vs MAS grade (n={stats['n']}{caveat})\n{rho_txt}",
                fontsize=10, fontweight="bold")

    # ── Panel 2: agreement heatmap (actual x predicted) ─────────────────────
    ax = axes[1]
    mat = np.zeros((len(MAS_ORDER), len(MAS_ORDER)), dtype=int)
    for p in pairs:
        mat[MAS_RANK[p["mas_grade"]], MAS_RANK[p["predicted_mas"]]] += 1
    ax.imshow(mat, cmap="Blues")
    ax.set_xticks(range(len(MAS_ORDER))); ax.set_xticklabels(MAS_ORDER, fontsize=9)
    ax.set_yticks(range(len(MAS_ORDER))); ax.set_yticklabels(MAS_ORDER, fontsize=9)
    ax.set_xlabel("Predicted MAS (from PT score)", fontsize=9)
    ax.set_ylabel("Actual (clinician) MAS", fontsize=9)
    peak = mat.max() if mat.size else 0
    for i in range(len(MAS_ORDER)):
        for j in range(len(MAS_ORDER)):
            if mat[i, j]:
                ax.text(j, i, str(mat[i, j]), ha="center", va="center",
                       color="white" if peak and mat[i, j] > peak / 2 else "black", fontsize=9)
    kappa_txt = f"weighted kappa={stats['weighted_kappa']:.2f}" if stats["weighted_kappa"] is not None else "kappa=n/a"
    ax.set_title(f"Agreement: predicted vs actual MAS\n{kappa_txt}", fontsize=10, fontweight="bold")

    # ── Panel 3 (optional): ROC, MAS>=1 ("spastic") vs MAS==0 ───────────────
    if stats["roc_auc"] is not None:
        ax = axes[2]
        binary = np.array([0 if p["mas_grade"] == "0" else 1 for p in pairs])
        pt_scores = np.array([p["pt_score"] for p in pairs])
        fpr, tpr, _ = roc_curve(binary, pt_scores)
        ax.plot(fpr, tpr, color=common.COLORS["blue"], linewidth=2)
        ax.plot([0, 1], [0, 1], color="#cccccc", linestyle="--", linewidth=1)
        ax.set_xlabel("False positive rate", fontsize=9)
        ax.set_ylabel("True positive rate", fontsize=9)
        ax.set_title(f"Spastic (MAS>=1) vs not\nAUC={stats['roc_auc']:.2f}", fontsize=10, fontweight="bold")

    fig.suptitle("PT Score vs Clinician MAS -- Concurrent Validity", fontsize=12, y=1.03, color="#333333")
    plt.tight_layout()
    return fig


def save_validation_figure(pairs, stats, out_path):
    fig = build_validation_figure(pairs, stats)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fig.savefig(out_path, dpi=150, facecolor="white", bbox_inches="tight")
    plt.close(fig)
    print(f"-> {out_path}")
```

- [ ] **Step 4: Update `main()`'s call site**

In `mas_validation.py`, find (currently line 325):

```python
    make_validation_figure(valid, stats, FIGURE_PNG)
```

Replace with:

```python
    save_validation_figure(valid, stats, FIGURE_PNG)
```

- [ ] **Step 5: Fix the matplotlib backend guard**

In `mas_validation.py`, replace the top-of-file matplotlib import (currently lines 41-43):

```python
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
```

with:

```python
import matplotlib
if __name__ == "__main__":
    matplotlib.use("Agg")
import matplotlib.pyplot as plt
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_mas_validation.py -v`
Expected: PASS (all tests, including the 3 new ones from Step 1)

- [ ] **Step 7: Commit**

```bash
git add mas_validation.py tests/test_mas_validation.py
git commit -m "refactor: split mas_validation figure builder; fix matplotlib backend guard"
```

---

### Task 3: `MasEntryPanel` skeleton + navigation wiring

**Files:**
- Modify: `pendulastic_app.py:115-122` (guarded imports, right after the `_WORKBENCH_AVAIL` block), `pendulastic_app.py:1035-1039` (`ModeSelectView._build_widgets`, 4th button), `pendulastic_app.py:1877` (insert new class before `class App(tk.Tk):`), `pendulastic_app.py:1938-1946` (`App.__init__`), `pendulastic_app.py:2201-2205` (insert new `_enter_mas_entry_mode` after `_enter_analysis_mode`), `pendulastic_app.py:2345-2359` (`on_back_to_mode_select`)
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: `MasEntryPanel(tk.Frame)` with `__init__(self, parent, controller)`, importable from `pendulastic_app`. `App` gains `self._mas_entry` (constructed only when `_MAS_VALIDATION_AVAIL` is `True`, matching the existing `_WORKBENCH_AVAIL` pattern) and `_enter_mas_entry_mode(self) -> None`. Later tasks (4, 5, 6) extend `MasEntryPanel._build_widgets` and add `refresh()`/`_on_save_clicked()`/`_on_export_clicked()` — this task only builds the header/back-button skeleton and the navigation plumbing around it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`, after `test_on_dashboard_back_returns_to_trial_load_panel` (the last test in the dashboard-navigation cluster):

```python
def test_enter_mas_entry_mode_shows_panel_and_hides_mode_select():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_mas_entry_mode()
        app.update()
        assert app._mas_entry.winfo_ismapped()
        assert not app._mode_select.winfo_ismapped()
        assert app._state == "mas_entry"
    finally:
        app.destroy()


def test_on_back_to_mode_select_hides_mas_entry_panel():
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._enter_mas_entry_mode()
        app.update()
        app.on_back_to_mode_select()
        app.update()
        assert app._mode_select.winfo_ismapped()
        assert not app._mas_entry.winfo_ismapped()
        assert app._state == "mode_select"
    finally:
        app.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k mas_entry_mode -v`
Expected: FAIL with `AttributeError: 'App' object has no attribute '_enter_mas_entry_mode'`

- [ ] **Step 3: Add the guarded import**

In `pendulastic_app.py`, immediately after the existing `_WORKBENCH_AVAIL` guarded-import block (currently lines 115-122):

```python
try:
    from pendulastic_workbench import TrialLoadPanel, WorkbenchView, DashboardView
    import workbench_engine as _wb_engine
    _WORKBENCH_AVAIL = True
except Exception:
    TrialLoadPanel = WorkbenchView = DashboardView = None
    _wb_engine = None
    _WORKBENCH_AVAIL = False
```

add:

```python
try:
    import mas_validation as _mas_validation
    _MAS_VALIDATION_AVAIL = True
except Exception:
    _mas_validation = None
    _MAS_VALIDATION_AVAIL = False
```

- [ ] **Step 4: Add the `MasEntryPanel` skeleton class**

In `pendulastic_app.py`, find the existing banner immediately above `class App(tk.Tk):` (currently just above line 1878, right after `AnalysisPanel` ends):

```python
# ---------------------------------------------------------------------------
# App  (thin host)
# ---------------------------------------------------------------------------

class App(tk.Tk):
```

Replace with:

```python
class MasEntryPanel(tk.Frame):
    """MAS score entry form + live PT-score-vs-MAS validation dashboard.
    controller: App instance -- receives on_back_to_mode_select()."""

    def __init__(self, parent, controller) -> None:
        super().__init__(parent)
        self.controller = controller
        self._build_widgets()

    def _build_widgets(self) -> None:
        self.configure(bg=ws.PALETTE["BG"])

        hdr = tk.Frame(self, bg=ws.PALETTE["BG"])
        hdr.pack(fill="x", padx=12, pady=(16, 4))
        ws.secondary_button(
            hdr, "← Mode Select", self.controller.on_back_to_mode_select
        ).pack(side="left", padx=(0, 8))
        tk.Label(hdr, text="Pendulastic — MAS Score Entry",
                 font=("Segoe UI", 13, "bold"),
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).pack(side="left")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=4)


# ---------------------------------------------------------------------------
# App  (thin host)
# ---------------------------------------------------------------------------

class App(tk.Tk):
```

- [ ] **Step 5: Register the panel in `App.__init__`**

In `pendulastic_app.py`, find (currently lines 1938-1946):

```python
        if _WORKBENCH_AVAIL:
            self._workbench_load = TrialLoadPanel(self, controller=self)
            self._workbench_view = WorkbenchView(self, controller=self)
            self._dashboard_view = DashboardView(self, controller=self)
            tk.Label(self, textvariable=self._workbench_status_var, anchor="w",
                     bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG2"]).pack(
                side="bottom", fill="x", padx=8, pady=2)

        self._mode_select.pack(fill="both", expand=True)
```

Replace with:

```python
        if _WORKBENCH_AVAIL:
            self._workbench_load = TrialLoadPanel(self, controller=self)
            self._workbench_view = WorkbenchView(self, controller=self)
            self._dashboard_view = DashboardView(self, controller=self)
            tk.Label(self, textvariable=self._workbench_status_var, anchor="w",
                     bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG2"]).pack(
                side="bottom", fill="x", padx=8, pady=2)

        if _MAS_VALIDATION_AVAIL:
            self._mas_entry = MasEntryPanel(self, controller=self)

        self._mode_select.pack(fill="both", expand=True)
```

- [ ] **Step 6: Add the 5th `ModeSelectView` nav button**

In `pendulastic_app.py`, find (currently lines 1035-1039):

```python
        analysis_btn = ws.secondary_button(
            self, "Analysis & Reports\nCompare Participants",
            self.controller._enter_analysis_mode)
        analysis_btn.config(font=("Segoe UI", 12, "bold"), width=24, height=4)
        analysis_btn.grid(row=4, column=0, columnspan=2, padx=40, pady=(0, 24), sticky="n")
```

Replace with:

```python
        analysis_btn = ws.secondary_button(
            self, "Analysis & Reports\nCompare Participants",
            self.controller._enter_analysis_mode)
        analysis_btn.config(font=("Segoe UI", 12, "bold"), width=24, height=4)
        analysis_btn.grid(row=4, column=0, columnspan=2, padx=40, pady=(0, 12), sticky="n")

        mas_btn = ws.secondary_button(
            self, "MAS Score Entry\nEnter & Validate",
            self.controller._enter_mas_entry_mode)
        mas_btn.config(font=("Segoe UI", 12, "bold"), width=24, height=4)
        mas_btn.grid(row=5, column=0, columnspan=2, padx=40, pady=(0, 24), sticky="n")
```

- [ ] **Step 7: Add `_enter_mas_entry_mode`**

In `pendulastic_app.py`, find `_enter_analysis_mode` (currently lines 2201-2205):

```python
    def _enter_analysis_mode(self) -> None:
        self._mode_select.pack_forget()
        self._analysis.pack(fill="both", expand=True)
        self._state = "analysis"
        self._analysis.on_shown()
```

Add immediately after it:

```python
    def _enter_mas_entry_mode(self) -> None:
        if not _MAS_VALIDATION_AVAIL:
            messagebox.showinfo(
                "MAS Entry Unavailable",
                "MAS score entry could not be loaded in this environment "
                "(a required dependency is missing).")
            return
        self._mode_select.pack_forget()
        self._mas_entry.pack(fill="both", expand=True)
        self._state = "mas_entry"
```

- [ ] **Step 8: Hide the panel in `on_back_to_mode_select`**

In `pendulastic_app.py`, find (currently lines 2345-2359):

```python
    def on_back_to_mode_select(self) -> None:
        self._acq.pack_forget()
        self._post.pack_forget()
        self._upload_meta.pack_forget()
        self._analysis.pack_forget()
        if _WORKBENCH_AVAIL:
            self._workbench_load.pack_forget()
            self._workbench_view.pack_forget()
            self._dashboard_view.pack_forget()
        self._mode_select.pack(fill="both", expand=True)
        self._state        = "mode_select"
        self._active_sources  = []
        self._rec_angles      = {}
        self._rec_timestamps  = {}
        self._pending_review  = {}
```

Replace with:

```python
    def on_back_to_mode_select(self) -> None:
        self._acq.pack_forget()
        self._post.pack_forget()
        self._upload_meta.pack_forget()
        self._analysis.pack_forget()
        if _WORKBENCH_AVAIL:
            self._workbench_load.pack_forget()
            self._workbench_view.pack_forget()
            self._dashboard_view.pack_forget()
        if _MAS_VALIDATION_AVAIL:
            self._mas_entry.pack_forget()
        self._mode_select.pack(fill="both", expand=True)
        self._state        = "mode_select"
        self._active_sources  = []
        self._rec_angles      = {}
        self._rec_timestamps  = {}
        self._pending_review  = {}
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k mas_entry_mode -v`
Expected: PASS (2 tests)

- [ ] **Step 10: Run the full test_app.py suite to confirm no regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -v`
Expected: PASS (all tests)

- [ ] **Step 11: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: add MasEntryPanel skeleton and navigation wiring"
```

---

### Task 4: Entry form fields + refresh pipeline (dashboard, empty state, skipped-row status)

**Files:**
- Modify: `pendulastic_app.py` (`MasEntryPanel._build_widgets`, from Task 3)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `_mas_validation.load_mas_scores(csv_path) -> list[dict]`, `_mas_validation.pair_pt_and_mas(rows, pt_lookup) -> list[dict]` (each item either has `pt_score`/`predicted_mas` or a `_skip_reason` key), `_mas_validation._pt_lookup_factory() -> Callable`, `_mas_validation.compute_validation_stats(pairs) -> dict`, `_mas_validation.build_validation_figure(pairs, stats) -> Figure`, `_mas_validation.MAS_CSV`, `_mas_validation.MAS_ORDER` — all from Task 1/2 and pre-existing `mas_validation.py` code.
- Produces: `MasEntryPanel` gains `pid_var`, `leg_var`, `condition_var`, `diagnosis_var`, `mas_grade_var`, `assessed_by_var`, `assessed_date_var` (all `tk.StringVar`), `status_text` (`tk.Text`), `canvas_frame`/`canvas_placeholder`, and `refresh(self) -> None`. `refresh()` sets `self._last_valid: list` and `self._last_stats: Optional[dict]`, which Task 6's Export button reads. Task 5's Save button calls `self.refresh()` after a successful append.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`, after the two tests added in Task 3:

```python
def test_mas_entry_panel_empty_state_placeholder(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m._mas_validation, "load_mas_scores", lambda path: [])
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.refresh()
        app.update()
        assert app._mas_entry.canvas_placeholder.winfo_ismapped()
        assert app._mas_entry._current_canvas is None
    finally:
        app.destroy()


def test_mas_entry_panel_shows_skipped_row_status(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m._mas_validation, "load_mas_scores", lambda path: [
        {"participant": "14", "leg": "left", "condition": "pre", "mas_grade": "1"}])
    monkeypatch.setattr(_m._mas_validation, "_pt_lookup_factory",
                        lambda: (lambda p, l, c: None))
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.refresh()
        app.update()
        text = app._mas_entry.status_text.get("1.0", "end")
        assert "14" in text
        assert "no matching trial data" in text
    finally:
        app.destroy()


def test_mas_entry_panel_refresh_renders_figure_when_data_present(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m._mas_validation, "load_mas_scores", lambda path: [
        {"participant": "20", "leg": "left", "condition": "pre", "mas_grade": "1"}])
    monkeypatch.setattr(_m._mas_validation, "_pt_lookup_factory",
                        lambda: (lambda p, l, c: 0.2))
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.refresh()
        app.update()
        assert not app._mas_entry.canvas_placeholder.winfo_ismapped()
        assert app._mas_entry._current_canvas is not None
        assert len(app._mas_entry._last_valid) == 1
        assert app._mas_entry._last_stats is not None
    finally:
        app.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k "mas_entry_panel" -v`
Expected: FAIL with `AttributeError: 'MasEntryPanel' object has no attribute 'refresh'`

- [ ] **Step 3: Extend `_build_widgets` with the form fields, status area, and canvas**

In `pendulastic_app.py`, find the `MasEntryPanel._build_widgets` method added in Task 3:

```python
    def _build_widgets(self) -> None:
        self.configure(bg=ws.PALETTE["BG"])

        hdr = tk.Frame(self, bg=ws.PALETTE["BG"])
        hdr.pack(fill="x", padx=12, pady=(16, 4))
        ws.secondary_button(
            hdr, "← Mode Select", self.controller.on_back_to_mode_select
        ).pack(side="left", padx=(0, 8))
        tk.Label(hdr, text="Pendulastic — MAS Score Entry",
                 font=("Segoe UI", 13, "bold"),
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).pack(side="left")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=4)
```

Replace with:

```python
    def _build_widgets(self) -> None:
        import datetime as _datetime
        pad = {"padx": 12, "pady": 5}
        self.configure(bg=ws.PALETTE["BG"])

        hdr = tk.Frame(self, bg=ws.PALETTE["BG"])
        hdr.pack(fill="x", padx=12, pady=(16, 4))
        ws.secondary_button(
            hdr, "← Mode Select", self.controller.on_back_to_mode_select
        ).pack(side="left", padx=(0, 8))
        tk.Label(hdr, text="Pendulastic — MAS Score Entry",
                 font=("Segoe UI", 13, "bold"),
                 bg=ws.PALETTE["BG"], fg=ws.PALETTE["FG"]).pack(side="left")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=4)

        form = tk.Frame(self, bg=ws.PALETTE["BG"])
        form.pack(fill="x")
        form.columnconfigure(1, weight=1)

        tk.Label(form, text="Participant ID:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=0, column=0, sticky="e", **pad)
        self.pid_var = tk.StringVar()
        tk.Entry(form, textvariable=self.pid_var, width=22).grid(
            row=0, column=1, sticky="w", **pad)

        tk.Label(form, text="Leg:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=1, column=0, sticky="e", **pad)
        self.leg_var = tk.StringVar(value="Left")
        leg_f = tk.Frame(form, bg=ws.PALETTE["BG"])
        leg_f.grid(row=1, column=1, sticky="w", **pad)
        tk.Radiobutton(leg_f, text="Left", variable=self.leg_var, value="Left",
                      bg=ws.PALETTE["BG"], activebackground=ws.PALETTE["BG"]
                      ).pack(side="left", padx=4)
        tk.Radiobutton(leg_f, text="Right", variable=self.leg_var, value="Right",
                      bg=ws.PALETTE["BG"], activebackground=ws.PALETTE["BG"]
                      ).pack(side="left", padx=4)

        tk.Label(form, text="Condition:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=2, column=0, sticky="e", **pad)
        self.condition_var = tk.StringVar()
        tk.Entry(form, textvariable=self.condition_var, width=22).grid(
            row=2, column=1, sticky="w", **pad)

        tk.Label(form, text="Diagnosis:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=3, column=0, sticky="e", **pad)
        self.diagnosis_var = tk.StringVar()
        tk.Entry(form, textvariable=self.diagnosis_var, width=22).grid(
            row=3, column=1, sticky="w", **pad)

        tk.Label(form, text="MAS Grade:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=4, column=0, sticky="e", **pad)
        self.mas_grade_var = tk.StringVar()
        ttk.Combobox(form, textvariable=self.mas_grade_var, width=19,
                    state="readonly",
                    values=list(_mas_validation.MAS_ORDER)).grid(
            row=4, column=1, sticky="w", **pad)

        tk.Label(form, text="Assessed By:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=5, column=0, sticky="e", **pad)
        self.assessed_by_var = tk.StringVar()
        tk.Entry(form, textvariable=self.assessed_by_var, width=22).grid(
            row=5, column=1, sticky="w", **pad)

        tk.Label(form, text="Assessed Date:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=6, column=0, sticky="e", **pad)
        self.assessed_date_var = tk.StringVar(
            value=_datetime.date.today().isoformat())
        tk.Entry(form, textvariable=self.assessed_date_var, width=22).grid(
            row=6, column=1, sticky="w", **pad)

        status_frame = tk.Frame(self, bg=ws.PALETTE["BG"])
        status_frame.pack(fill="x", padx=12, pady=(4, 8))
        self.status_text = tk.Text(status_frame, height=4, wrap="word",
                                   state="disabled", bg=ws.PALETTE["SURFACE"],
                                   fg=ws.PALETTE["FG"])
        status_scroll = tk.Scrollbar(status_frame, command=self.status_text.yview)
        self.status_text.configure(yscrollcommand=status_scroll.set)
        self.status_text.pack(side="left", fill="x", expand=True)
        status_scroll.pack(side="right", fill="y")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=4)

        self._current_canvas = None
        self._current_fig = None
        self._last_valid: list = []
        self._last_stats = None

        self.canvas_frame = tk.Frame(self, bg=ws.PALETTE["SURFACE"])
        self.canvas_frame.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self.canvas_placeholder = tk.Label(
            self.canvas_frame,
            text="No MAS-scored trials with matching trial data yet",
            bg=ws.PALETTE["SURFACE"], fg=ws.PALETTE["FG2"])
        self.canvas_placeholder.pack(pady=40)

    def refresh(self) -> None:
        rows = _mas_validation.load_mas_scores(_mas_validation.MAS_CSV)
        paired = _mas_validation.pair_pt_and_mas(
            rows, _mas_validation._pt_lookup_factory())
        valid = [p for p in paired if "_skip_reason" not in p]
        skipped = [p for p in paired if "_skip_reason" in p]

        self.status_text.config(state="normal")
        self.status_text.delete("1.0", "end")
        for row in skipped:
            self.status_text.insert(
                "end",
                f"P{row.get('participant')} {row.get('leg')}/{row.get('condition')}: "
                f"{row['_skip_reason']}\n")
        self.status_text.config(state="disabled")

        if not valid:
            self._last_valid = []
            self._last_stats = None
            self._show_placeholder()
            return

        stats = _mas_validation.compute_validation_stats(valid)
        fig = _mas_validation.build_validation_figure(valid, stats)
        self._last_valid = valid
        self._last_stats = stats
        self._show_figure(fig)

    def _show_placeholder(self) -> None:
        if self._current_canvas is not None:
            self._current_canvas.get_tk_widget().destroy()
            self._current_canvas = None
        if self._current_fig is not None:
            try:
                import matplotlib.pyplot as _plt
                _plt.close(self._current_fig)
            except Exception:
                pass
            self._current_fig = None
        self.canvas_placeholder.pack(pady=40)

    def _show_figure(self, fig) -> None:
        self.canvas_placeholder.pack_forget()
        if self._current_canvas is not None:
            self._current_canvas.get_tk_widget().destroy()
        if self._current_fig is not None:
            try:
                import matplotlib.pyplot as _plt
                _plt.close(self._current_fig)
            except Exception:
                pass
        self._current_fig = fig
        self._current_canvas = FigureCanvasTkAgg(fig, master=self.canvas_frame)
        self._current_canvas.draw()
        self._current_canvas.get_tk_widget().pack(fill="both", expand=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k "mas_entry_panel" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full test_app.py suite to confirm no regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -v`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: add MasEntryPanel refresh pipeline (dashboard, empty state, skipped-row status)"
```

---

### Task 5: Save button

**Files:**
- Modify: `pendulastic_app.py` (`MasEntryPanel._build_widgets`/new `_on_save_clicked`, from Task 4)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `_mas_validation.append_mas_score(row: dict) -> None` (raises `ValueError` on invalid grade) from Task 1; `self.refresh()` from Task 4.
- Produces: `MasEntryPanel` gains `error_var` (`tk.StringVar`) and `_on_save_clicked(self) -> None`. Blank `participant` or `mas_grade` sets `error_var` and returns without calling `append_mas_score`. A caught `ValueError` from `append_mas_score` also sets `error_var` and returns. On success, clears `error_var` and calls `self.refresh()`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`, after the three tests added in Task 4:

```python
def test_mas_entry_panel_blocks_save_on_missing_required_fields(monkeypatch):
    import pendulastic_app as _m
    calls = []
    monkeypatch.setattr(_m._mas_validation, "append_mas_score",
                        lambda row, **kw: calls.append(row))
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.pid_var.set("")
        app._mas_entry.mas_grade_var.set("")
        app._mas_entry._on_save_clicked()
        app.update()
        assert calls == []
        assert "required" in app._mas_entry.error_var.get().lower()
    finally:
        app.destroy()


def test_mas_entry_panel_save_appends_and_refreshes(monkeypatch):
    import pendulastic_app as _m
    append_calls = []
    monkeypatch.setattr(_m._mas_validation, "append_mas_score",
                        lambda row, **kw: append_calls.append(row))
    monkeypatch.setattr(_m._mas_validation, "load_mas_scores", lambda path: [
        {"participant": "20", "leg": "left", "condition": "pre", "mas_grade": "1"}])
    monkeypatch.setattr(_m._mas_validation, "_pt_lookup_factory",
                        lambda: (lambda p, l, c: 0.2))
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.pid_var.set("20")
        app._mas_entry.mas_grade_var.set("1")
        app._mas_entry._on_save_clicked()
        app.update()
        assert len(append_calls) == 1
        assert append_calls[0]["participant"] == "20"
        assert append_calls[0]["leg"] == "left"
        assert append_calls[0]["mas_grade"] == "1"
        assert app._mas_entry.error_var.get() == ""
        assert app._mas_entry._current_canvas is not None
    finally:
        app.destroy()


def test_mas_entry_panel_save_shows_error_on_invalid_grade(monkeypatch):
    import pendulastic_app as _m

    def raise_invalid(row, **kw):
        raise ValueError(f"invalid mas_grade {row['mas_grade']!r} (must be one of [])")
    monkeypatch.setattr(_m._mas_validation, "append_mas_score", raise_invalid)
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.pid_var.set("20")
        app._mas_entry.mas_grade_var.set("1")
        app._mas_entry._on_save_clicked()
        app.update()
        assert "invalid mas_grade" in app._mas_entry.error_var.get()
    finally:
        app.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k "mas_entry_panel_save or mas_entry_panel_blocks" -v`
Expected: FAIL with `AttributeError: 'MasEntryPanel' object has no attribute 'error_var'`

- [ ] **Step 3: Add the error label, Save button, and handler**

In `pendulastic_app.py`, find the end of `MasEntryPanel._build_widgets` (from Task 4) — the block ending in:

```python
        self.canvas_frame = tk.Frame(self, bg=ws.PALETTE["SURFACE"])
        self.canvas_frame.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self.canvas_placeholder = tk.Label(
            self.canvas_frame,
            text="No MAS-scored trials with matching trial data yet",
            bg=ws.PALETTE["SURFACE"], fg=ws.PALETTE["FG2"])
        self.canvas_placeholder.pack(pady=40)

    def refresh(self) -> None:
```

Insert the error label + Save button between the `assessed_date` row and the status-area block (i.e. right after the `assessed_date` `tk.Entry` and before `status_frame = tk.Frame(...)`):

```python
        tk.Label(form, text="Assessed Date:", bg=ws.PALETTE["BG"],
                 fg=ws.PALETTE["FG"]).grid(row=6, column=0, sticky="e", **pad)
        self.assessed_date_var = tk.StringVar(
            value=_datetime.date.today().isoformat())
        tk.Entry(form, textvariable=self.assessed_date_var, width=22).grid(
            row=6, column=1, sticky="w", **pad)

        self.error_var = tk.StringVar(value="")
        tk.Label(self, textvariable=self.error_var, fg="#B45309",
                 bg=ws.PALETTE["BG"]).pack(fill="x", padx=12, pady=(0, 4))

        ws.primary_button(self, "Save", self._on_save_clicked).pack(pady=(0, 8))

        status_frame = tk.Frame(self, bg=ws.PALETTE["BG"])
```

(Only the `self.error_var = ...` through the `ws.primary_button(...)` line are new; the `assessed_date` block above and `status_frame = tk.Frame(...)` line below already exist from Task 4 and are shown here only to pinpoint the insertion point.)

Then find the boundary between `refresh()` and `_show_placeholder()` (from Task 4):

```python
        self._show_figure(fig)

    def _show_placeholder(self) -> None:
```

Replace with:

```python
        self._show_figure(fig)

    def _on_save_clicked(self) -> None:
        participant = self.pid_var.get().strip()
        mas_grade = self.mas_grade_var.get().strip()
        if not participant or not mas_grade:
            self.error_var.set("Participant ID and MAS grade are required.")
            return
        row = {
            "participant": participant,
            "leg": self.leg_var.get().lower(),
            "condition": self.condition_var.get().strip(),
            "diagnosis": self.diagnosis_var.get().strip(),
            "mas_grade": mas_grade,
            "assessed_by": self.assessed_by_var.get().strip(),
            "assessed_date": self.assessed_date_var.get().strip(),
        }
        try:
            _mas_validation.append_mas_score(row)
        except ValueError as e:
            self.error_var.set(str(e))
            return
        self.error_var.set("")
        self.refresh()

    def _show_placeholder(self) -> None:
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k "mas_entry_panel_save or mas_entry_panel_blocks" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full test_app.py suite to confirm no regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -v`
Expected: PASS (all tests)

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: add Save button to MasEntryPanel"
```

---

### Task 6: Export button

**Files:**
- Modify: `pendulastic_app.py` (`MasEntryPanel._build_widgets`/new `_on_export_clicked`, from Task 5)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `_mas_validation.write_stats_csv(stats: dict, out_path: str) -> None` (pre-existing, unmodified), `_mas_validation.save_validation_figure(pairs, stats, out_path) -> None` (from Task 2), `_mas_validation.STATS_CSV`/`_mas_validation.FIGURE_PNG`/`_mas_validation.OUT_DIR` (pre-existing module constants). `self._last_valid`/`self._last_stats` from Task 4's `refresh()`.
- Produces: `MasEntryPanel` gains `export_btn` (`tk.Button`, disabled whenever `self._last_valid` is empty) and `_on_export_clicked(self) -> None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py`, after the three tests added in Task 5:

```python
def test_mas_entry_panel_export_disabled_when_no_data(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m._mas_validation, "load_mas_scores", lambda path: [])
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.refresh()
        app.update()
        assert str(app._mas_entry.export_btn.cget("state")) == "disabled"
    finally:
        app.destroy()


def test_mas_entry_panel_export_writes_stats_and_figure(monkeypatch):
    import pendulastic_app as _m
    monkeypatch.setattr(_m._mas_validation, "load_mas_scores", lambda path: [
        {"participant": "20", "leg": "left", "condition": "pre", "mas_grade": "1"}])
    monkeypatch.setattr(_m._mas_validation, "_pt_lookup_factory",
                        lambda: (lambda p, l, c: 0.2))
    stats_calls = []
    figure_calls = []
    monkeypatch.setattr(_m._mas_validation, "write_stats_csv",
                        lambda stats, out_path: stats_calls.append((stats, out_path)))
    monkeypatch.setattr(_m._mas_validation, "save_validation_figure",
                        lambda valid, stats, out_path: figure_calls.append(
                            (valid, stats, out_path)))
    from pendulastic_app import App
    app = App()
    try:
        app.update()
        app._mas_entry.refresh()
        app.update()
        assert str(app._mas_entry.export_btn.cget("state")) == "normal"
        app._mas_entry._on_export_clicked()
        assert len(stats_calls) == 1
        assert stats_calls[0][0] == app._mas_entry._last_stats
        assert len(figure_calls) == 1
        assert figure_calls[0][0] == app._mas_entry._last_valid
        assert figure_calls[0][1] == app._mas_entry._last_stats
    finally:
        app.destroy()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k mas_entry_panel_export -v`
Expected: FAIL with `AttributeError: 'MasEntryPanel' object has no attribute 'export_btn'`

- [ ] **Step 3: Add the Export button and handler**

In `pendulastic_app.py`, find the end of `MasEntryPanel._build_widgets` (from Task 4):

```python
        self.canvas_frame = tk.Frame(self, bg=ws.PALETTE["SURFACE"])
        self.canvas_frame.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self.canvas_placeholder = tk.Label(
            self.canvas_frame,
            text="No MAS-scored trials with matching trial data yet",
            bg=ws.PALETTE["SURFACE"], fg=ws.PALETTE["FG2"])
        self.canvas_placeholder.pack(pady=40)

    def refresh(self) -> None:
```

Replace with:

```python
        self.canvas_frame = tk.Frame(self, bg=ws.PALETTE["SURFACE"])
        self.canvas_frame.pack(fill="both", expand=True, padx=12, pady=(0, 4))
        self.canvas_placeholder = tk.Label(
            self.canvas_frame,
            text="No MAS-scored trials with matching trial data yet",
            bg=ws.PALETTE["SURFACE"], fg=ws.PALETTE["FG2"])
        self.canvas_placeholder.pack(pady=40)

        self.export_btn = ws.secondary_button(self, "Export", self._on_export_clicked)
        self.export_btn.config(state="disabled")
        self.export_btn.pack(pady=(0, 12))

    def refresh(self) -> None:
```

Then update `refresh()`'s two branches to toggle `export_btn`. Find (from Task 4):

```python
        if not valid:
            self._last_valid = []
            self._last_stats = None
            self._show_placeholder()
            return

        stats = _mas_validation.compute_validation_stats(valid)
        fig = _mas_validation.build_validation_figure(valid, stats)
        self._last_valid = valid
        self._last_stats = stats
        self._show_figure(fig)
```

Replace with:

```python
        if not valid:
            self._last_valid = []
            self._last_stats = None
            self._show_placeholder()
            self.export_btn.config(state="disabled")
            return

        stats = _mas_validation.compute_validation_stats(valid)
        fig = _mas_validation.build_validation_figure(valid, stats)
        self._last_valid = valid
        self._last_stats = stats
        self._show_figure(fig)
        self.export_btn.config(state="normal")
```

Finally, find the boundary between `_on_save_clicked` and `_show_placeholder()` (from Task 5):

```python
        self.error_var.set("")
        self.refresh()

    def _show_placeholder(self) -> None:
```

Replace with:

```python
        self.error_var.set("")
        self.refresh()

    def _on_export_clicked(self) -> None:
        if not self._last_valid or self._last_stats is None:
            return
        _mas_validation.write_stats_csv(self._last_stats, _mas_validation.STATS_CSV)
        _mas_validation.save_validation_figure(
            self._last_valid, self._last_stats, _mas_validation.FIGURE_PNG)
        messagebox.showinfo("Exported", f"Saved to:\n{_mas_validation.OUT_DIR}")

    def _show_placeholder(self) -> None:
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_app.py -k mas_entry_panel_export -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full test suite to confirm no regressions**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q --ignore=tests/test_metrics.py --ignore=tests/test_pose.py`
Expected: PASS (all tests; the only acceptable failures are the pre-existing, order-dependent Tcl/Tk resource-contention flakes documented in this project's recent merge history — re-run any failure in isolation to confirm before treating it as a regression)

- [ ] **Step 6: Commit**

```bash
git add pendulastic_app.py tests/test_app.py
git commit -m "feat: add Export button to MasEntryPanel"
```

## Self-Review Notes

- **Spec coverage:** §5.1 `append_mas_score` -> Task 1. §5.2 figure split -> Task 2 Steps 3-4. §5.3 backend guard -> Task 2 Step 5. §6 panel registration/nav -> Task 3. §6.1 entry form -> Task 4 Step 3. §6.2 dashboard + Export -> Task 4 (canvas/placeholder) + Task 6 (Export button). §6.3 refresh pipeline -> Task 4 Step 3 (`refresh`/`_show_placeholder`/`_show_figure`). §7 error handling -> Task 5 (`_on_save_clicked`) + Task 4 (empty-state placeholder). §8 testing -> one test per spec bullet, plus 2 extra figure-shape tests in Task 2 (correcting the spec's incorrect assumption that `make_validation_figure` already had tests) and 1 extra error-path test in Task 5.
- **Placeholder scan:** none — every step has literal, complete code. Task ordering (skeleton -> refresh pipeline -> Save -> Export) was chosen specifically so no task ever leaves a stub method behind for a later task to fill in.
- **Type consistency:** `append_mas_score(row: dict, csv_path=MAS_CSV) -> None` (Task 1) is called identically in Task 5's `_on_save_clicked`. `build_validation_figure(pairs, stats) -> Figure` and `save_validation_figure(pairs, stats, out_path) -> None` (Task 2) are called with matching argument order in Task 4's `refresh()` and Task 6's `_on_export_clicked()`. `self._last_valid`/`self._last_stats` are set once in Task 4's `refresh()` and read only in Task 6's `_on_export_clicked()` — no naming drift.
