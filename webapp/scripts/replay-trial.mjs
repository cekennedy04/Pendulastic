// Replays a raw phone recording (.jsonl, the app's own export format) through
// the app's worker protocol and the wasm binary that ships, and prints what the
// app would have shown and stored for it, as JSON on stdout.
//
//   node webapp/scripts/replay-trial.mjs <trial.jsonl> [--batch N] [--trajectory] [--wasm FILE]
//
// The wasm is the one in webapp/src/wasm, which is a LOCAL BUILD and not in
// git: run `npm run build:wasm` first or this replays whatever source that
// folder was last built from.
//
// It behaves as the capture screen does: samples go in through the worker's
// `batch` message, the hold/release/settle state is read back after each one,
// and the trial is ended the moment the core reports SETTLED (app.js onState,
// code 4 -> endTrial). A recording that never settles runs to its last sample
// and is ended there, which is what an operator pressing Stop does.
//
// Batches are one sample by default so a state change is timed to the sample.
// The app flushes every 50 ms (about three samples); the core walks a batch one
// sample at a time, so the batch size changes when a state is SEEN, never what
// it is. Pass --batch 3 to see it at the app's own cadence.
//
// Used by neuromech/phone_trial.py, which generates the recordings and knows
// what each one should produce.
import { readFileSync } from 'node:fs';
import { createWorkerHandler } from '../src/worker.js';
import { captureQualityOf, SETTLE_TARGET_S } from '../src/capture-feedback.js';

const STATE_NAMES = ['moving', 'holding', 'ready', 'released', 'settled'];
// capture.js posts exactly this with `start`; it is inline there, not exported.
const CAPTURE_CFG = { beta: 0.041, emaAlpha: 0.3 };

function parseArgs(argv) {
  const out = { file: null, batch: 1, trajectory: false, wasm: null };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--batch') { out.batch = Number(argv[i + 1]); i += 1; }
    else if (argv[i] === '--wasm') { out.wasm = argv[i + 1]; i += 1; }
    else if (argv[i] === '--trajectory') out.trajectory = true;
    else out.file = argv[i];
  }
  if (!out.file || !Number.isInteger(out.batch) || out.batch < 1) {
    console.error('usage: node webapp/scripts/replay-trial.mjs <trial.jsonl> [--batch N] [--trajectory]');
    process.exit(2);
  }
  return out;
}

// One [t_ms, ax, ay, az, gx, gy, gz] record per gyro line, carrying the accel
// line that precedes it -- the pairing WasmSession::push_batch undoes.
function readSamples(path) {
  const samples = [];
  let accel = null;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    if (!line.trim()) continue;
    const r = JSON.parse(line);
    if (r.sensor === 'accel') accel = r.v;
    else if (r.sensor === 'gyro') {
      if (!accel) throw new Error('gyro line before any accel line: not an app export');
      samples.push([r.t * 1000, ...accel, ...r.v]);
    }
  }
  return samples;
}

const args = parseArgs(process.argv.slice(2));
const samples = readSamples(args.file);
if (samples.length === 0) {
  console.error(`no samples in ${args.file}`);
  process.exit(2);
}

// --wasm swaps in another build of the SAME core (same exports, so the same
// bindings) -- for asking what a candidate fix would do before it ships.
const wasmSource = readFileSync(args.wasm ?? new URL('../src/wasm/mobile_imu_core_bg.wasm', import.meta.url));
const handle = createWorkerHandler();
let last = null;
const post = (m) => { last = m; };
const send = async (m) => { last = null; await handle(m, post); return last; };

const t0 = samples[0][0] / 1000;
const transitions = [];
let code = null;
let maxDriftDeg = 0;
let settleS = 0;
let used = 0;
let ended = 'end_of_file';

const note = (state, t) => {
  if (state.type !== 'state') throw new Error(`worker replied ${JSON.stringify(state)}`);
  maxDriftDeg = Math.max(maxDriftDeg, state.drift_deg);
  settleS = state.settle_s;
  if (state.code !== code) {
    code = state.code;
    transitions.push({ t: t - t0, code, state: STATE_NAMES[code] });
  }
};

note(await send({ type: 'start', cfg: { ...CAPTURE_CFG, wasmSource } }), t0);
for (let i = 0; i < samples.length; i += args.batch) {
  const chunk = samples.slice(i, i + args.batch);
  const state = await send({ type: 'batch', buf: Float64Array.from(chunk.flat()).buffer });
  used += chunk.length;
  note(state, chunk[chunk.length - 1][0] / 1000);
  if (code === 4) { ended = 'settled'; break; }
}

const fin = await send({ type: 'finish' });
const exported = await send({ type: 'export' });

const out = {
  file: args.file,
  samples_in: samples.length,
  samples_used: used,
  duration_used_s: samples[used - 1][0] / 1000 - t0,
  ended,
  transitions,
  max_hold_drift_deg: maxDriftDeg,
  settle_s: settleS,
  capture_quality: captureQualityOf({
    settleS, settleTargetS: SETTLE_TARGET_S, endedManually: ended !== 'settled',
  }),
  exported_lines: exported.jsonl.split('\n').filter(Boolean).length,
  scored: fin.type === 'result',
  error: fin.type === 'error' ? fin.reason : null,
};

if (fin.type === 'result') {
  const tr = fin.trajectory;
  out.params = fin.params;
  out.pt_score = fin.ptScore;
  out.params_detrended = fin.paramsDetrended;
  out.pt_score_detrended = fin.ptScoreDetrended;
  out.lateral_motion = fin.lateralMotion;
  // The release the SCORER settled on, on the trial's own clock (seconds from
  // the first sample). The live detector's release is the `released`
  // transition above; the two are found independently.
  out.scored_release_t = tr ? tr.t[tr.release_idx] : null;
  if (args.trajectory && tr) out.trajectory = { t: tr.t, angle_deg: tr.angle_deg };
}

console.log(JSON.stringify(out));
