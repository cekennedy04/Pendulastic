"""Which recording folders resolve to no participant?

The 2026-09-08 clinical session landed in Recordings/MINT_MINT_25 because the
recorder prefixed an already-prefixed participant id. pid_from_dir_name returns
"MINT_25" for that folder, which resolves to no participant, so the session was
invisible to every discovery path until someone went looking by hand.

That failure is silent by construction: nothing downstream can flag a session it
cannot see. A guard at the capture point stops new losses and finds none of the
old ones, so this sweeps what is already on disk.

Read-only.
"""
import os
import re
import sys

BASE = r"C:\Users\cladi\Pendulastic"
sys.path.insert(0, BASE)
os.chdir(BASE)

import participant_paths as pp  # noqa: E402

ROOTS = ["Recordings", "OptiTrack_Recordings"]


def main():
    prefixes = tuple(pp.ALL_PREFIXES)
    print(f"known prefixes: {prefixes}\n")

    suspicious, unresolved, fine = [], [], 0
    for root in ROOTS:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            full = os.path.join(root, name)
            if not os.path.isdir(full):
                continue
            pid = pp.pid_from_dir_name(name)
            n_trials = 0
            for dirpath, _d, files in os.walk(full):
                n_trials += sum(1 for f in files
                                if f.lower().endswith((".csv", ".avi", ".mp4")))
            if pid is None:
                unresolved.append((root, name, n_trials))
                continue
            # A pid that itself still carries a group prefix is the double-prefix
            # bug: the id was already qualified when the folder name was built.
            if any(str(pid).upper().startswith(p.rstrip("_").upper())
                   for p in prefixes):
                suspicious.append((root, name, pid, n_trials))
            elif not re.fullmatch(r"[A-Za-z0-9_\-.]+", str(pid)):
                suspicious.append((root, name, pid, n_trials))
            else:
                fine += 1

    print(f"folders resolving to a clean participant id: {fine}")

    print(f"\nDOUBLE-PREFIXED or malformed ids: {len(suspicious)}")
    for root, name, pid, n in suspicious:
        print(f"  {root}/{name}  ->  pid {pid!r}   ({n} data files)")

    print(f"\nfolders pid_from_dir_name refuses entirely: {len(unresolved)}")
    for root, name, n in unresolved:
        note = "  <- HAS DATA" if n else ""
        print(f"  {root}/{name}   ({n} data files){note}")

    lost = sum(n for _r, _nm, _p, n in suspicious) + \
        sum(n for _r, _nm, n in unresolved)
    print(f"\ndata files sitting behind an id that does not resolve: {lost}")


if __name__ == "__main__":
    main()
