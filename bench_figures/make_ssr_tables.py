"""Step 1 of the bench analysis: turn the tracker's per-step SSR tab into ssr_steps.csv,
and join the encoder-QA overshoot table to each recording's PID config -> ssr_qa_joined.csv.

    python bench_figures/make_ssr_tables.py [--tracker PATH] [--qa PATH]

Inputs (defaults):
  tracker : C:/Users/cladi/Downloads/Bench_SSR_Static_FILLED_DYNAMIC.xlsx (or the CK Final tracker — same tab)  (sheet "SSR Step-Response")
  qa      : C:/Users/cladi/Downloads/ssr_encoder_qa.csv                       (overshoot/ring per encoder trial)
Outputs: bench_figures/ssr_steps.csv, bench_figures/ssr_qa_joined.csv
"""
import argparse, os
import pandas as pd, openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser()
ap.add_argument("--tracker", default=r"C:\Users\cladi\Downloads\Bench_SSR_Static_FILLED_DYNAMIC.xlsx")
ap.add_argument("--qa", default=r"C:\Users\cladi\Downloads\ssr_encoder_qa.csv")
a = ap.parse_args()

ws = openpyxl.load_workbook(a.tracker, data_only=True)["SSR Step-Response"]
rows = list(ws.iter_rows(values_only=True))
hdr = [str(h) for h in rows[3]]                       # row 4 of the sheet is the header
df = pd.DataFrame(rows[4:], columns=hdr)
df = df[pd.to_numeric(df["Encoder Δ"], errors="coerce").notna()]
for c in ["P", "I", "D", "A (deg)", "Step speed", "Step #", "From°", "To°", "Encoder Δ", "IMU Δ (gyro)", "Drift °/s", "Δ error", "|Δ err|", "Encoder trial", "Capture rank", "Rec #"]:
    df[c] = pd.to_numeric(df[c], errors="coerce")
# derive the error columns from the two deltas (the sheet stores them as formulas, which may not be cached)
df["Δ error"] = df["IMU Δ (gyro)"] - df["Encoder Δ"]; df["|Δ err|"] = df["Δ error"].abs(); df["% error"] = df["Δ error"] / df["Encoder Δ"]
df.to_csv(os.path.join(HERE, "ssr_steps.csv"), index=False, encoding="utf-8")
clean = df.dropna(subset=["Encoder Δ", "IMU Δ (gyro)"])
print(f"ssr_steps.csv: {len(df)} rows, {len(clean)} with both deltas, {clean['Encoder trial'].nunique()} recordings; mean|err| {clean['|Δ err|'].mean():.3f} deg")

if os.path.exists(a.qa):
    qa = pd.read_csv(a.qa)
    cfg = df.groupby("Encoder trial")[["P", "I", "D", "Step speed", "A (deg)"]].first().reset_index()
    j = qa.merge(cfg, left_on="trial", right_on="Encoder trial", how="left")
    j.to_csv(os.path.join(HERE, "ssr_qa_joined.csv"), index=False, encoding="utf-8")
    print(f"ssr_qa_joined.csv: {len(qa)} QA rows, {j['P'].notna().sum()} matched to a PID config")
else:
    print("no QA csv found -> skipping ssr_qa_joined.csv (pid_speed / pid_explainer figures need it)")
