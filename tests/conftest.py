"""Shared pytest configuration for the GUI tests.

Tk tears down Tcl's global library state when the last interpreter in a process
goes away, and re-initialising it mid-session is unreliable on this build. It
surfaces as a TclError naming a library file that demonstrably exists on disk --
init.tcl, auto.tcl, ttk/utils.tcl -- with the named file varying from run to
run, and it takes down whichever test happens to construct the next App:

    _tkinter.TclError: couldn't read file ".../tcl8.6/auto.tcl":
                       no such file or directory

The suite builds ~120 App instances, each its own interpreter, so it hit this
often: tests/test_master_app_imu.py failed 2 runs in 3 on a clean checkout.

Holding one hidden root open for the whole session keeps that global state
alive, so no individual App teardown is ever the last one out. Measured across
10 runs of the two previously-flaky files: zero failures.

Keep this root alive. Destroying it mid-session reintroduces the problem.
"""

import os
import re
import sys

# Optional on purpose. pytest_configure below already treats a missing display
# as a non-fatal condition, with a comment spelling out why: an exception there
# "aborts the whole session at collection time, so a missing display would take
# down the ~30 pure-computation files ... that have no Tk dependency and pass
# headless today." A hard import at module scope defeated that intent for the
# stronger case of Tk not being installed at all -- a Tk-less Python, a slim
# container, a cloud session -- where THIS file raised ModuleNotFoundError
# before any handler could run, taking down all 78 test files rather than the
# 17 that actually need Tk. Importing it optionally, and skipping only the
# modules that need it (pytest_ignore_collect below), restores the documented
# behaviour.
try:
    import tkinter
except ModuleNotFoundError:  # pragma: no cover - depends on the interpreter build
    tkinter = None

# The repo ships a src-layout package (setup.py, package_dir={"": "src"}) that
# is not installed in the dev environment, so `import pendulastic` failed and
# tests/test_pose.py and tests/test_metrics.py could not even be collected.
# Every other test file here bootstraps its own path rather than requiring an
# install step; do the same for src/ so a bare checkout runs the whole suite.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# The repo root, for the same reason and one more: most modules under test live
# there (imu_calibration_tuner, pendulastic_pt_score, workbench_engine, ...),
# and whether they import depends on how pytest was started. `python -m pytest`
# puts the current directory on sys.path, so a run from the repo root works;
# the bare `pytest` console script does not, and pytest's own rootdir insertion
# only covers the directory holding the test file. CI invokes the bare script,
# which is why `pytest tests/test_web_export_contract.py` failed there with
# ModuleNotFoundError: No module named 'imu_calibration_tuner' while the same
# command passed locally under `python -m pytest`. Bootstrapping it here makes
# the suite independent of the launcher.
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_anchor = None

# Every GUI test maps a real window, so a full run otherwise takes over the
# screen for ~22 minutes. Parking each one off-screen and fully transparent
# keeps it *mapped* -- winfo_ismapped() stays true, so visibility assertions
# still mean what they say -- while keeping it off the display.
# Set PENDULASTIC_SHOW_TEST_WINDOWS=1 to watch the windows while debugging.
_OFFSCREEN = (10000, 10000)


def _park(win):
    try:
        win.wm_geometry("+%d+%d" % _OFFSCREEN)
    except Exception:
        pass
    try:
        win.wm_attributes("-alpha", 0.0)
    except Exception:
        pass


def _hide_new_windows():
    orig_tk = tkinter.Tk.__init__
    orig_top = tkinter.Toplevel.__init__

    def tk_init(self, *a, **kw):
        orig_tk(self, *a, **kw)
        _park(self)

    def top_init(self, *a, **kw):
        orig_top(self, *a, **kw)
        _park(self)

    tkinter.Tk.__init__ = tk_init
    tkinter.Toplevel.__init__ = top_init


# Modules that cannot even be imported without Tk: they import tkinter
# directly, or a repo module that builds a GUI at import time. Matched against
# the file's source rather than hardcoded as a list, so a new GUI test file is
# covered the day it is written instead of silently erroring until someone
# updates this.
_TK_DEPENDENT = re.compile(
    r"^\s*(?:import\s+tkinter|from\s+tkinter\b"
    r"|(?:import|from)\s+(?:master_app|pendulastic_app|pendulastic_viewer"
    r"|pendulastic_workbench|video_review_dialog|trial_review|browse_videos"
    r"|longitudinal_dashboard|select_tracking)\b)",
    re.MULTILINE,
)

_skipped_for_tk = []


def pytest_ignore_collect(collection_path, config):
    """Skip the Tk-dependent modules when Tk is not installed at all.

    Returning True here is a collection-time skip, not a silent pass: the
    count and the reason are reported in pytest_report_header, so a headless
    run says plainly which files it did not look at. Without this the same
    files raise ImportError during collection, which pytest reports as errors
    and which make a headless run non-green for a reason that has nothing to
    do with the code under test.
    """
    if tkinter is not None:
        return None
    if collection_path.suffix != ".py" or not collection_path.name.startswith("test_"):
        return None
    try:
        source = collection_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if _TK_DEPENDENT.search(source):
        _skipped_for_tk.append(collection_path.name)
        return True
    return None


def pytest_report_collectionfinish(config, items):
    # Reported here rather than in pytest_report_header: the header is emitted
    # before collection runs, so the count would always read zero.
    if tkinter is None:
        # Phrased as "excluded from collection" rather than "skipped this run":
        # pytest consults pytest_ignore_collect for every file under the
        # rootdir even when a single file was named on the command line, so a
        # targeted run would otherwise claim to have skipped modules it was
        # never asked to collect.
        return (
            f"tkinter is not installed: {len(_skipped_for_tk)} GUI test module(s) "
            f"excluded from collection; all other modules collect normally"
        )
    return None


def pytest_configure(config):
    """Open the session-long Tk root before any test builds an App."""
    global _anchor
    if tkinter is None:
        _anchor = None
        return
    if not os.environ.get("PENDULASTIC_SHOW_TEST_WINDOWS"):
        _hide_new_windows()
    try:
        _anchor = tkinter.Tk()
        _anchor.withdraw()
    except Exception:
        # No display: headless CI, WSL without an X server, a Tk-less build.
        # This must not propagate. An exception out of pytest_configure aborts
        # the whole session at collection time, so a missing display would take
        # down the ~30 pure-computation files (test_pt_score, test_metrics,
        # test_stats, test_reliability_stats, ...) that have no Tk dependency
        # and pass headless today. Without an anchor the GUI tests fail on
        # their own terms, which is the informative outcome; the rest still run.
        _anchor = None
    # Do not let the anchor act as tkinter's default root. Anything created
    # without an explicit master -- PhotoImage above all -- would bind to the
    # anchor's interpreter while the widget using it lives in the App's, and
    # Tk rejects that with: image "pyimage1" doesn't exist. Releasing the slot
    # lets the next Tk() claim it, exactly as without this file.
    tkinter._default_root = None


def pytest_unconfigure(config):
    """Release it once, after the last test has finished."""
    global _anchor
    if _anchor is not None:
        try:
            _anchor.destroy()
        except Exception:
            pass
        _anchor = None
