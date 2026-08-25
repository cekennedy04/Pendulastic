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
import tkinter

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


def pytest_configure(config):
    """Open the session-long Tk root before any test builds an App."""
    global _anchor
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
