"""Write Model_Analysis_Outputs/manifest.json: what was generated, and from what.

Why this exists
---------------
Model_Analysis_Outputs/ is gitignored (see .gitignore) and runs to ~239 MB, most
of it PNGs. Tracking that would bloat the history for binaries that are
regenerable by definition. But leaving it entirely unrecorded cost us a real
answer on 2026-09-02: paper_results_analysis_trials.csv was overwritten before
its trial list could be diffed against the 2026-08-19 run, so the cause of an
ICC drop could only be inferred rather than measured.

The manifest is the middle path. It is small, text, and committed -- so the
history says which commit produced which numbers, and a later reader can tell a
change in the code from a change in the data.

What it records, and what it deliberately does not
--------------------------------------------------
For every CSV: the row count, the column names, and a content hash. Shape and
hash together answer "did this change, and did it change size or just values",
which is the question asked when a figure moves. For every figure: name, size
and mtime -- enough to say a figure was regenerated, without pretending to
describe a picture.

It records HEADLINE METRICS by name from the tables that carry them, because a
hash tells you something changed and not what. The extractors are deliberately
narrow and fail soft: a manifest that refuses to write because one table moved
is worse than one that records the rest and says which part it could not read.

It does NOT record every number in every table. That is what the tables are for.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import subprocess
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(BASE_DIR, "Model_Analysis_Outputs")
MANIFEST = os.path.join(OUT_ROOT, "manifest.json")

# Directories under the output root that are not generated results.
SKIP_DIRS = {"_cache", "_archive"}


def _git(*args, default=""):
    """Best-effort git. A checkout without git still stamps, it just cannot
    claim a revision -- and says so rather than inventing one."""
    try:
        return subprocess.run(["git", *args], cwd=BASE_DIR, capture_output=True,
                              text=True, check=True).stdout.strip() or default
    except Exception:
        return default


def _sha256(path, limit=None):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            if limit and h.block_size and fh.tell() > limit:
                break
    return h.hexdigest()[:16]


def _iter_outputs():
    for root, dirs, files in os.walk(OUT_ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(files):
            if name == "manifest.json":
                continue
            yield os.path.join(root, name)


def _describe_csv(path):
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            header = next(reader, [])
            n = sum(1 for _ in reader)
        return {"rows": n, "columns": header}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


# ── headline metrics ─────────────────────────────────────────────────────────
# Narrow by design: each knows one table and returns {} if that table is not
# shaped the way it expects, so a manifest still writes when one moves.

def _metrics_paper_trials(path):
    """IMU-vs-OptiTrack agreement, and how much of the corpus it rests on.

    n_measurable is the number that matters and the one that used to be
    invisible: before 48d7c96 an unmeasurable optical trial scored 0.0 for
    every parameter and was averaged in as an observation.
    """
    params = ["R2n", "N", "phi_max_ratio", "omega_max_n", "f", "area_ratio",
              "omega_min_n"]
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    except Exception:
        return {}
    if not rows or f"opti_{params[0]}" not in rows[0]:
        return {}

    def measurable(r, side):
        for p in params:
            try:
                if not math.isfinite(float(r[f"{side}_{p}"])):
                    return False
            except (TypeError, ValueError, KeyError):
                return False
        return True

    both = [r for r in rows if measurable(r, "opti") and measurable(r, "imu")]
    return {
        "n_trials": len(rows),
        "n_measurable_both": len(both),
        "n_opti_unmeasurable": sum(1 for r in rows if not measurable(r, "opti")),
        "n_imu_unmeasurable": sum(1 for r in rows if not measurable(r, "imu")),
    }


def _metrics_leg_labels(path):
    """Spasticity label counts by level and by provenance. The provenance
    breakdown is the point: 'unknown' means something different when its source
    is a0-threshold-invalid than when it is no-data."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.DictReader(fh))
    except Exception:
        return {}
    if not rows or "spasticity" not in rows[0]:
        return {}
    levels, sources = {}, {}
    for r in rows:
        levels[r["spasticity"]] = levels.get(r["spasticity"], 0) + 1
        sources[r.get("source", "?")] = sources.get(r.get("source", "?"), 0) + 1
    return {"n_legs": len(rows), "by_level": levels, "by_source": sources}


EXTRACTORS = {
    "paper_results_analysis_trials.csv": _metrics_paper_trials,
    "leg_labels.csv": _metrics_leg_labels,
}


def build():
    files, metrics, unreadable = {}, {}, []
    for path in _iter_outputs():
        rel = os.path.relpath(path, OUT_ROOT).replace("\\", "/")
        try:
            st = os.stat(path)
        except OSError:
            continue
        entry = {
            "bytes": st.st_size,
            "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc)
                                .strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if rel.lower().endswith(".csv"):
            entry["sha256_16"] = _sha256(path)
            entry.update(_describe_csv(path))
            fn = EXTRACTORS.get(os.path.basename(rel))
            if fn:
                got = fn(path)
                if got:
                    metrics[rel] = got
                else:
                    unreadable.append(rel)
        files[rel] = entry

    dirty = _git("status", "--porcelain")
    return {
        "generated_at": datetime.now(timezone.utc)
                                .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git": {
            "commit": _git("rev-parse", "HEAD", default="unknown"),
            "short": _git("rev-parse", "--short=12", "HEAD", default="unknown"),
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD",
                           default="unknown"),
            # Recorded rather than refused: stamping a dirty tree is normal
            # mid-session, but a reader must know the commit does not fully
            # describe what produced these numbers.
            "working_tree_clean": not bool(dirty),
        },
        "counts": {
            "files": len(files),
            "csv": sum(1 for k in files if k.lower().endswith(".csv")),
            "figures": sum(1 for k in files
                           if k.lower().endswith((".png", ".pdf", ".svg"))),
            "total_bytes": sum(v["bytes"] for v in files.values()),
        },
        "headline_metrics": metrics,
        "tables_not_readable_by_an_extractor": unreadable,
        "files": files,
    }


def main():
    if not os.path.isdir(OUT_ROOT):
        print(f"no {OUT_ROOT}; nothing to stamp")
        return 1
    manifest = build()
    os.makedirs(OUT_ROOT, exist_ok=True)
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, MANIFEST)

    c = manifest["counts"]
    g = manifest["git"]
    print(f"stamped {c['files']} files ({c['csv']} csv, {c['figures']} figures, "
          f"{c['total_bytes'] / 1e6:.0f} MB)")
    print(f"  commit {g['short']} on {g['branch']}"
          f"{'' if g['working_tree_clean'] else '  (WORKING TREE DIRTY)'}")
    for rel, m in sorted(manifest["headline_metrics"].items()):
        print(f"  {rel}: {m}")
    if manifest["tables_not_readable_by_an_extractor"]:
        print(f"  not readable by their extractor: "
              f"{manifest['tables_not_readable_by_an_extractor']}")
    print(f"-> {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
