import SwiftUI

/// Bare start/stop UI for U0's throwaway capture harness. No participant
/// model, no calibration, no scoring — this app exists only to get real
/// on-device accel/gyro/mag data onto disk for the KTD3 shadow study to
/// consume offline.
struct ContentView: View {
    private let recorder = SampleRecorder()
    @State private var recording = false
    @State private var status = "Idle."

    var body: some View {
        VStack(spacing: 24) {
            Text("Pendulastic Capture Harness (throwaway)")
                .font(.headline)
            Text(status)
                .multilineTextAlignment(.center)
            Button(recording ? "Stop" : "Start") {
                if recording {
                    if let file = recorder.stop() {
                        status = "Saved \(recorder.sampleCount) samples to \(file.path)"
                    } else {
                        status = "Failed to write trial file."
                    }
                } else {
                    recorder.start()
                    status = "Recording…"
                }
                recording.toggle()
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(24)
    }
}
