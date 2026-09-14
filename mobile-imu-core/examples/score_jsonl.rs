//! Score exported raw-IMU JSONL files through the production pipeline.
//!
//! This is the same path the PWA runs (`replay` -> `compute_pt_params` under
//! the live `detrend=false` convention, plus `unmeasured_params`), so its
//! output can be compared line-for-line against a session's `*-manifest.json`.
//!
//!     cargo run --release --example score_jsonl -- [--detrend] <trial.jsonl>...
//!
//! One line of tab-separated fields per file. The JSONL is parsed by hand to
//! keep the crate dependency-free.

use mobile_imu_core::pt_score::unmeasured_params;
use mobile_imu_core::replay::{replay, RawSample, ReplayConfig, Sensor};
use mobile_imu_core::scoring::compute_pt_params;

fn field<'a>(line: &'a str, key: &str) -> Option<&'a str> {
    let k = format!("\"{key}\":");
    let start = line.find(&k)? + k.len();
    let rest = &line[start..];
    let end = rest.find([',', '}']).unwrap_or(rest.len());
    Some(rest[..end].trim())
}

fn parse_line(line: &str) -> Option<RawSample> {
    let t: f64 = field(line, "t")?.parse().ok()?;
    let ts_ms: i64 = field(line, "phone_ts_ms")?.parse().ok()?;
    let sensor = match field(line, "sensor")?.trim_matches('"') {
        "accel" => Sensor::Accel,
        "gyro" => Sensor::Gyro,
        "mag" => Sensor::Mag,
        _ => return None,
    };
    let vk = "\"v\":[";
    let vs = line.find(vk)? + vk.len();
    let ve = line[vs..].find(']')? + vs;
    let mut v = [0.0; 3];
    for (i, s) in line[vs..ve].split(',').enumerate().take(3) {
        v[i] = s.trim().parse().ok()?;
    }
    Some(RawSample {
        t,
        ts_ms,
        sensor,
        v,
    })
}

fn main() {
    let mut detrend = false;
    let mut dump = false;
    let mut files = Vec::new();
    for a in std::env::args().skip(1) {
        match a.as_str() {
            "--detrend" => detrend = true,
            // Print the replayed tick series (t, angle_deg) instead of scores.
            "--dump" => dump = true,
            _ => files.push(a),
        }
    }
    println!("file\tstatus\trelease_idx\tt_release\tf\tn\ta0_deg\tr2n\tphi_max_ratio\tomega_max_n\tomega_min_n\tarea_ratio\tneutral_deg\tunmeasured");
    for f in files {
        let text = std::fs::read_to_string(&f).expect("read jsonl");
        let samples: Vec<RawSample> = text.lines().filter_map(parse_line).collect();
        let name = std::path::Path::new(&f)
            .file_name()
            .map(|s| s.to_string_lossy().into_owned())
            .unwrap_or(f.clone());
        let r = match replay(&samples, &ReplayConfig::default()) {
            Ok(r) => r,
            Err(e) => {
                println!("{name}\treplay_err:{e:?}");
                continue;
            }
        };
        if dump {
            for (t, a) in r.t.iter().zip(&r.angle_deg) {
                println!("{t:.3},{a:.6}");
            }
            continue;
        }
        match compute_pt_params(&r.t, &r.angle_deg, None, detrend) {
            None => println!("{name}\tREFUSED"),
            Some(p) => {
                let rel = p.t_r.first().copied().unwrap_or(f64::NAN);
                let rel_i = r.t.iter().position(|&x| x >= rel).unwrap_or(0);
                println!(
                    "{name}\tok\t{rel_i}\t{rel:.3}\t{:.6}\t{:.1}\t{:.6}\t{:.6}\t{:.6}\t{:.6}\t{:.6}\t{:.6}\t{:.6}\t{}",
                    p.f, p.n, p.a0_deg, p.r2n, p.phi_max_ratio, p.omega_max_n, p.omega_min_n,
                    p.area_ratio, p.neutral_deg,
                    unmeasured_params(&p).join(",")
                );
            }
        }
    }
}
