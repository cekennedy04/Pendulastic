import CoreMotion
import Foundation

/// Throwaway capture-only recorder for U0 (see
/// docs/plans/2026-08-21-001-feat-phone-imu-pendulum-app-plan.md).
///
/// Writes one JSON array of samples per trial, each shaped as
/// {t, role, sensor, v, phone_ts_ms} to match imu_calibration_tuner.py's
/// replay_trial() input contract. `role` is fixed to "proximal" — that is
/// the role replay_trial()'s solo-fallback path (is_solo = not has_distal
/// and role == ROLE_PROXIMAL) expects for a single-phone capture; a "distal"
/// role with no proximal counterpart would NOT trigger that fallback.
///
/// No calibration, no scoring, no participant model. This is not reused by
/// U3-U13 — see U0's plan entry for why.
final class SampleRecorder {
    private struct Sample {
        let tSeconds: Double
        let sensor: String
        let v: (Double, Double, Double)
        let phoneTsMs: Int64
    }

    private let motionManager = CMMotionManager()
    private var samples: [Sample] = []
    private var startUptime: TimeInterval?

    var sampleCount: Int { samples.count }

    /// ~100Hz, matching KTD5 — CMMotionManager's raw streams, not the newer
    /// CMBatchedSensorManager 800Hz path, since pendulum motion is low-frequency.
    private let updateInterval: TimeInterval = 1.0 / 100.0

    func start() {
        samples.removeAll()
        startUptime = nil
        guard motionManager.isAccelerometerAvailable,
              motionManager.isGyroAvailable,
              motionManager.isMagnetometerAvailable else {
            return
        }

        motionManager.accelerometerUpdateInterval = updateInterval
        motionManager.gyroUpdateInterval = updateInterval
        motionManager.magnetometerUpdateInterval = updateInterval

        // Raw accelerometerData/gyroData/magnetometerData, not the fused
        // CMDeviceMotion — U1 does its own AHRS fusion; feeding it Apple's
        // already-fused orientation would double-fuse (per U4's Approach).
        motionManager.startAccelerometerUpdates(to: .main) { [weak self] data, _ in
            guard let self, let data else { return }
            self.record(sensor: "accel", v: data.acceleration.tuple, uptime: data.timestamp)
        }
        motionManager.startGyroUpdates(to: .main) { [weak self] data, _ in
            guard let self, let data else { return }
            self.record(sensor: "gyro", v: data.rotationRate.tuple, uptime: data.timestamp)
        }
        motionManager.startMagnetometerUpdates(to: .main) { [weak self] data, _ in
            guard let self, let data else { return }
            self.record(sensor: "mag", v: data.magneticField.tuple, uptime: data.timestamp)
        }
    }

    @discardableResult
    func stop() -> URL? {
        motionManager.stopAccelerometerUpdates()
        motionManager.stopGyroUpdates()
        motionManager.stopMagnetometerUpdates()
        return writeTrialFile()
    }

    private func record(sensor: String, v: (Double, Double, Double), uptime: TimeInterval) {
        if startUptime == nil { startUptime = uptime }
        let tSeconds = uptime - (startUptime ?? uptime)
        let phoneTsMs = Int64((uptime * 1000).rounded())
        samples.append(Sample(tSeconds: tSeconds, sensor: sensor, v: v, phoneTsMs: phoneTsMs))
    }

    private func writeTrialFile() -> URL? {
        // Chronological order isn't guaranteed across sensor callback types
        // (per U6's Approach note) — sort before writing so downstream
        // replay_trial() gets a pre-sorted list, matching its documented contract.
        let sorted = samples.sorted { $0.tSeconds < $1.tSeconds }

        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        formatter.timeZone = TimeZone(identifier: "UTC")
        let stamp = formatter.string(from: Date())

        let dir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("trials", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appendingPathComponent("trial-\(stamp).json")

        var lines: [String] = ["["]
        for (index, s) in sorted.enumerated() {
            let v = "[\(s.v.0), \(s.v.1), \(s.v.2)]"
            let comma = index == sorted.count - 1 ? "" : ","
            lines.append(
                "  {\"t\": \(s.tSeconds), \"role\": \"proximal\", \"sensor\": \"\(s.sensor)\", " +
                "\"v\": \(v), \"phone_ts_ms\": \(s.phoneTsMs)}\(comma)"
            )
        }
        lines.append("]")

        do {
            try lines.joined(separator: "\n").write(to: file, atomically: true, encoding: .utf8)
            return file
        } catch {
            return nil
        }
    }
}

private extension CMAcceleration {
    var tuple: (Double, Double, Double) { (x, y, z) }
}
private extension CMRotationRate {
    var tuple: (Double, Double, Double) { (x, y, z) }
}
private extension CMMagneticField {
    var tuple: (Double, Double, Double) { (x, y, z) }
}
