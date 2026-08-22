package com.pendulastic.harness

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/**
 * Bare start/stop UI for U0's throwaway capture harness. No participant model,
 * no calibration, no scoring — this app exists only to get real on-device
 * accel/gyro/mag data onto disk for the KTD3 shadow study to consume offline.
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val recorder = SampleRecorder(applicationContext)
        setContent {
            MaterialTheme {
                Surface(modifier = Modifier.fillMaxSize()) {
                    HarnessScreen(recorder)
                }
            }
        }
    }
}

@Composable
fun HarnessScreen(recorder: SampleRecorder) {
    var recording by remember { mutableStateOf(false) }
    var lastStatus by remember { mutableStateOf("Idle.") }

    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("Pendulastic Capture Harness (throwaway)")
        Text(lastStatus)
        Button(onClick = {
            if (!recording) {
                recorder.start()
                recording = true
                lastStatus = "Recording…"
            } else {
                val file = recorder.stop()
                recording = false
                lastStatus = "Saved ${recorder.sampleCount} samples to ${file.absolutePath}"
            }
        }) {
            Text(if (recording) "Stop" else "Start")
        }
    }
}
