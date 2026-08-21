# PENDULASTIC DEVELOPMENT SPECIFICATION: HYBRID SENSOR FUSION & CLINICAL BIOMECHANICS ENGINE
## Systems Engineering & Algorithmic Blueprint for Claude Code

This specification compiles the physical, mathematical, and architectural requirements of the **Pendulastic Platform** [1, 2]. It serves as a unified context document to initialize and guide **Claude Code** (or any autonomous agent) in implementing, debugging, and refining your clinical-grade goniometry pipelines, thread-safe network handlers, and Popović parameter scoring engines [2, 70, 71, 149].

---

### SECTION 1: SYSTEM ENVIRONMENT & ARCHITECTURE

#### 1.1 Local Development Context
*   **Operating System:** Windows 11 (Surface Laptop) [640].
*   **Active Subsystem:** Ubuntu (WSL) utilized for native C++ model builds and system-level dependencies [640, 595].
*   **Primary Toolchain:** Python 3.10+ virtual environment (`.venv`) managed via PowerShell and integrated with VS Code [631, 640].
*   **Core Packages:** `numpy`, `scipy`, `pandas`, `scikit-learn`, `opencv-python`, `mediapipe`, `matplotlib` [640, 915].
*   **External Hardware Interfaces:** 8-camera OptiTrack Motion Capture System (Vicon/Motive C3D exports at 100 Hz–200 Hz), consumer smartphone cameras (30 FPS/60 FPS), and 9-axis smartphone IMUs (accelerometer, gyroscope, magnetometer streaming via Sensor Stream Pro over UDP) [47, 54, 55, 110, 111, 555].

#### 1.2 Directory Layout & Asset Hygiene
The codebase enforces a highly organized directory structure. Out-of-tree cache files and high-volume temporary images are automatically pruned via `cleanup.py` to prevent Git tracking pollution and context window bloat [631, 641, 643]:
```
/workspace/
├── data/
│   ├── raw/                           ← Raw video files (.mp4, .avi) and Motive CSVs [631, 641]
│   └── temp_frames/                   ← Temporary extracted frames (purged via cleanup.py) [631, 641]
├── hpe_csv/                           ← Extracted 2D coordinate/landmark trajectory logs [611, 771]
├── models/
│   └── calibration/                   ← Serialized regressors (*.pkl) and fitting statistics [631, 641]
├── training_data/                     ← Group-split datasets and aligned COCO annotations [633, 720]
└── src/                               ← Application source files (pendulastic_app.py, etc.) [623, 670]
```

---

### SECTION 2: BIOMECHANICAL MODELS & VECTOR MATH

#### 2.1 Single-Segment Tibial Goniometry (The Ockendon Model)
To avoid femoral skin-sliding artifacts and the clinical difficulty of securely instrumenting a patient's thigh in a clinical setting, knee flexion/extension ($\kappa$) can be computed dynamically from the tibial inclination angle ($\beta$) alone relative to gravity [1110, 1116]:
*   **Assumed Femur-to-Tibia Ratio ($f$):** Constant scaling coefficient set to $f = 1.2t$ (where $t$ is tibia length) [1116].
*   **Trigonometric Transfer Function:**
    $$\kappa = 90^\circ + \beta - \arccos\left(\frac{\sin \beta}{1.2}\right)$$ [1116]
*   **Calibration Convention:** Upright standing or fully extended anatomical knee position maps to $180^\circ$ (interior angle), while flexion decreases towards $90^\circ$ [626, 1116, 1117].

#### 2.2 2D Image-Plane Keypoint Goniometry
When tracking from a monocular side-profile view (sagittal plane), joint angles must be calculated using 2D pixel coordinates (not the pseudo-3D "world" coordinates projected by MediaPipe, which suffer from severe depth-distortion when a patient is seated) [501, 717, 723]:
1.  **Coordinate Aspect Scaling:** To neutralize non-square camera sensor aspect ratios, raw normalized landmarks $(\hat{x}, \hat{y}) \in [0, 1]$ must be scaled to pixel dimensions $(W, H)$ before constructing vectors [769, 770]:
    $$x = \hat{x} \cdot W, \quad y = \hat{y} \cdot H$$ [770]
2.  **Vector Construction:** For the Hip ($H$), Knee ($K$), and Ankle ($A$) keypoints, define the thigh and shank segments [669, 694]:
    $$\vec{u} = H - K, \quad \vec{v} = A - K$$ [669, 694]
3.  **Angle Extraction via Quadrant-Corrected Arctangent:** Avoid basic cosine formulas which break when the leg swings past the vertical axis during hyper-extension. Use `atan2` to maintain mathematical continuity over the entire $360^\circ$ swing [275, 277]:
    $$\theta = \left( \operatorname{atan2}(v_y, v_x) - \operatorname{atan2}(u_y, u_x) \right) \times \frac{180}{\pi}$$
    $$\text{knee\_angle\_deg} = \theta \pmod{360}$$

#### 2.3 Image-Space Gating & Left/Right Sorting
To prevent the "wrong leg" tracking bug during bilateral assessments, completely bypass MediaPipe's anatomical left/right labels (which flip depending on whether the subject faces toward or away from the camera lens) [308, 900]:
*   Compare the horizontal coordinates ($x$) of the detected left and right knees [308, 900].
*   Assign tracking targets programmatically based on the manual user configuration (`--leg left` or `--leg right`) mapped directly to image-space boundaries [885, 912, 913]:
    $$\text{Target Knee (Left Selection)} = \min(x_{\text{left\_knee}}, x_{\text{right\_knee}})$$ [884, 900]
    $$\text{Target Knee (Right Selection)} = \max(x_{\text{left\_knee}}, x_{\text{right\_knee}})$$

---

### SECTION 3: SIGNAL CONDITIONING & HARDWARE SYNCHRONIZATION

#### 3.1 Dual-Threshold Schmitt-Trigger Latch
To prevent static background edges (such as clinical tables, parallel bars, or passing clinicians) from hijacking the keypoint trackers, implement a hysteresis control loop inside the tracking step [153, 877]:
*   **The Freeze State ($\text{motion\_level} < 5.0$):** When the limb is stationary before release, lock the tracker position and set velocity to $0.0$. Ignore all background perpendicular variance [872, 876, 877].
*   **The Latch Trigger ($\text{motion\_level} \ge 15.0$):** Transition to active tracking only after **3 consecutive frames** exceed the motion threshold [872, 876, 877]. This deadband prevents sudden shadows or momentary hand occlusions from causing premature release triggers [877].

#### 3.2 Dynamic Auto-Tare Calibration
To establish an accurate anatomical baseline, the first incoming data packets during the pre-release static holding phase (or the countdown timer) are averaged to calculate a zero-point offset [153, 626]:
$$\theta_{\text{offset}} = 180^\circ - \theta_{\text{raw\_initial}}$$ [153, 626]
$$\theta_{\text{calibrated}} = \theta_{\text{raw}} + \theta_{\text{offset}}$$

#### 3.3 Sliding Cross-Correlation Temporal Alignment
Because the camera streams (30/60 FPS) and the OptiTrack system (100 Hz) operate on separate, unsynchronized physical clocks, raw data suffers from up to 4 seconds of hardware clock drift [916, 918].
*   **The Search Window:** Sweep a frame-shift lag window ($L$) from **$-138$ to $+116$ frames** [916, 918].
*   **Joint Optimization:** To resolve camera orientation mirroring, jointly evaluate both standard and flipped coordinates ($180^\circ - \theta$) across all lags to locate the maximum absolute Pearson correlation coefficient ($|r| \ge 0.90$) [494, 500, 522]:
    $$r(L) = \frac{\sum (x_t - \bar{x})(y_{t-L} - \bar{y})}{\sqrt{\sum (x_t - \bar{x})^2 \sum (y_{t-L} - \bar{y})^2}}$$

---

### SECTION 4: THE POPOVIĆ 7-PARAMETER SCORING ENGINE

To objectively quantify spasticity levels during the Knee Pendulum Test, extract 7 distinct kinematic parameters from the filtered goniogram curve ($\theta(t)$ smoothed with a 3rd-order, 15-frame Savitzky-Golay filter) [12, 153]:

```
Angle (°)
  180° ──┐  ▲ (Initial Drop: A₀)
         │  │ 
         ▼  │                                   ─── Settle Baseline (φ_inf)
            │      ▲ (First Rebound: A₁)
            │      │ 
            ▼      ▼
  90° ─────────────────────────────────────────
         0       1       2       3       4     Time (s)
```

1.  **Normalized Relaxation Index ($R_{2n}$):** Standardized index of overall joint spasticity [455]:
    $$R_{2n} = \frac{A_1}{1.6 A_0}$$ [479]
    *(Note: Add epsilon $\epsilon = 1e-5$ to denominators to prevent division-by-zero on completely stiff joints).*
2.  **Number of Swings ($N$):** Count of complete oscillations crossing the resting baseline. Apply a prominence threshold of $\ge 2.0^\circ$ and a minimum peak-to-peak separation of 10 frames (~100 ms) to filter out muscular tremors [152, 153, 421].
3.  **First Flexion Rebound ($A_1$):** Peak extension recoil angle [421].
    *   *Pathognomonic Sign:* In severe extensor hypertonia, the quadriceps fire an active, involuntary stretch reflex. If the leg is arrested before crossing below the resting baseline ($\phi_\infty$), $A_{1,\text{flex}}$ becomes negative, signaling a severe neuromuscular catch [935, 937, 941].
4.  **Maximum Flexion Velocity ($\omega_{\max}$):** First derivative peak ($d\theta/dt$) during the initial drop [152, 422].
5.  **Minimum Extension Velocity ($\omega_{\min}$):** Peak return stroke velocity [152, 422].
6.  **Oscillation Frequency ($f$):** Natural frequency calculated from the period of the first two completed cycles ($f = 1 / T$) [422].
7.  **Symmetry / Area Ratio ($A_{\text{ratio}}$):** The relative balance of flexion vs. extension motion. To avoid "integration leak" where low-frequency wander in the static "dead tail" inflates the score, integrate strictly within a **dynamic active-window mask** bounded by the initial release and the final dampening threshold [930]:
    $$A_{\text{ratio}} = \frac{\int_{t_{\text{start}}}^{t_{\text{end}}} |\theta(t)_+ - \phi_{\infty}| \, dt - \int_{t_{\text{start}}}^{t_{\text{end}}} |\theta(t)_- - \phi_{\infty}| \, dt}{\int_{t_{\text{start}}}^{t_{\text{end}}} |\theta(t) - \phi_{\infty}| \, dt}$$

---

### SECTION 5: NETWORKING & PROTOCOL ROBUSTNESS

#### 5.1 Dependency-Free RFC 6455 WebSocket Server
To completely eliminate virtual environment package conflicts (such as missing `websockets` or `asyncio` sub-modules on corporate clinical machines), the IMU server runs on a **dependency-free RFC 6455 WebSocket handshaker** written entirely on native `asyncio.start_server` [560, 607]:
*   **Port Ownership:** Handshake port **5000**, goniometer UDP listener port **8888** [70, 71].
*   **Hotspot binding:** Actively ranks local network adapters, automatically binding to the standard iPhone Hotspot gateway adapter (`172.20.10.2`) [601, 602].
*   **Bypass Tooling:** Includes a Tkinter helper that copies the exact elevated firewall bypass command to the system clipboard [561, 563]:
    `New-NetFirewallRule -DisplayName "Pendulastic IMU 5000" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5000 -Program "<path_to_venv_python.exe>"` [561, 563]

#### 5.2 Thread-Safe UI Dispatcher
Because background socket threads cannot safely update Tkinter canvas or label widgets directly, coordinate payloads must be piped through a thread-safe `queue.Queue` [72, 607]:
```python
# Inside main thread polling loop (50ms interval)
def poll_telemetry_queue(self):
    try:
        while not self.telemetry_queue.empty():
            packet = self.telemetry_queue.get_nowait()
            self.biomechanical_engine.process_packet(packet)
            self.update_live_sparkline()
    except queue.Empty:
        pass
    self.root.after(50, self.poll_telemetry_queue)
```

---

### SECTION 6: RIGOROUS MANUSCRIPT-GRADE STANDARDS

To maintain academic credibility in publication and peer review, all code, charts, and statistics must adhere to these structural policies [310, 312]:
1.  **Leave-One-Trial-Out (LOTO) Cross-Validation:** To avoid overfitting to individual physical profiles (body mass, leg dimensions), never split training data randomly [498, 523]. Always train models on $N-1$ trials and validate on the completely held-out trial of the remaining subject [498, 523].
2.  **No Clinical Fabrications:** Never hardcode mock patient identifiers, simulated diagnostic statements, or fictional doctor names into the data structures [Policies]. Stated clinical conditions must trace strictly to the metadata files [Policies].
3.  **Vector Asset Hygiene:** Matplotlib figures must be generated with `plt.rcParams['pdf.fonttype'] = 42` to output text as editable vector paths rather than flattened bitmaps, preserving resolution during final journal layout adjustments [939, 948].
4.  **Objective Code Context:** Do not include first-person comments or personal logs within production code headers. Maintain a clean, academic, third-person framework perspective [290, 357].
