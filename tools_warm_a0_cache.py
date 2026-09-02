"""Fill generate_paper_results_analysis's A0 cache in parallel.

The production script computes this serially at ~22 s a trial, which is more
wall clock than this environment gives a single run -- attempts kept being
killed after banking ~10 trials. This writes exactly the same keys and values
into exactly the same file, using three worker processes, so the production run
afterwards finds everything cached and reaches its own loop immediately.

It does NOT change how a value is computed: each worker calls the same
load_optitrack_detailed + compute_pt_params the script calls, and the key format
is duplicated from it deliberately -- if that format drifts, the production run
recomputes rather than reading something wrong, because the keys simply will not
match. Three workers, not more: scipy OOM'd on this machine at higher counts.
"""
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

BASE = r"C:\Users\cladi\Pendulastic"
sys.path.insert(0, BASE)

CACHE = os.path.join(BASE, "Model_Analysis_Outputs", "_cache",
                     "paper_a0_by_trial.json")


def key_for(path, version):
    st = os.stat(path)
    return f"{version}|{path}|{st.st_size}|{int(st.st_mtime)}"


def compute(path):
    os.chdir(BASE)
    import pendulastic_pt_score as pts
    try:
        t, angle, _q = pts.load_optitrack_detailed(path)
        p = pts.compute_pt_params(t, angle)
        val = None if p is None else p.get("A0_deg")
        if val is not None:
            val = float(val)
            if val != val:          # NaN
                val = None
    except Exception:
        val = None
    return path, val


def main():
    os.chdir(BASE)
    import participant_paths as pp
    import generate_paper_results_analysis as gpra

    version = gpra._CACHE_VERSION
    paths = pp.glob_participants("OptiTrack_Recordings", "*", "pre", "**",
                                 "trial_*_optitrack.csv", recursive=True)
    cache = {}
    if os.path.isfile(CACHE):
        try:
            cache = json.load(open(CACHE, encoding="utf-8"))
        except ValueError:
            cache = {}
    todo = []
    for p in paths:
        try:
            k = key_for(p, version)
        except OSError:
            continue
        if k not in cache:
            todo.append((p, k))
    print(f"{len(paths)} trials, {len(cache)} cached, {len(todo)} to compute",
          flush=True)

    done = 0
    with ProcessPoolExecutor(max_workers=3) as ex:
        for path, val in ex.map(compute, [p for p, _ in todo], chunksize=1):
            k = dict(todo)[path]
            cache[k] = val
            done += 1
            tmp = CACHE + ".tmp"
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cache, f)
            os.replace(tmp, CACHE)
            if done % 10 == 0:
                print(f"  {done}/{len(todo)}", flush=True)
    print(f"cache now holds {len(cache)} entries", flush=True)


if __name__ == "__main__":
    main()
