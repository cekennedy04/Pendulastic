import clr
clr.AddReference("NMotive")
from NMotive import Trajectorizer, TrajectorizerOption


def ProcessTake(take, progress):

    # ── Step 1: Reconstruct ────────────────────────────────────────────────
    progress.SetMessage("Reconstructing 3D markers...")
    progress.SetProgress(float(0.05))

    traj = Trajectorizer(progress)
    result = traj.Process(take, TrajectorizerOption.Reconstruct)

    if not result.Success:
        return result   # will show error in batch processor UI

    progress.SetProgress(float(0.4))
    progress.SetMessage("Reconstruction complete. Reading markers...")

    # ── Step 2: Read marker positions frame by frame ───────────────────────
    scene    = take.Scene
    markers  = list(scene.AllMarkers)
    fr_range = take.FullFrameRange
    fps      = take.FrameRate

    if not markers:
        return type(result)(False, "Reconstruction succeeded but no markers found in scene.")

    # Determine frame range
    start = int(fr_range.Start) if hasattr(fr_range, "Start") else 0
    end   = int(fr_range.End)   if hasattr(fr_range, "End")   else start

    # Build output path without os module
    full_path  = take.FileName
    last_sep   = max(full_path.rfind("\\"), full_path.rfind("/"))
    folder     = full_path[:last_sep]
    filename   = full_path[last_sep + 1:]
    dot        = filename.rfind(".")
    stem       = filename[:dot] if dot >= 0 else filename
    output_csv = folder + "\\" + stem + "_markers.csv"

    # ── Step 3: Write CSV ──────────────────────────────────────────────────
    progress.SetMessage("Writing marker CSV...")
    n_markers = len(markers)

    # Header row: frame, time_sec, m0_x, m0_y, m0_z, m1_x, ...
    header = "frame,time_sec"
    for mi in range(n_markers):
        header += ",m%d_x,m%d_y,m%d_z" % (mi, mi, mi)

    lines = [header]
    total_frames = max(1, end - start + 1)

    for frame_idx in range(start, end + 1):
        t_sec = (frame_idx - start) / fps
        row   = "%d,%.6f" % (frame_idx, t_sec)
        for mk in markers:
            try:
                pos = mk.Position(frame_idx)
                row += ",%.6f,%.6f,%.6f" % (pos.X, pos.Y, pos.Z)
            except Exception:
                row += ",,,"
        lines.append(row)

        if (frame_idx - start) % 200 == 0:
            pct = 0.4 + 0.55 * (frame_idx - start) / total_frames
            progress.SetProgress(float(pct))

    with open(output_csv, "w") as fh:
        fh.write("\n".join(lines))

    take.Save()

    progress.SetProgress(float(1.0))
    progress.SetMessage("Done: %d frames, %d markers -> %s" % (
        total_frames, n_markers, output_csv))

    return type(result)(True, "Exported marker CSV: " + output_csv)
