"""Provenance (plan D2/B14): a library, a session and a score must all come from the same scorer and model.

None of these need the built scenario library: each points OUT and SESS at a temporary directory.
"""
import json
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from neuromech import build as B  # noqa: E402
from neuromech import session as SS  # noqa: E402
import pendulastic_pt_score as P  # noqa: E402


@pytest.fixture
def lib(tmp_path, monkeypatch):
    out = tmp_path / "out"; out.mkdir()
    monkeypatch.setattr(B, "OUT", str(out)); monkeypatch.setattr(SS, "OUT", str(out))
    monkeypatch.setattr(SS, "SESS", str(tmp_path / "sessions"))
    return out


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(obj), encoding="utf-8")


def test_provenance_names_the_scorer_formula():
    p = B.provenance()
    assert p["score_formula_id"] == P.SCORE_FORMULA_ID
    assert len(p["model_hash"]) == 12 and p == B.provenance()


def test_model_hash_ignores_line_endings_but_not_content(tmp_path, monkeypatch):
    for rel in B.PROVENANCE_SOURCES:
        dst = tmp_path / rel; dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(os.path.join(B.ROOT, rel), dst)
    monkeypatch.setattr(B, "ROOT", str(tmp_path))
    base = B.provenance()["model_hash"]
    f = tmp_path / B.PROVENANCE_SOURCES[0]
    lf = f.read_bytes().replace(b"\r\n", b"\n")
    f.write_bytes(lf.replace(b"\n", b"\r\n"))
    assert B.provenance()["model_hash"] == base, "a CRLF checkout of the same source must hash the same"
    f.write_bytes(lf + b"# edited\n")
    assert B.provenance()["model_hash"] != base


def test_a_changed_formula_id_is_a_mismatch(monkeypatch):
    before = B.provenance()
    monkeypatch.setattr(P, "SCORE_FORMULA_ID", "some-other-formula")
    assert B.provenance_mismatch(before, B.provenance()) == ["score_formula_id"]
    assert B.provenance_mismatch(None, B.provenance()) == list(B.PROVENANCE_KEYS)


def test_plan_refuses_a_library_built_under_another_scorer(lib):
    _write(lib / "provenance.json", dict(B.provenance(), score_formula_id="old-formula"))
    with pytest.raises(SystemExit, match="recalibrate"):
        SS.plan("S1", sids=["A-P0"])


def test_plan_refuses_a_library_with_no_provenance(lib):
    with pytest.raises(SystemExit, match="recalibrate"):
        SS.plan("S1", sids=["A-P0"])


def test_score_refuses_a_session_planned_under_another_scorer(lib, tmp_path):
    _write(lib / "provenance.json", B.provenance())
    _write(tmp_path / "sessions" / "S1" / "session.json", {"provenance": dict(B.provenance(), model_hash="000000000000")})
    with pytest.raises(SystemExit, match="refusing to score.*model_hash"):
        SS.score("S1", str(tmp_path))


def test_score_can_be_forced_past_a_mismatch(lib, tmp_path):
    _write(lib / "provenance.json", B.provenance())
    _write(tmp_path / "sessions" / "S1" / "session.json", {})
    # past the provenance check it goes on to read the session's key, which this fixture does not write
    with pytest.raises(FileNotFoundError, match="key.csv"):
        SS.score("S1", str(tmp_path), allow_provenance_mismatch=True)
