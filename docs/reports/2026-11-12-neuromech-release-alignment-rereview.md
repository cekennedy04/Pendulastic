# Re-review: neuromech session release alignment (2026-11-12)

Independent re-review of `origin/neuromech/session-release-alignment` at `6e56a0b`, the seven commits
`333d08d..6e56a0b` on top of `e2257cc`. The first review is
`docs/reports/2026-11-11-neuromech-release-alignment-review.md` on `origin/docs/release-alignment-review`
(verdict: approve with required changes). The author's account of the changes is the "After review"
section of `docs/reports/2026-11-11-neuromech-release-alignment.md` on the branch (sections A.1 to A.8).
The re-reviewer wrote none of the code. Nothing was pushed to the branch under review and no study data
was read.

Everything under "ran" was run in a throwaway detached worktree at `6e56a0b` with the project `.venv`, on
a library built there (`python -m neuromech.build`, 67 scenarios). It is all simulation: `dryrun`'s plant,
Player and phone. The probe harness is not committed; section 8 says what it does.

## Verdict

**Ready for a draft PR to main.** All seven changes are really in the code, each is covered by tests that
fail on `e2257cc` and pass on `6e56a0b` (one constant is not pinned, section 2.3), the gates pass, and I found no regression against `e2257cc` or
`main`. Nothing I found makes the branch worse than `main` on any input; every gap below is either a
flag that fires too often (loud, with a recorded override) or a fault that `main` and `e2257cc` also
score without comment.

It is not ready to be the judge of a real rig session. Four limits remain, the author states all four,
and I classify each as a **documented known limit, not a merge blocker** (section 3). All four
(three kinds of fault present in every run of a session, and the fixed tracking limit) have one remedy,
which I measured and which is not in the branch: compare the session with what the plant identified from
its own chirp run (R000) predicts (sections 3d and 3e). That should be a tracked follow-up, done before rig data is interpreted.

Before the PR leaves draft (small, none changes a number):

1. `score` exits 0 when a run ends in `scoring error` (section 4.1).
2. A run inside a session lag finding is told its lag "is outside the plausible" range even when it is
   inside it (section 4.2).
3. The centre of the phone gate is not pinned by any test (section 2.3).
4. Four statements in the author's report to tighten (section 5).
5. The session lag finding should say that an accepted session is still held to the tracking gate, and
   that the override is for a lag the chirp identification explains (section 3c).

## 1. The seven changes

Numbering is the author's (A.1). The first review listed five required and two recommended changes; the
author's 4 (tracking) comes from that review's section 3 and its 6 from the two recommendations.

| # | change | status | evidence reproduced here |
|---|---|---|---|
| 1 | an ambiguous onset must fail | **fixed** | Read: `_delivered_release` returns `hold_rms_deg` (rms over the 0.30 s before the delivered release, limit 0.20 deg) and `rival_ratio` (best fit 10 ms or more away over the chosen fit, limit 1.5), flags an optimum on the edge of the search, and a flagged release is never scored. Ran: the 18 test cases fail on `e2257cc`. Several fail on a key that did not exist then, so I checked the behaviour directly: `e2257cc`'s `_delivered_release` gives `D-wobble` on the two shifted plants lag -9.6 ms and -0.3 ms with `flag ''`, and on my deliveries leaves a 1 deg sag, a 2 deg sag and 0.5 deg of ringing at 6.35 Hz unflagged 8 of 8 each (release off by up to +9.3 and -13.9 ms); `6e56a0b` flags all of them for the hold. More hold faults: section 2.1. |
| 2 | session-level lag | **fixed** | Read: `_session_lag`, `_lag_verdict`, two passes in `score`, override recorded on every row. Ran: stiction in every run is one finding (8 of 8 "session lag finding", not 8 faults); one run 35 ms from the others is flagged and the rest scored (the author's test, which passes); the seven tests fail on `e2257cc`. Rule edge cases: section 2.2. |
| 3 | the phone's lower gate | **fixed** | Read: the phone is held to the encoder of its own run, centred 0.6 sample periods early, 1.5 either side, from the recording's median period. Ran through real files: 9 default runs at seeds 11, 5, 23, 3 and all 67 scenarios at seeds 11 and 5: 0 phone flags in 168 scored traces; phone minus encoder -18.2 to -3.2 ms on the default runs, against a gate of -35.6 to +15.3 ms at 59 Hz. Other rates: the author's test only (30, 59, 100, 200 Hz); I did not rebuild the A.4 table. |
| 4 | tracking gate | **fixed as specified; the limit is plant-specific** | Ran: 30 % gain recovering over 0.6 s, eight scenarios: tracking 32.4 to 38.5 ms, 8 of 8 flagged, scores kept, not a delivery (the author's figures exactly). Section 3d for the limit. |
| 5 | pin the method | **fixed** | Ran the mutation: `RELEASE_ONSET_DEG = 10` gives **19 failed, 56 passed** (the author's count); `= 5` gives 12 failed, 63 passed. Both fail `test_the_onset_method_is_pinned_across_the_whole_library` and the default-session test. Other constants: section 2.3 (one is not pinned). |
| 6 | small fixes | **fixed** | Ran through real files: `D-short` phone in band at seeds 11 and 5 (no phone band miss in 132 scored traces); headline reads "9 of 9 runs (100%); scored 9, release flagged 0, tracking flagged 0". The six tests pass, five fail on `e2257cc`. One gap: section 4.1. |
| 7 | two statements | **fixed** | Read: the `RELEASE_RESID_MAX_DEG` comment and the report's "Failing loudly" row now say 54 % of the window's 0.56 deg signal and withdraw "15 % of the onset"; the docstring and the report's summary and section 1 say the N counts do not single out 2 deg and that the plant sweep is the argument. |

**Fail before, pass after (ran).** `neuromech/tests/test_session_dryrun.py` as committed, with
`e2257cc`'s `session.py` loaded as `neuromech.session`: **44 failed, 31 passed**. On `6e56a0b`: 75 passed.
The 44 are the tests named in A.1 rows 1 to 6 less `test_a_real_sync_failure_is_still_a_sync_refusal`,
as the author says. Some of the 44 fail on a missing attribute or key, which says only that the feature
did not exist; for the hold faults and `D-wobble` I ran the old function directly (row 1 above).

## 2. Probes the author's tests do not cover (ran)

In-memory encoder path (section 8), identified plant unless stated, seed 11, eight scenarios (the first
review's set) unless stated. "Clean" is the same delivery without the fault.

### 2.1 Hold and uniqueness guards

| input | result |
|---|---|
| clean, all 67, seeds 11, 5, 23 (5 and 23 are not the author's) | `D-wobble` flagged (hold) and nothing else at each seed; hold rms at most 0.049 deg, rival ratio at least 2.59, residual at most 0.122 deg, tracking 9.0 to 19.2 ms; 66 of 66 in band |
| hold sag 0.75, 1, 1.5, 2 deg over the last 40, 80, 150 or 300 ms (16 cells) | flagged 8 of 8 in every cell: hold in 14; the 1.5 and 2 deg sags over 40 ms are caught as ambiguous, by lag, or as a session finding |
| hold sag 0.5 deg (4 durations) | not flagged (hold rms 0.16 to 0.17); release moves +1.8 to +2.2 ms; no N or band change |
| ringing 0.3 deg at 6.35 and 12 Hz, 0.4 deg at 2.5, 6.35, 12 Hz; 5 scenarios x 8 phases | flagged 40 of 40 in each |
| ringing 0.3 deg at 2.5 Hz | flagged 20 of 40; the other 20 are within -3.0 to +2.8 ms, no N or band change |
| ringing 0.25 deg at 2.5, 6.35, 12 Hz | flagged 0, 0, 5 of 40; unflagged releases within -6.2 to +5.1 ms; no N or band change |
| a 50 ms bump on the hold (a hand on the rig), 1 or 2 deg, 280, 150 or 60 ms before the release | flagged 8 of 8 wherever it falls in the window |
| the same, 0.5 deg | not flagged (0.13 deg); release unmoved (0.1 ms at most) |
| clean, encoder noise 0.05, 0.10, 0.15, 0.20 deg rms (the dry run uses 0.02) | hold rms is the noise; at 0.20 the hold check starts to flag good runs (1 of 8). The rival ratio falls with noise: 4.9 to 5.3 at 0.05, 2.9 to 3.1 at 0.10, 2.2 at 0.15, 1.8 to 1.9 at 0.20 (limit 1.5) |
| clean, encoder quantised to 0.09 and 0.36 deg | no flag; at 0.36 deg the rival ratio is 2.6 to 3.0 and the release moves -2 to -1 ms |

The guards do what change 1 asked: every hold fault I built that moved a release by more than about 6 ms
(6.2, to be exact) is flagged, and the ones that pass move it by less than that and change no N or band. The transition is
graceful, not a cliff. The limits lean on the encoder being quieter than about 0.15 deg rms: the author
lists the real hold's rms as unseen (A.7), and that is the right caveat.

### 2.2 Session rule

- Stiction of 100 ms in every run: one `SESSION LAG FINDING`, 8 runs "not scored for this reason", no
  per-run fault list.
- A session that is part faulty (eight runs, the first k with the fault):

  | faulty runs | fault | session median | faulty runs | good runs |
  |---|---|---|---|---|
  | 3 of 8 | slow start (30 % over 0.6 s), or 40 ms stiction | 27.5 ms | flagged, "this run, not the rig" | scored |
  | 4 of 8 | slow start | 37.6 ms | flagged (tracking 3, session median 1) | scored |
  | 4 of 8 | 40 ms stiction | 41.0 ms | flagged, "this run" | **flagged, "this run, not the rig"** |
  | 5 of 8 | either | 47.6, 54.5 ms | flagged (tracking) | **flagged, "this run, not the rig"** |

  When half or more of a session is faulty the median belongs to the fault, and the good runs are the
  ones told they are odd. Every faulty run is still flagged and `score` still exits 1, so nothing wrong
  is passed; the message points at the wrong runs. That is what a median does and is acceptable; one
  sentence in the README ("if most runs are flagged this way, suspect the others") would cover it.
- A two-run session has no reference (as documented); both runs of a lurching pair were caught by the
  tracking gate.
- Other plants, clean, all 67: damping at zeta 0.10 and 0.05 gives no hold or uniqueness flag (the lift's
  ringing has died by the release), 1 and 2 tracking flags; bandwidth -40 % gives 29 tracking flags;
  bandwidth +60 % gives none.
- `_lag_verdict` wording: section 4.2.

### 2.3 Do the tests pin the guards? (mutations, ran)

Each constant changed in memory, `test_session_dryrun.py` run unmodified.

| mutation | result |
|---|---|
| `RELEASE_ONSET_DEG` 2 to 10 | 19 failed, 56 passed |
| `RELEASE_ONSET_DEG` 2 to 5 | 12 failed, 63 passed |
| `RELEASE_ONSET_DEG` 2 to 3 | 5 failed, 70 passed (the pin test among them) |
| `RELEASE_HOLD_STILL_MAX_DEG` 0.20 to 0.40 | 8 failed (sag and ringing tests) |
| `RELEASE_UNIQUE_MIN_RATIO` 1.5 to 1.0 | 1 failed (`test_an_onset_with_two_candidate_releases_is_flagged`) |
| `SESSION_LAG_TOL_S` 12 to 40 ms | 4 failed |
| `TRACKING_TIME_ERR_MAX_MS` 25 to 40 | 2 failed |
| `PHONE_EARLY_SAMPLES` 0.6 to 0.0 | **75 passed** |

Every guard is pinned except the centre of the phone gate. The phone tests compute the expected centre
from `SS.PHONE_EARLY_SAMPLES` itself, so they follow the constant wherever it goes: "centred on the
sample-period offset" is tested only against its own definition. That the phone is held to the encoder
and not to the absolute range is pinned (those tests fail on `e2257cc`). One assertion with literal
numbers would close it, for instance at 59 Hz: 30 ms early of the encoder passes and 20 ms late is a sync
fault (with the centre at 0 the first is flagged and the second passes).

## 3. The four admitted limits

### (a) A forward lurch in every run is scored, unflagged, from an early release

**Reproduced. Known limit, not blocking.**

The author's fault constructor is not committed, so the lurch is mine: extra motion in the fall direction,
a half-cosine rise starting a set time after the commanded release, then decaying with a 200 ms time
constant. (A lurch that leaves a
permanent offset is caught by the tracking gate, 61 of 67, so the transient is the hard case.) All 66
scenarios other than `D-wobble`, every run faulty. "Early by" is against a marker that knows nothing of the
fit: the first instant the noise-free arm is 0.1 deg off its hold, less the same quantity on clean runs.

| lurch | scored without a flag | release early by (median) | fit statistics (largest residual in deg, smallest rival ratio) | band differs from the same trace scored at the marker |
|---|---|---|---|---|
| 3 deg over 150 ms, from +30 ms | 57 of 66 | 5.5 ms | 0.14, 1.66 | 0 |
| 6 deg over 150 ms, from +30 ms | 47 of 66 (tracking flags 14) | 9.3 ms | 0.18, 1.65 | 0 |
| 8 deg over 100 ms, from +30 ms | 39 of 66 (tracking flags 16) | 18.2 ms | 0.25, 1.57 | 1 (`M-tone_L4`) |
| 6 deg over 60 ms, from +30 ms | 48 of 66 | 22.5 ms | 0.30, 1.53 | 2 (`D-short`, `M-tone_L4`) |
| 6 deg over 150 ms, from +0 ms | 50 of 66 | 4.0 ms (the arm really does leave early) | 0.18, 1.53 | 0 |

- The direction and the absence of a flag are as the author says. The size depends on the shape: about
  25 ms needs a sharp lurch (6 deg in 60 ms); the author's "6 deg over 150 ms" gives 9 ms in my
  construction. I could not confirm 25 ms for that exact case (section 5).
- The cost is small and is not the half-cycle this change exists to remove. N differs from the
  marker-scored trace in 2 to 8 runs per row, and the same comparison on clean runs gives 6 (the marker's
  own scatter on knife-edge scenarios), so no N effect is distinguishable. Band changes: 0 to 2 of about 60 per row.
- The author's claim that no threshold on the present quantities separates it holds. I also tried one
  more quantity, the time from the declared release until the recording is 0.2 deg off its hold: clean
  0.5 to 36 ms (median 14.5), lurch 0.7 to 46 ms (median 22). It does not separate either.
- One lurching run among seven good ones is caught only when the lurch is sharp: 6 deg in 60 ms, flagged
  8 of 8 (session median); 6 deg over 150 ms, 2 of 8; 3 deg over 150 ms, 0 of 8. With a permanent offset,
  8 of 8. The author's A.6 says 8 of 8 for its own shape.
- Why it is not blocking: `main` and `e2257cc` score the same traces with no flag and from a release that
  is wrong on every run, clean ones included; the band is preserved in all but 0 to 2 of 60; and the report
  says it plainly.
- What does separate it, and is not in the branch: the lag the identified plant predicts (section 3e).

### (b) A 50 % sluggish start over 0.3 s is not flagged

**Reproduced exactly. Known limit, not blocking.**

Eight scenarios, every run: scored 8, in band 5, tracking 19.4 to 22.6 ms (limit 25), lag 37.5 ms; PT7
down by up to 0.026. All 67: 64 scored without a flag, band differs from the clean delivery in 3. One
faulty run among seven good ones: flagged 0 of 8 (it is 10 ms from the session median, tolerance 12).

The score is of what the arm did, and where that leaves the band the run shows as a band miss; what is
missing is the reason. Not blocking, for the same reasons as (a). It gets worse on a rig that tracks better
than the model (section 3d): the fixed limit then passes much larger faults.

### (c) The override is offered when the consistent lag comes from a fault

**Reproduced; in my runs the consequence is milder than the author states. Known limit, not blocking,
with a wording change.**

The finding does offer `--accept-session-lag-ms` for stiction in every run and for a lurch that starts
before the release. I then accepted it, as a careless operator would:

| fault in every run of eight | lag median | with the override accepted |
|---|---|---|
| stiction 100 ms, catching up over 200, 400, 600 or 1000 ms | 102 to 121 ms | **tracking flagged 8 of 8** in each |
| stiction 70 ms, catching up over 1000 ms | 92.5 ms | tracking flagged 8 of 8 |
| lurch 3 deg over 60 ms from -30 ms, transient or permanent | -45.5 ms | tracking flagged 8 of 8 |
| lurch 4 deg over 60 ms from -20 ms | -40.6 ms | tracking flagged 8 of 8 |
| lurch 2 deg over 40 ms from -40 ms | -58.4 ms | tracking flagged 6, edge of search 2 |
| arm simply 90 ms late with nothing else wrong (latency) | 117.5 ms | scored 8, in band 8: the case the override is for |

In ten fault variants (the table's, plus a 3 deg lurch from +0 ms) the override produced no run that counts as a delivery: the tracking gate is a
second line behind it, and `score` still exits 1. The author's A.6 note 2 ("would score a faulty session")
is therefore more pessimistic than what I measure; the scores are written to `report.csv`, but every run
is marked `tracking flagged`. I did not find a fault that passes both, which is not proof that none
exists. The message should say that an accepted session is still held to the tracking gate, and should
describe the override as being for a lag that the chirp identification explains. Section 3e makes that
check mechanical.

### (d) The tracking limit: is a fixed 25 ms acceptable?

**Acceptable to merge as a documented, provisional limit. Not acceptable as the limit a real session is
judged by: it must be derived from the session's identified plant before rig data is interpreted.**

Reproduced: bandwidth -30 % with damping -30 %, 66 good runs, tracking 21.1 to 36.3 ms, **49 over 25**
(the author's 49). Bandwidth -30 %: `S-lam3` and `R-reflex-sat` at up to 27.5. Damping -30 %: `B-clonus`
at 26.8.

What the author does not say is that the limit is wrong in the other direction too, and that direction
is silent. A rig that tracks better than the model, which is what ILC is for, has good runs far below
25 ms, so the same faults pass:

| plant | good runs, ms (min, median, max) | good runs over 25 | 30 % over 0.6 s flagged | 30 % over 0.3 s flagged | 50 % over 0.3 s flagged |
|---|---|---|---|---|---|
| identified (6.35 Hz, 0.23), 66 runs | 9.1, 14.2, 19.2 | 0 | 8 of 8 | 6 of 8 | 0 of 8 |
| bandwidth -30 %, damping -30 %, 66 runs | 21.1, 25.2, 36.3 | **49** | 8 of 8 | 8 of 8 | 8 of 8 |
| bandwidth -15 %, damping -15 %, 14 runs | 17.1, 18.6, 25.1 | 1 | 8 of 8 | 8 of 8 | 6 of 8 |
| bandwidth +30 %, 14 runs | 6.1, 10.1, 12.2 | 0 | 8 of 8 | **0 of 8** | 0 of 8 |
| 12 Hz, zeta 0.7, 66 runs | 0.3, 0.4, 2.6 | 0 | 6 of 6 | **0 of 8** (14.6 to 16.4 ms) | **0 of 8** (9.1 to 10.2 ms) |
| 20 Hz, zeta 0.7, 14 runs | 0.4, 0.6, 1.4 | 0 | 6 of 6 | **0 of 8** | **0 of 8** |

On a 12 Hz arm a fault that makes tracking 40 times worse than the plant's own passes untouched.

Deriving the limit is feasible with what a session already records. I identified each plant from a
simulated chirp (`session.chirp_command` through the Player hold and the plant, `ilc.identify`), drove
each command through the identified model, and took the model's tracking time error as the prediction:

| plant | identified (Hz, zeta, delay ms) | good runs, measured / predicted | faults, measured / predicted |
|---|---|---|---|
| identified | 6.34, 0.234, 4.8 | 0.96 to 1.04 (66 runs) | 1.39 to 2.93 |
| bandwidth -30 % | 4.46, 0.236, 5.1 | 1.01 to 1.05 | 1.29 to 2.26 |
| bandwidth -30 %, damping -30 % | 4.46, 0.171, 5.2 | 1.01 to 1.06 (66 runs) | 1.16 to 2.07 |
| bandwidth -15 %, damping -15 % | 5.40, 0.196, 5.0 | 0.99 to 1.03 | 1.25 to 2.43 |
| bandwidth +30 % | 8.27, 0.232, 5.0 | 0.99 to 1.04 | 1.61 to 4.61 |
| damping -30 % | 6.34, 0.167, 4.6 | 0.98 to 1.03 | 1.28 to 2.66 |
| + 40 ms delay | 6.33, 0.235, 44.6 | 1.00 to 1.04 | 1.37 to 1.77 |
| 3 Hz, zeta 0.7 | 3.00, 0.699, 4.8 | 0.98 to 1.02 (9 runs) | none scored |
| 12 Hz and 20 Hz, zeta 0.7 | 11.76, 0.705; 18.79, 0.717 | 0.61 to 3.69 (the prediction is under 1 ms, so a ratio is meaningless; needs an absolute floor) | 26 to 168 |

Against its own plant's prediction a good run sits within 6 % on every second-order plant from 3 to
8.3 Hz, where the fixed limit flags 0 to 49 of them; and the faults are 16 % to several hundred per cent over (the mildest, the 50 % start over 0.3 s of
limit (b), is 21 to 83 % over). This is a measurement of feasibility, not a design: the margin, the floor for a rig that
tracks almost perfectly, and what to do for a session planned without `--chirp` are open.

Why the fixed limit can still merge: on the slow side it fails loudly, keeps the scores, and has a
recorded override (`--tracking-limit-ms`, written to `tracking_limit_ms` on every row); on the fast side it
is no worse than `main`, which has no tracking gate. The code, README and report all call it provisional.
The report's A.5 and A.7 should add the fast-plant direction (section 5).

### (e) One remedy for (a), (b) and (c): the lag the identified plant predicts

A fault in every run cannot be seen by comparing runs with each other. It can be seen by comparing the
session with its own chirp. Same identification as in (d); predicted lag is the onset-matched lag of each
command through the identified model.

| session (eight runs, every run the same) | measured lag, median | predicted from the chirp | measured minus predicted (min, median, max) |
|---|---|---|---|
| clean, identified plant | 27.5 | 30.5 | -4.0, -3.0, -2.9 |
| clean, + 60 ms delay (a session lag finding today) | 87.5 | 90.6 | -4.1, -3.1, -3.0 |
| clean, bandwidth -30 % + 20 ms | 58.6 | 61.6 | -4.0, -3.0, -2.6 |
| clean, 3 Hz, zeta 0.7 | 61.0 | 64.0 | -3.9, -2.9, -2.6 |
| (b) sluggish start, 50 % over 0.3 s | 37.5 | 30.5 | +5.8, +6.9, +8.0 |
| slow start, 30 % over 0.6 s | 49.0 | 30.5 | +17.0, +18.4, +21.0 |
| (a) lurch 6 deg over 150 ms from +30 ms | 16.1 | 30.5 | -16.8, -14.5, -13.2 |
| (a) lurch 6 deg over 150 ms from +0 ms | -1.1 | 30.5 | -35.0, -31.7, -30.2 |
| (c) stiction 60 ms | 69.6 | 30.5 | +39.0, +39.1, +40.1 |
| (c) stiction 100 ms | 102.0 | 30.5 | +70.4, +71.5, +72.8 |

Clean sessions on four different plants sit at -3 ms (the Player's write phase, which the chirp fit
absorbs as delay) within about 1 ms. Every admitted fault is at least 8 ms from that, on every run. A
session with 60 ms of honest latency, which today needs the override, would be explained by its own chirp;
stiction of 100 ms, which today is offered the same override, would not.

Caveats: simulation only; it assumes the chirp identifies the plant correctly on a rig that has the
release fault (plausible for a fault at the release, unverified); it needs R000 in the session. I did not
implement it and it is not required for the draft PR. It is the follow-up I would put first.

## 4. Defects found in the new code (minor)

### 4.1 `score` exits 0 with a run that could not be scored

`main()` exits 1 on `release_flags(rep) or tracking_flags(rep)`. A run whose status is `scoring error`
is in neither (checked by calling both on such a report: both empty, exit status 0), and the same holds
for a phone `scoring error` or `release error`. The run is counted in the printed headline, so it is not
hidden, but a script that trusts the exit status sees a clean session with an unscored run. Change 6 made
the scoring error non-fatal, which is right; the exit status should follow. One line and one assertion in
`test_an_encoder_scoring_error_is_reported_and_does_not_stop_the_session`.

### 4.2 A false sentence in a per-run status

In a session with a lag finding every run goes through the same branch of `_lag_verdict`, so a run at
+27.0 ms in a session whose median is +87.0 ms gets "session lag finding: lag +27.0 ms, with the session's
median at +87.0 ms, is outside the plausible -10 to +80 ms". It is not outside it. The run is correctly
not scored (it is 60 ms from its session); the sentence is wrong. `test_session_lag_rules` asserts only
that the verdict is non-empty.

### 4.3 Observations, no change asked

- The pins are for seed 11. Through the in-memory delivery the all-67 median lag is 27.4 and 27.3 ms at seeds 11
  and 5 and 28.4 ms at seed 23 (one Player phase for the whole library at a given seed), which is outside
  the pin's 26.9 to 27.9. The test fixes the seed, so it is stable; the docstring already says the numbers
  are regression pins.
- The residual limit (0.3 deg) now does almost nothing that the other checks do not: clean fits are at
  0.12 or below at three seeds. It was not asked to be tightened and tightening it does not catch the lurch
  (residuals 0.03 to 0.30).

## 5. The author's report against what I measured

Confirmed by running: 174 passed; 44 failed / 31 passed on `e2257cc`'s `session.py`; 19 failed / 56 passed
with a 10 deg onset; parity; the default dry run; A.3's seed 11 "fixed" row to the digit (66 scored, 66 in
band, N differs in 8, phone 66 scored, 66 in band, N differs in 10, lag 27.3 (22.6 to 28.6), `D-wobble`
not scored); tracking time error median 14.1, maximum 19.2; A.5's identified row (66 scored, 10 / 0) and
the tracking counts at bandwidth -30 % (2), damping -30 % (1) and both (49); A.6's slow start, sluggish
start (both, and the one-faulty-run case for the 50 % one) and stiction-as-finding rows. An independent seed (5) through real files
gives the same picture: 66 scored and in band on encoder and phone, `D-wobble` flagged, N differs in 10
and 13, lag 27.4 (22.6 to 29.3).

README: "174 tests", "9 of 9 at the five seeds" (I ran seeds 11, 5, 23, 3 and 7: 9 of 9 each, three of them
the author's), "tracking time error 14 ms median, 19 ms at most", "66 scored and in band on the encoder and
the phone" (seed 11 confirmed), the status table and the session-finding procedure all match the code
and my runs.

Could not confirm, or would tighten:

1. **A.6, forward lurch "lag wrong by about 25 ms" for 6 deg over 150 ms from +30 ms.** With my constructor
   that case is 9 ms early (10 to 14 ms below the clean lag on the eight scenarios); 22 ms needs 6 deg in
   60 ms. The fault constructor is not committed, so this is a difference of shape, not a contradiction.
   The report should give the shape, or commit the constructor as it did for sag, ringing and stall.
2. **A.6 note 2, "would score a faulty session".** Not in my ten variants: every one is tracking flagged
   after the override (section 3c). Say so; it is a point in the design's favour.
3. **A.2, seed-specific figures stated as bounds.** "Uniqueness 2.70 or more on the identified plant": 2.59
   at seed 5. "Good runs deviate by -4.8 to +3.0 ms" from the session median: -5.8 to +1.9 at seed 23.
   Both are far from their limits (1.5 and 12 ms); say "at seeds 3, 11 and 42".
4. **A.5 / A.7, the tracking limit.** "A slower rig than the model will need this limit re-set" is half of
   it. A faster or better-corrected rig needs it re-set more, because there the limit fails silently
   (section 3d).

## 6. Regressions and gates (ran, at `6e56a0b`)

| gate | result |
|---|---|
| `python -m pytest neuromech/tests -q` | **174 passed**, 0 failed, 0 skipped (2286 s with five other jobs running beside it) |
| `test_session_dryrun.py` with `e2257cc`'s `session.py` | 44 failed, 31 passed |
| `python neuromech/designer/parity/run_parity.py` (after pytest had finished) | **PARITY OK**, 355 cases, every field within tolerance |
| `python -m neuromech.dryrun --seed 11 --discard`, and seed 7 | **9 of 9** in band, scored 9, release flagged 0, tracking flagged 0, exit 0; phone 9 of 9; lag 27.4 ms (22.7 to 28.4); plant 6.34 Hz, zeta 0.234 (`main`: 7 of 9) |
| default dry run through `dryrun.run`, seeds 11, 5, 23, 3 | 9 of 9 scored and in band on encoder and phone at each; catch runs at 0.127 to 0.128 |
| all 67 through `dryrun.run` in sessions of eight, seeds 11 and 5 | 66 scored and in band on encoder and phone; `D-wobble` flagged; no tracking flag; no phone flag |
| line endings | `git diff --stat e2257cc 6e56a0b` and the same with `--ignore-cr-at-eol` are identical (4 files, 1105 insertions, 76 deletions); likewise `origin/main...6e56a0b` (5 files, 1823 / 38); `git ls-files --eol` gives `i/lf w/lf` for the four files; `git diff --check` is clean |
| scope | `e2257cc..6e56a0b` touches `neuromech/session.py`, `neuromech/tests/test_session_dryrun.py`, `neuromech/README.md` and the report. Nothing else |

No behaviour on the identified plant got worse between `e2257cc` and `6e56a0b`: the encoder N and band
counts are the author's "before" figures with `D-wobble` removed, and the phone improved (`D-short`).

## 7. Not verified

- Anything on the rig, a real LabVIEW log or a real phone. Every number here is simulated, as the author's
  are. In particular a real hold's rms and a real encoder's noise, which the hold and uniqueness limits
  depend on (section 2.1).
- The author's A.3 rows for seeds 3 and 42, and the "e2257cc" rows of that table through real files.
- The A.4 phone table (flags by rate, 180 runs per cell) and the offset figures by rate. The phone was
  checked only at the dry run's 59 Hz.
- A.5 rows I did not run: bandwidth +-20 %, damping +30 %, + 20 ms, the two combined rows other than
  -30 % / -30 %, zeta 0.5 and 1.0; and the `D-wobble` 14-plant count. I ran identified, bandwidth -30 %
  and +30 %, damping -30 %, + 40 ms, -30 % / -30 %, 3 Hz and 12 Hz, and my flag counts for 3 Hz are from
  a 14-scenario subset.
- A.6 rows I did not run: ringing at 1.0 and 1.5 deg, kick-back, slack.
- `node neuromech/labview/verify_formula_node.js`, the CI python list outside `neuromech/tests` (the
  author reports 536 passed), the Rust and webapp jobs, `ilc_demo` beyond its test, `nm_figs.fig_dryrun`.
- Whether a chirp identifies the plant correctly on a rig that has a release fault (section 3e assumes it).
- Mutations of constants other than those in section 2.3 (the phone tolerance, the window sizes, the
  session's minimum run count and consistency share).

## 8. How this was run

- Worktree: `git worktree add <dir> origin/neuromech/session-release-alignment --detach` at `6e56a0b`,
  library built there, removed afterwards with its `neuromech/out`. The author's worktree and the main
  checkout were not touched.
- Fail-before: `e2257cc`'s `session.py` (`git show e2257cc:neuromech/session.py`) compiled into a module
  object registered as `neuromech.session` before pytest collects, so the test file and `dryrun.py` are
  the committed ones and no file in the worktree is edited. Mutations: the same runner sets one module
  constant before collection.
- Probe harness (not committed): imports the branch's `session`, `dryrun` and `ilc` unmodified and repeats
  the encoder branch of `session.score` in memory: `dryrun.player_hold`, `dryrun.plant_response` with a
  chosen plant, a fault added to the arm's motion, 0.02 deg noise, `ilc.locate`,
  `session._delivered_release`, `session._session_lag`, `session._lag_verdict`, `session._score_trace`,
  `session._tracking`. The fault constructors are my own (lurch, stiction as a closing time delay, sag,
  ringing, bump, slow start); the sag, ringing and slow-start shapes follow the committed tests so that
  the author's figures can be compared.
- Real file path: `dryrun.run` with `session.SESS` pointed at a temporary directory, deleted afterwards.
