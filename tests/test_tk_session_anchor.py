"""Guards the session-long Tk anchor that tests/conftest.py installs.

The anchor keeps Tcl's global library state alive for the whole pytest
session, so no individual App teardown is ever the last interpreter out.
Without it the GUI suite fails intermittently with a TclError naming a Tcl
library file that demonstrably exists on disk (icons.tcl, init.tcl,
auto.tcl), the file varying run to run.

That failure is load- and timing-dependent -- 12 rounds of bare Tk() and of
ttk-widget churn in a fresh process do NOT reproduce it, so there is no
cheap deterministic behavioural test. What IS worth pinning is the
mechanism, because the way this regressed in practice was the fix simply
not being present: conftest.py lived on fix/app-teardown-imu-port-release
and was absent from this branch and from main, and test_analysis_panel.py
went back to failing 1-2 tests per run with shifting names.
"""
import tkinter


def test_session_anchor_interpreter_is_open():
    """conftest.py must hold one Tk interpreter open for the whole session."""
    import conftest
    assert conftest._anchor is not None, (
        "no session anchor -- tests/conftest.py is missing or its pytest_configure "
        "failed; the GUI suite will fail intermittently with TclError on Tcl "
        "library files that exist on disk")
    assert conftest._anchor.tk.call("info", "tclversion")


def test_session_anchor_does_not_own_the_default_root():
    """The anchor must not claim tkinter._default_root.

    If it does, anything created without an explicit master -- PhotoImage
    above all -- binds to the anchor's interpreter while its widget lives in
    the App's, and Tk rejects that with: image "pyimage1" doesn't exist.
    """
    import conftest
    assert tkinter._default_root is not conftest._anchor
