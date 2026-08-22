package com.pendulastic.harness

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import java.io.File
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.TimeZone

/**
 * Throwaway capture-only recorder for U0 (see docs/plans/2026-08-21-001-feat-phone-imu-pendulum-app-plan.md).
 *
 * Writes one JSON array of samples per trial, each shaped as
 * {t, role, sensor, v, phone_ts_ms} to match imu_calibration_tuner.py's
 * replay_trial() input contract. `role` is fixed to "proximal" — that is
 * the role replay_trial()'s solo-fallback path (is_solo = not has_distal
 * and role == ROLE_PROXIMAL) expects for a single-phone capture; a "distal"
 * role with no proximal counterpart would NOT trigger that fallback.
 *
 * No calibration, no scoring, no participant model. This is not reused by
 * U3-U13 — see U0's plan entry for why.
 */
class SampleRecorder(private val context: Context) : SensorEventListener {

    private val sensorManager = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
    private val accel = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER)
    private val gyro = sensorManager.getDefaultSensor(Sensor.TYPE_GYROSCOPE)
    private val mag = sensorManager.getDefaultSensor(Sensor.TYPE_MAGNETIC_FIELD)

    private val samples = mutableListOf<Sample>()
    private var recording = false
    private var startElapsedNanos = 0L

    val sampleCount: Int get() = samples.size

    private data class Sample(val tSeconds: Double, val sensor: String, val v: FloatArray, val phoneTsMs: Long)

    fun start() {
        samples.clear()
        // SensorEvent.timestamp is nanoseconds since boot (monotonic, not wall-clock) —
        // per KTD5/R1, this is the timestamp used for all dt math, never arrival time.
        startElapsedNanos = 0L
        recording = true
        // SENSOR_DELAY_FASTEST requests the highest rate the driver supports; U1/U2 need
        // ~100Hz, not the 800Hz CMBatchedSensorManager-equivalent path (KTD5) — FASTEST on
        // typical Android accel/gyro drivers lands in the low hundreds of Hz, which is fine.
        accel?.let { sensorManager.registerListener(this, it, SensorManager.SENSOR_DELAY_FASTEST) }
        gyro?.let { sensorManager.registerListener(this, it, SensorManager.SENSOR_DELAY_FASTEST) }
        mag?.let { sensorManager.registerListener(this, it, SensorManager.SENSOR_DELAY_FASTEST) }
    }

    fun stop(): File {
        recording = false
        sensorManager.unregisterListener(this)
        return writeTrialFile()
    }

    override fun onSensorChanged(event: SensorEvent) {
        if (!recording) return
        if (startElapsedNanos == 0L) startElapsedNanos = event.timestamp
        val tSeconds = (event.timestamp - startElapsedNanos) / 1_000_000_000.0
        val phoneTsMs = event.timestamp / 1_000_000L
        val sensorName = when (event.sensor.type) {
            Sensor.TYPE_ACCELEROMETER -> "accel"
            Sensor.TYPE_GYROSCOPE -> "gyro"
            Sensor.TYPE_MAGNETIC_FIELD -> "mag"
            else -> return
        }
        samples.add(Sample(tSeconds, sensorName, event.values.copyOf(3), phoneTsMs))
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {
        // Not scored by the harness — U1's stillness/bias logic is what cares about
        // signal quality, and this is deliberately not ported here.
    }

    private fun writeTrialFile(): File {
        // Chronological order isn't guaranteed across sensor callback types (per U6's
        // Approach note) — sort before writing so downstream replay_trial() gets a
        // pre-sorted list, matching its documented contract.
        val sorted = samples.sortedBy { it.tSeconds }
        val stamp = SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }.format(System.currentTimeMillis())
        // App-specific external storage — no runtime permission needed on any supported
        // API level, and still reachable via `adb pull` or a file manager for retrieval.
        val dir = File(context.getExternalFilesDir(null), "trials").apply { mkdirs() }
        val file = File(dir, "trial-$stamp.json")
        file.bufferedWriter().use { out ->
            out.write("[\n")
            sorted.forEachIndexed { index, s ->
                val v = "[${s.v[0]}, ${s.v[1]}, ${s.v[2]}]"
                out.write(
                    "  {\"t\": ${s.tSeconds}, \"role\": \"proximal\", \"sensor\": \"${s.sensor}\", " +
                        "\"v\": $v, \"phone_ts_ms\": ${s.phoneTsMs}}"
                )
                out.write(if (index == sorted.lastIndex) "\n" else ",\n")
            }
            out.write("]\n")
        }
        return file
    }
}
