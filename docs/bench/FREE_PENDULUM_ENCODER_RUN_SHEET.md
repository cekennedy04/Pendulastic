# Free-Pendulum Encoder Run Sheet: phone angle against the encoder, at several weights

Plan: `docs/plans/2026-11-18-001-plan-free-pendulum-encoder-weight-validation.md`. Read
sections 3 and 8 of the plan before the first session. Print this sheet, or the section for
today's session.

**What this session measures.** The angle the shipped app computes, against an encoder on the
pivot of a gravity pendulum, with the phone off the rotation axis. No MAS grade or clinical
statement comes from it. Do not quote a PT score from here as a severity.

## Session header (fill in every session)

| Field | Value |
|---|---|
| Date / operator | |
| Session (P0, S1 … S7) | |
| Phone model, OS, browser | |
| App footer: build / algorithm | |
| Phone radius from pivot axis (m) | |
| Phone orientation (photo taken?) | |
| Encoder model / counts per rev | |
| Log sample rate (Hz) / file prefix | `pf____` |
| Encoder zero at hanging rest (deg) | |
| Pivot-axis tilt from level (deg) | |
| Analyser commit | |

Measured weights (scale, ±10 g), carrier at 0.38 m from the pivot axis:

| ID | Nominal added mass | Measured (kg) | Radius (m) |
|---|---|---|---|
| W0 | none | n/a | n/a |
| W1 | 1.0 kg | | |
| W2 | 3.0 kg | | |
| W3 | 4.5 kg | | |
| W4 | 6.0 kg | | |

## Bring

- [ ] The phone for this session, charged. iPhone: Low Power Mode off, Auto-Lock Never, Do Not
      Disturb on. Android: battery saver off, screen timeout at maximum, Do Not Disturb on.
- [ ] Phone clamp and tether. Stage 2: the participant strap.
- [ ] Mass carrier, plates, scale, digital inclinometer or level, tape measure, camera.
- [ ] Laptop with the analyser checked out and `npm run build:wasm` already run in `webapp/`.
- [ ] This sheet and `2026-11-18-free-pendulum-rig-log-template.csv` (one copy per session).

## Setup (10 min)

- [ ] Frame clamped to the bench. Swing path clear to ±75°. Floor below clear.
- [ ] Pivot axis level (write the tilt above). Arm swings freely with no rub.
- [ ] Phone clamped at the session radius, in the shin orientation. Tether on. No cable.
      Photograph the mount.
- [ ] Arm hanging still: zero the encoder, write the zero above.
- [ ] Encoder check, first session of each day: clamp the arm at 0° and at one peg; encoder
      and inclinometer agree within 0.3°.
- [ ] Open the app in the browser at `https://pendulastic-app.vercel.app/?research=1`. Do
      everything in the browser tab, not a home-screen icon, so raw and scored trials export
      together. Open a session. Write the footer build and algorithm above.
- [ ] Encoder log prefix set; trial numbers continue through the day and are never reused.

## Every swing

Follow the printed order. Do not reorder.

**Raw mode (`raw`)**
1. Arm hanging still. Phone: home tile **Record (raw)** → Start.
2. Start the encoder log. Write its file number in the row.
3. Raise the arm slowly to the peg for this row. Hold it still against the peg for **5 s**.
4. Let go cleanly. Hands away, below the arm. Do not push.
5. Let it ring down. If it is still swinging at **60 s**, steady it gently by hand and write
   `60s` under *Stopped by*; otherwise write `auto`.
6. Arm still for **6 s**. Then **one sharp knuckle tap** on the arm. Wait 2 s.
7. Stop the phone. The status must read `saved`. Stop the encoder log **last**.

**Quick Test mode (`Quick Test`)**
1. Arm hanging still. Phone: **Quick Test → Start**.
2. Start the encoder log. Write its file number in the row.
3. Raise the arm slowly to the peg. Hold **5 s**. Let go cleanly.
4. **No tap, at any point.**
5. Wait for the app to stop on its own (`auto`). If it is still swinging at **60 s**, press
   Stop and write `60s`.
6. Stop the encoder log **last**. Write the app result: `scored`, `NOT SCORABLE` or
   `ERROR: <text>`.

**Every row, including bad ones, gets a line in the rig log.** If something went wrong that you
saw at the time (recorder not started, arm bumped, clamp slipped, release fumbled), write it in
Notes **now** and repeat that row at the end of the session. Do not judge a swing by its result.

**Changing weight (rows where the condition is in bold):** arm hanging, lock the carrier at
0.38 m, check the clamp bolts, check the phone clamp has not moved, let the arm settle, confirm
the encoder still reads within 0.2° of zero.

## Conditions

| ID | Meaning |
|---|---|
| W0 … W4 | Added mass none, 1.0, 3.0, 4.5, 6.0 kg at 0.38 m; phone at 0.30 m |
| R10, R20, R40 | W3 loaded; phone at 0.10, 0.20, 0.40 m |
| YAW15 | W3 loaded; phone at 0.30 m, turned 15° on the clamp about the axis through the screen |
| ROLL90 | W3 loaded; phone at 0.30 m, turned 90° (landscape) |
| L0 | Leg phantom, phone strapped as on a participant |
| L1, L2 | Phantom plus a 1 kg / 2 kg ankle cuff |
| L3 | Phantom, phone taken off and strapped on again |
| L4 | Phantom, strap one finger loose |

## P0: pilot (not analysed for acceptance)

- [ ] Encoder against inclinometer at 0, 30, 60, 90°: ____ / ____ / ____ / ____ (within 0.3°).
- [ ] One 40° raw swing per weight. Ring-down time to still: W0 ____ s, W1 ____ s, W2 ____ s,
      W3 ____ s, W4 ____ s. Measured frequency: ____ / ____ / ____ / ____ / ____ Hz.
- [ ] End tap visible in the encoder trace and in the phone gyro: yes / no.
- [ ] Tap ring frequency in the phone gyro with W4 loaded: ____ Hz (want above 15 Hz).
- [ ] Both phones: one Quick Test swing captured, scored and exported, and the export holds the
      raw samples. iPhone: yes / no. Android: yes / no.
- [ ] Phone sample rate: iPhone ____ Hz, Android ____ Hz.
- [ ] Analyser runs end to end on these swings. Analyser and criteria frozen at commit ________.

## Session order tables

Orders were generated from seed `20261118` (code at the end). S1 to S4 are the weight matrix
(S1, S3 iPhone; S2, S4 Android). S5 is the radius and orientation session (iPhone). S6 and S7
are the leg phantom (iPhone, Android). A bold condition marks a change of weight or mount.

### S1

| Seq | Trial ID | Condition | Release (deg) | Mode | Encoder file # | App result | Stopped by | Notes |
|---|---|---|---|---|---|---|---|---|
| 1 | `PF-S1-W2-A20-R-01` | **W2** | 20 | raw | | | | |
| 2 | `PF-S1-W2-A40-R-02` | W2 | 40 | raw | | | | |
| 3 | `PF-S1-W2-A60-R-03` | W2 | 60 | raw | | | | |
| 4 | `PF-S1-W2-A40-Q-04` | W2 | 40 | Quick Test | | | | |
| 5 | `PF-S1-W2-A60-Q-05` | W2 | 60 | Quick Test | | | | |
| 6 | `PF-S1-W2-A20-Q-06` | W2 | 20 | Quick Test | | | | |
| 7 | `PF-S1-W1-A60-Q-07` | **W1** | 60 | Quick Test | | | | |
| 8 | `PF-S1-W1-A20-Q-08` | W1 | 20 | Quick Test | | | | |
| 9 | `PF-S1-W1-A40-Q-09` | W1 | 40 | Quick Test | | | | |
| 10 | `PF-S1-W1-A60-R-10` | W1 | 60 | raw | | | | |
| 11 | `PF-S1-W1-A40-R-11` | W1 | 40 | raw | | | | |
| 12 | `PF-S1-W1-A20-R-12` | W1 | 20 | raw | | | | |
| 13 | `PF-S1-W3-A20-R-13` | **W3** | 20 | raw | | | | |
| 14 | `PF-S1-W3-A40-R-14` | W3 | 40 | raw | | | | |
| 15 | `PF-S1-W3-A60-R-15` | W3 | 60 | raw | | | | |
| 16 | `PF-S1-W3-A20-Q-16` | W3 | 20 | Quick Test | | | | |
| 17 | `PF-S1-W3-A60-Q-17` | W3 | 60 | Quick Test | | | | |
| 18 | `PF-S1-W3-A40-Q-18` | W3 | 40 | Quick Test | | | | |
| 19 | `PF-S1-W0-A20-Q-19` | **W0** | 20 | Quick Test | | | | |
| 20 | `PF-S1-W0-A60-Q-20` | W0 | 60 | Quick Test | | | | |
| 21 | `PF-S1-W0-A40-Q-21` | W0 | 40 | Quick Test | | | | |
| 22 | `PF-S1-W0-A40-R-22` | W0 | 40 | raw | | | | |
| 23 | `PF-S1-W0-A60-R-23` | W0 | 60 | raw | | | | |
| 24 | `PF-S1-W0-A20-R-24` | W0 | 20 | raw | | | | |
| 25 | `PF-S1-W4-A40-Q-25` | **W4** | 40 | Quick Test | | | | |
| 26 | `PF-S1-W4-A20-Q-26` | W4 | 20 | Quick Test | | | | |
| 27 | `PF-S1-W4-A60-Q-27` | W4 | 60 | Quick Test | | | | |
| 28 | `PF-S1-W4-A60-R-28` | W4 | 60 | raw | | | | |
| 29 | `PF-S1-W4-A20-R-29` | W4 | 20 | raw | | | | |
| 30 | `PF-S1-W4-A40-R-30` | W4 | 40 | raw | | | | |

### S2

| Seq | Trial ID | Condition | Release (deg) | Mode | Encoder file # | App result | Stopped by | Notes |
|---|---|---|---|---|---|---|---|---|
| 1 | `PF-S2-W3-A60-Q-01` | **W3** | 60 | Quick Test | | | | |
| 2 | `PF-S2-W3-A20-Q-02` | W3 | 20 | Quick Test | | | | |
| 3 | `PF-S2-W3-A40-Q-03` | W3 | 40 | Quick Test | | | | |
| 4 | `PF-S2-W3-A40-R-04` | W3 | 40 | raw | | | | |
| 5 | `PF-S2-W3-A20-R-05` | W3 | 20 | raw | | | | |
| 6 | `PF-S2-W3-A60-R-06` | W3 | 60 | raw | | | | |
| 7 | `PF-S2-W4-A60-Q-07` | **W4** | 60 | Quick Test | | | | |
| 8 | `PF-S2-W4-A20-Q-08` | W4 | 20 | Quick Test | | | | |
| 9 | `PF-S2-W4-A40-Q-09` | W4 | 40 | Quick Test | | | | |
| 10 | `PF-S2-W4-A40-R-10` | W4 | 40 | raw | | | | |
| 11 | `PF-S2-W4-A60-R-11` | W4 | 60 | raw | | | | |
| 12 | `PF-S2-W4-A20-R-12` | W4 | 20 | raw | | | | |
| 13 | `PF-S2-W0-A60-Q-13` | **W0** | 60 | Quick Test | | | | |
| 14 | `PF-S2-W0-A40-Q-14` | W0 | 40 | Quick Test | | | | |
| 15 | `PF-S2-W0-A20-Q-15` | W0 | 20 | Quick Test | | | | |
| 16 | `PF-S2-W0-A20-R-16` | W0 | 20 | raw | | | | |
| 17 | `PF-S2-W0-A40-R-17` | W0 | 40 | raw | | | | |
| 18 | `PF-S2-W0-A60-R-18` | W0 | 60 | raw | | | | |
| 19 | `PF-S2-W1-A20-Q-19` | **W1** | 20 | Quick Test | | | | |
| 20 | `PF-S2-W1-A40-Q-20` | W1 | 40 | Quick Test | | | | |
| 21 | `PF-S2-W1-A60-Q-21` | W1 | 60 | Quick Test | | | | |
| 22 | `PF-S2-W1-A40-R-22` | W1 | 40 | raw | | | | |
| 23 | `PF-S2-W1-A20-R-23` | W1 | 20 | raw | | | | |
| 24 | `PF-S2-W1-A60-R-24` | W1 | 60 | raw | | | | |
| 25 | `PF-S2-W2-A60-Q-25` | **W2** | 60 | Quick Test | | | | |
| 26 | `PF-S2-W2-A40-Q-26` | W2 | 40 | Quick Test | | | | |
| 27 | `PF-S2-W2-A20-Q-27` | W2 | 20 | Quick Test | | | | |
| 28 | `PF-S2-W2-A60-R-28` | W2 | 60 | raw | | | | |
| 29 | `PF-S2-W2-A20-R-29` | W2 | 20 | raw | | | | |
| 30 | `PF-S2-W2-A40-R-30` | W2 | 40 | raw | | | | |

### S3

| Seq | Trial ID | Condition | Release (deg) | Mode | Encoder file # | App result | Stopped by | Notes |
|---|---|---|---|---|---|---|---|---|
| 1 | `PF-S3-W4-A60-Q-01` | **W4** | 60 | Quick Test | | | | |
| 2 | `PF-S3-W4-A40-Q-02` | W4 | 40 | Quick Test | | | | |
| 3 | `PF-S3-W4-A20-Q-03` | W4 | 20 | Quick Test | | | | |
| 4 | `PF-S3-W4-A40-R-04` | W4 | 40 | raw | | | | |
| 5 | `PF-S3-W4-A60-R-05` | W4 | 60 | raw | | | | |
| 6 | `PF-S3-W4-A20-R-06` | W4 | 20 | raw | | | | |
| 7 | `PF-S3-W3-A60-R-07` | **W3** | 60 | raw | | | | |
| 8 | `PF-S3-W3-A40-R-08` | W3 | 40 | raw | | | | |
| 9 | `PF-S3-W3-A20-R-09` | W3 | 20 | raw | | | | |
| 10 | `PF-S3-W3-A60-Q-10` | W3 | 60 | Quick Test | | | | |
| 11 | `PF-S3-W3-A20-Q-11` | W3 | 20 | Quick Test | | | | |
| 12 | `PF-S3-W3-A40-Q-12` | W3 | 40 | Quick Test | | | | |
| 13 | `PF-S3-W0-A60-Q-13` | **W0** | 60 | Quick Test | | | | |
| 14 | `PF-S3-W0-A40-Q-14` | W0 | 40 | Quick Test | | | | |
| 15 | `PF-S3-W0-A20-Q-15` | W0 | 20 | Quick Test | | | | |
| 16 | `PF-S3-W0-A40-R-16` | W0 | 40 | raw | | | | |
| 17 | `PF-S3-W0-A60-R-17` | W0 | 60 | raw | | | | |
| 18 | `PF-S3-W0-A20-R-18` | W0 | 20 | raw | | | | |
| 19 | `PF-S3-W1-A60-Q-19` | **W1** | 60 | Quick Test | | | | |
| 20 | `PF-S3-W1-A20-Q-20` | W1 | 20 | Quick Test | | | | |
| 21 | `PF-S3-W1-A40-Q-21` | W1 | 40 | Quick Test | | | | |
| 22 | `PF-S3-W1-A40-R-22` | W1 | 40 | raw | | | | |
| 23 | `PF-S3-W1-A60-R-23` | W1 | 60 | raw | | | | |
| 24 | `PF-S3-W1-A20-R-24` | W1 | 20 | raw | | | | |
| 25 | `PF-S3-W2-A20-Q-25` | **W2** | 20 | Quick Test | | | | |
| 26 | `PF-S3-W2-A40-Q-26` | W2 | 40 | Quick Test | | | | |
| 27 | `PF-S3-W2-A60-Q-27` | W2 | 60 | Quick Test | | | | |
| 28 | `PF-S3-W2-A20-R-28` | W2 | 20 | raw | | | | |
| 29 | `PF-S3-W2-A60-R-29` | W2 | 60 | raw | | | | |
| 30 | `PF-S3-W2-A40-R-30` | W2 | 40 | raw | | | | |

### S4

| Seq | Trial ID | Condition | Release (deg) | Mode | Encoder file # | App result | Stopped by | Notes |
|---|---|---|---|---|---|---|---|---|
| 1 | `PF-S4-W4-A60-Q-01` | **W4** | 60 | Quick Test | | | | |
| 2 | `PF-S4-W4-A20-Q-02` | W4 | 20 | Quick Test | | | | |
| 3 | `PF-S4-W4-A40-Q-03` | W4 | 40 | Quick Test | | | | |
| 4 | `PF-S4-W4-A20-R-04` | W4 | 20 | raw | | | | |
| 5 | `PF-S4-W4-A40-R-05` | W4 | 40 | raw | | | | |
| 6 | `PF-S4-W4-A60-R-06` | W4 | 60 | raw | | | | |
| 7 | `PF-S4-W2-A60-R-07` | **W2** | 60 | raw | | | | |
| 8 | `PF-S4-W2-A40-R-08` | W2 | 40 | raw | | | | |
| 9 | `PF-S4-W2-A20-R-09` | W2 | 20 | raw | | | | |
| 10 | `PF-S4-W2-A40-Q-10` | W2 | 40 | Quick Test | | | | |
| 11 | `PF-S4-W2-A60-Q-11` | W2 | 60 | Quick Test | | | | |
| 12 | `PF-S4-W2-A20-Q-12` | W2 | 20 | Quick Test | | | | |
| 13 | `PF-S4-W1-A60-R-13` | **W1** | 60 | raw | | | | |
| 14 | `PF-S4-W1-A20-R-14` | W1 | 20 | raw | | | | |
| 15 | `PF-S4-W1-A40-R-15` | W1 | 40 | raw | | | | |
| 16 | `PF-S4-W1-A40-Q-16` | W1 | 40 | Quick Test | | | | |
| 17 | `PF-S4-W1-A20-Q-17` | W1 | 20 | Quick Test | | | | |
| 18 | `PF-S4-W1-A60-Q-18` | W1 | 60 | Quick Test | | | | |
| 19 | `PF-S4-W3-A40-Q-19` | **W3** | 40 | Quick Test | | | | |
| 20 | `PF-S4-W3-A60-Q-20` | W3 | 60 | Quick Test | | | | |
| 21 | `PF-S4-W3-A20-Q-21` | W3 | 20 | Quick Test | | | | |
| 22 | `PF-S4-W3-A40-R-22` | W3 | 40 | raw | | | | |
| 23 | `PF-S4-W3-A20-R-23` | W3 | 20 | raw | | | | |
| 24 | `PF-S4-W3-A60-R-24` | W3 | 60 | raw | | | | |
| 25 | `PF-S4-W0-A60-R-25` | **W0** | 60 | raw | | | | |
| 26 | `PF-S4-W0-A20-R-26` | W0 | 20 | raw | | | | |
| 27 | `PF-S4-W0-A40-R-27` | W0 | 40 | raw | | | | |
| 28 | `PF-S4-W0-A20-Q-28` | W0 | 20 | Quick Test | | | | |
| 29 | `PF-S4-W0-A40-Q-29` | W0 | 40 | Quick Test | | | | |
| 30 | `PF-S4-W0-A60-Q-30` | W0 | 60 | Quick Test | | | | |

### S5

| Seq | Trial ID | Condition | Release (deg) | Mode | Encoder file # | App result | Stopped by | Notes |
|---|---|---|---|---|---|---|---|---|
| 1 | `PF-S5-YAW15-A40-Q-01` | **YAW15** | 40 | Quick Test | | | | |
| 2 | `PF-S5-YAW15-A20-Q-02` | YAW15 | 20 | Quick Test | | | | |
| 3 | `PF-S5-YAW15-A60-Q-03` | YAW15 | 60 | Quick Test | | | | |
| 4 | `PF-S5-YAW15-A20-R-04` | YAW15 | 20 | raw | | | | |
| 5 | `PF-S5-YAW15-A40-R-05` | YAW15 | 40 | raw | | | | |
| 6 | `PF-S5-YAW15-A60-R-06` | YAW15 | 60 | raw | | | | |
| 7 | `PF-S5-R10-A60-Q-07` | **R10** | 60 | Quick Test | | | | |
| 8 | `PF-S5-R10-A20-Q-08` | R10 | 20 | Quick Test | | | | |
| 9 | `PF-S5-R10-A40-Q-09` | R10 | 40 | Quick Test | | | | |
| 10 | `PF-S5-R10-A60-R-10` | R10 | 60 | raw | | | | |
| 11 | `PF-S5-R10-A40-R-11` | R10 | 40 | raw | | | | |
| 12 | `PF-S5-R10-A20-R-12` | R10 | 20 | raw | | | | |
| 13 | `PF-S5-R20-A40-Q-13` | **R20** | 40 | Quick Test | | | | |
| 14 | `PF-S5-R20-A60-Q-14` | R20 | 60 | Quick Test | | | | |
| 15 | `PF-S5-R20-A20-Q-15` | R20 | 20 | Quick Test | | | | |
| 16 | `PF-S5-R20-A40-R-16` | R20 | 40 | raw | | | | |
| 17 | `PF-S5-R20-A20-R-17` | R20 | 20 | raw | | | | |
| 18 | `PF-S5-R20-A60-R-18` | R20 | 60 | raw | | | | |
| 19 | `PF-S5-R40-A40-R-19` | **R40** | 40 | raw | | | | |
| 20 | `PF-S5-R40-A20-R-20` | R40 | 20 | raw | | | | |
| 21 | `PF-S5-R40-A60-R-21` | R40 | 60 | raw | | | | |
| 22 | `PF-S5-R40-A20-Q-22` | R40 | 20 | Quick Test | | | | |
| 23 | `PF-S5-R40-A40-Q-23` | R40 | 40 | Quick Test | | | | |
| 24 | `PF-S5-R40-A60-Q-24` | R40 | 60 | Quick Test | | | | |
| 25 | `PF-S5-ROLL90-A60-Q-25` | **ROLL90** | 60 | Quick Test | | | | |
| 26 | `PF-S5-ROLL90-A40-Q-26` | ROLL90 | 40 | Quick Test | | | | |
| 27 | `PF-S5-ROLL90-A20-Q-27` | ROLL90 | 20 | Quick Test | | | | |
| 28 | `PF-S5-ROLL90-A40-R-28` | ROLL90 | 40 | raw | | | | |
| 29 | `PF-S5-ROLL90-A20-R-29` | ROLL90 | 20 | raw | | | | |
| 30 | `PF-S5-ROLL90-A60-R-30` | ROLL90 | 60 | raw | | | | |

### S6

| Seq | Trial ID | Condition | Release (deg) | Mode | Encoder file # | App result | Stopped by | Notes |
|---|---|---|---|---|---|---|---|---|
| 1 | `PF-S6-L2-A20-Q-01` | **L2** | 20 | Quick Test | | | | |
| 2 | `PF-S6-L2-A40-Q-02` | L2 | 40 | Quick Test | | | | |
| 3 | `PF-S6-L2-A60-Q-03` | L2 | 60 | Quick Test | | | | |
| 4 | `PF-S6-L2-A60-R-04` | L2 | 60 | raw | | | | |
| 5 | `PF-S6-L2-A20-R-05` | L2 | 20 | raw | | | | |
| 6 | `PF-S6-L2-A40-R-06` | L2 | 40 | raw | | | | |
| 7 | `PF-S6-L0-A60-Q-07` | **L0** | 60 | Quick Test | | | | |
| 8 | `PF-S6-L0-A20-Q-08` | L0 | 20 | Quick Test | | | | |
| 9 | `PF-S6-L0-A40-Q-09` | L0 | 40 | Quick Test | | | | |
| 10 | `PF-S6-L0-A60-R-10` | L0 | 60 | raw | | | | |
| 11 | `PF-S6-L0-A20-R-11` | L0 | 20 | raw | | | | |
| 12 | `PF-S6-L0-A40-R-12` | L0 | 40 | raw | | | | |
| 13 | `PF-S6-L1-A60-R-13` | **L1** | 60 | raw | | | | |
| 14 | `PF-S6-L1-A40-R-14` | L1 | 40 | raw | | | | |
| 15 | `PF-S6-L1-A20-R-15` | L1 | 20 | raw | | | | |
| 16 | `PF-S6-L1-A20-Q-16` | L1 | 20 | Quick Test | | | | |
| 17 | `PF-S6-L1-A60-Q-17` | L1 | 60 | Quick Test | | | | |
| 18 | `PF-S6-L1-A40-Q-18` | L1 | 40 | Quick Test | | | | |
| 19 | `PF-S6-L4-A40-Q-19` | **L4** | 40 | Quick Test | | | | |
| 20 | `PF-S6-L4-A60-Q-20` | L4 | 60 | Quick Test | | | | |
| 21 | `PF-S6-L4-A20-Q-21` | L4 | 20 | Quick Test | | | | |
| 22 | `PF-S6-L4-A40-R-22` | L4 | 40 | raw | | | | |
| 23 | `PF-S6-L4-A60-R-23` | L4 | 60 | raw | | | | |
| 24 | `PF-S6-L4-A20-R-24` | L4 | 20 | raw | | | | |
| 25 | `PF-S6-L3-A20-R-25` | **L3** | 20 | raw | | | | |
| 26 | `PF-S6-L3-A60-R-26` | L3 | 60 | raw | | | | |
| 27 | `PF-S6-L3-A40-R-27` | L3 | 40 | raw | | | | |
| 28 | `PF-S6-L3-A20-Q-28` | L3 | 20 | Quick Test | | | | |
| 29 | `PF-S6-L3-A40-Q-29` | L3 | 40 | Quick Test | | | | |
| 30 | `PF-S6-L3-A60-Q-30` | L3 | 60 | Quick Test | | | | |

### S7

| Seq | Trial ID | Condition | Release (deg) | Mode | Encoder file # | App result | Stopped by | Notes |
|---|---|---|---|---|---|---|---|---|
| 1 | `PF-S7-L1-A40-Q-01` | **L1** | 40 | Quick Test | | | | |
| 2 | `PF-S7-L1-A20-Q-02` | L1 | 20 | Quick Test | | | | |
| 3 | `PF-S7-L1-A60-Q-03` | L1 | 60 | Quick Test | | | | |
| 4 | `PF-S7-L1-A40-R-04` | L1 | 40 | raw | | | | |
| 5 | `PF-S7-L1-A60-R-05` | L1 | 60 | raw | | | | |
| 6 | `PF-S7-L1-A20-R-06` | L1 | 20 | raw | | | | |
| 7 | `PF-S7-L3-A40-R-07` | **L3** | 40 | raw | | | | |
| 8 | `PF-S7-L3-A20-R-08` | L3 | 20 | raw | | | | |
| 9 | `PF-S7-L3-A60-R-09` | L3 | 60 | raw | | | | |
| 10 | `PF-S7-L3-A20-Q-10` | L3 | 20 | Quick Test | | | | |
| 11 | `PF-S7-L3-A60-Q-11` | L3 | 60 | Quick Test | | | | |
| 12 | `PF-S7-L3-A40-Q-12` | L3 | 40 | Quick Test | | | | |
| 13 | `PF-S7-L0-A40-R-13` | **L0** | 40 | raw | | | | |
| 14 | `PF-S7-L0-A20-R-14` | L0 | 20 | raw | | | | |
| 15 | `PF-S7-L0-A60-R-15` | L0 | 60 | raw | | | | |
| 16 | `PF-S7-L0-A40-Q-16` | L0 | 40 | Quick Test | | | | |
| 17 | `PF-S7-L0-A60-Q-17` | L0 | 60 | Quick Test | | | | |
| 18 | `PF-S7-L0-A20-Q-18` | L0 | 20 | Quick Test | | | | |
| 19 | `PF-S7-L2-A60-Q-19` | **L2** | 60 | Quick Test | | | | |
| 20 | `PF-S7-L2-A20-Q-20` | L2 | 20 | Quick Test | | | | |
| 21 | `PF-S7-L2-A40-Q-21` | L2 | 40 | Quick Test | | | | |
| 22 | `PF-S7-L2-A60-R-22` | L2 | 60 | raw | | | | |
| 23 | `PF-S7-L2-A20-R-23` | L2 | 20 | raw | | | | |
| 24 | `PF-S7-L2-A40-R-24` | L2 | 40 | raw | | | | |
| 25 | `PF-S7-L4-A40-R-25` | **L4** | 40 | raw | | | | |
| 26 | `PF-S7-L4-A20-R-26` | L4 | 20 | raw | | | | |
| 27 | `PF-S7-L4-A60-R-27` | L4 | 60 | raw | | | | |
| 28 | `PF-S7-L4-A20-Q-28` | L4 | 20 | Quick Test | | | | |
| 29 | `PF-S7-L4-A60-Q-29` | L4 | 60 | Quick Test | | | | |
| 30 | `PF-S7-L4-A40-Q-30` | L4 | 40 | Quick Test | | | | |

## Save

```
Pendulastic_Mobile_App_Trial/pendulum_fixture/<YYYY-MM-DD>_<session>_<phone>/
  phone/         app export: *-manifest.json and every *.jsonl
  encoder/       pf<MMDD>_NNN.bin (or .csv)
  rig_log.csv    the filled template
  session.json   the session header above
  photos/
```

Export from the app: Session → Export, and save every file including the manifest. This folder
is gitignored. Nothing in it is committed.

## After the session

- [ ] Count check: rig-log rows without `ERROR` = exported phone trials = encoder files.
- [ ] Run the analyser (`pendulum_fixture_report.py`, see the plan, section 11). Read its
      Pairing section first: it must not report a count mismatch.
- [ ] The report states pass or fail for criteria A1 to A9 of the plan. Do not change a limit
      or drop a swing because of what the report shows.

## Order generator

```python
import random
SEED = 20261118
ANGLES = [20, 40, 60]
SESSIONS = {
    "S1": ["W0", "W1", "W2", "W3", "W4"], "S2": ["W0", "W1", "W2", "W3", "W4"],
    "S3": ["W0", "W1", "W2", "W3", "W4"], "S4": ["W0", "W1", "W2", "W3", "W4"],
    "S5": ["R10", "R20", "R40", "YAW15", "ROLL90"],
    "S6": ["L0", "L1", "L2", "L3", "L4"], "S7": ["L0", "L1", "L2", "L3", "L4"],
}

def order(session, conds):
    rng = random.Random(f"{SEED}-{session}")
    blocks = conds[:]
    rng.shuffle(blocks)                 # block order
    rows = []
    for c in blocks:
        modes = ["R", "Q"]
        rng.shuffle(modes)              # raw or Quick Test first
        for m in modes:
            a = ANGLES[:]
            rng.shuffle(a)              # angle order within the mode
            rows += [(c, x, m) for x in a]
    return rows
```
