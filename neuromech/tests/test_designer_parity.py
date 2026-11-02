"""The C# PtScore port (neuromech/designer/PtScore.cs) must score every parity case exactly as
neuromech.build.score_angle does: floats to 1e-6 relative, counts / extrema / refusals exactly.

Runs neuromech/designer/parity/run_parity.py's comparison. Skipped where it cannot run: no
.NET 3.5 compiler (non-Windows, or the 3.5 feature not installed) or no built scenario set
(python -m neuromech.build has not been run, so neuromech/out/ is missing).
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PARITY = os.path.join(HERE, "..", "designer", "parity")
OUT = os.path.join(HERE, "..", "out")
sys.path.insert(0, PARITY)

import run_parity  # noqa: E402


@pytest.mark.skipif(not os.path.exists(run_parity.CSC), reason="csc v3.5 not installed")
@pytest.mark.skipif(not (os.path.isdir(os.path.join(OUT, "traces")) and os.path.isdir(os.path.join(OUT, "setpoints"))),
                    reason="neuromech/out not built (python -m neuromech.build)")
def test_csharp_port_matches_python_scorer():
    ok, text = run_parity.run(regen=False, repeats=1)
    print(text)
    assert ok, text
