// Executes model_step_formula_node.txt (as JavaScript) on each test vector and compares every
// output state -- the six continuous states and the eight HESS phase registers -- with the Python twin's. Formula Node syntax is C-like; two rewrites make it JS:
//   "float64 a, b;" -> "let a, b;"   and   math calls -> Math.*
//   node neuromech/labview/verify_formula_node.js
const fs = require("fs"), path = require("path");
const dir = __dirname;
const src = fs.readFileSync(path.join(dir, "model_step_formula_node.txt"), "utf8")
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/\bfloat64\b/g, "let")
  .replace(/\b(cos|sin|exp|tanh)\(/g, "Math.$1(");
const HESS = ["hph", "ht0", "hwc", "hs0", "hsp", "hlv", "hte", "hta"];
const OUT = ["th_o", "w_o", "ts_o", "tr_o", "tf_o", "tc_o", "srs_on_o", "rev_o", "clon_t0_o", "tau_o", ...HESS.map(k => k + "_o")];
const INPUTS = ["th", "w", "ts", "tr", "tf", "tc", "srs_on", "rev", "clon_t0", "t", "wd", "wd_prev", "sd", "sd_prev", "thd",
  "J", "mgl", "inc", "th0", "B", "coul", "tone", "k_f", "k_df", "k_srs", "k_v", "k_v_thr", "k_l", "k_l_thr", "refl_max",
  "flex_g", "flex_vth", "catch_g", "catch_vth", "catch_rel",
  "clon_nm", "clon_hz", "clon_tau", "rigid", "cog_d", "cog_hz", "con_k", "con_th", "guard", "gph0", "gph1", "gph2", "gph3",
  "spasm", "spasm_t", "spasm_w", "use_lim",
  ...HESS, "hH", "hQ", "hD", "hL", "hA", "hMin", "hWL", "hTp", "hKp", "hWth", "hRel"];
const step = new Function(...INPUTS, `let ${OUT.join(", ")};\n${src}\nreturn {${OUT.join(", ")}};`);
const rad = d => d * Math.PI / 180;
let worst = 0, failed = false;
for (const f of fs.readdirSync(dir).filter(n => n.startsWith("test_vector_") && n.endsWith(".csv"))) {
  const lines = fs.readFileSync(path.join(dir, f), "utf8").trim().split(/\r?\n/);
  const h = JSON.parse(lines[0].slice(2)); const cols = lines[1].split(",");
  const P = { J: h.J, mgl: h.mgl, inc: h.inc, th0: h.th0, B: h.B, coul: h.coulomb_nm, tone: h.tone,
    k_f: h.k_f * h.gain_scale, k_df: h.k_df * h.gain_scale,
    k_srs: h.k_srs, k_v: h.k_v * h.gain_scale, k_v_thr: h.k_v_thr, k_l: h.k_l * h.gain_scale,
    k_l_thr: rad(h.k_l_thr_deg), refl_max: h.reflex_max_nm,
    flex_g: h.flex_gain, flex_vth: h.flex_vth, catch_g: h.catch_gain, catch_vth: h.catch_vth, catch_rel: rad(h.catch_release_deg),
    clon_nm: h.clon_nm, clon_hz: h.clonus_hz, clon_tau: h.clonus_tau, rigid: h.rigid_nm, cog_d: h.cog_depth, cog_hz: h.cog_hz,
    con_k: h.contract_k, con_th: rad(h.contract_deg), guard: h.guard_nm, gph0: h["gph0..3"][0], gph1: h["gph0..3"][1],
    gph2: h["gph0..3"][2], gph3: h["gph0..3"][3], spasm: h.spasm_nm, spasm_t: h.spasm_t, spasm_w: h.spasm_w, use_lim: h.use_limit ? 1 : 0,
    hH: h.hess_H, hQ: h.hess_Q, hD: h.hess_D_deg, hL: h.hess_L_deg, hA: h.hess_alpha_s, hMin: h.hess_min_deg,
    hWL: h.hess_wL_dps, hTp: h.hess_tpeak_s, hKp: h.hess_kpost, hWth: h.hess_wth_dps, hRel: h.hess_relax_s };
  let maxErr = 0, at = -1;
  for (const line of lines.slice(2)) {
    const v = Object.fromEntries(line.split(",").map((x, i) => [cols[i], parseFloat(x)]));
    const r = step(...INPUTS.map(k => (k in v ? v[k] : P[k])));
    for (const k of ["th_o", "w_o", "ts_o", "tr_o", "tf_o", "tc_o", ...HESS.map(k => k + "_o")]) {
      const e = Math.abs(r[k] - v[k]); if (e > maxErr) { maxErr = e; at = v.t; }
    }
  }
  worst = Math.max(worst, maxErr);
  const ok = maxErr < 1e-9; failed = failed || !ok;
  console.log(`${f}: ${lines.length - 2} ticks, max |Formula Node - Python| = ${maxErr.toExponential(2)} (at t=${at.toFixed(3)} s)  ${ok ? "PASS" : "FAIL"}`);
}
process.exit(failed ? 1 : 0);
