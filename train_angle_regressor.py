#!/usr/bin/env python3
"""
train_angle_regressor.py

Trains a MobileNetV3-Small knee-angle regressor on the Pendulastic clinical
dataset.  This replaces the attempted MediaPipe Model Maker pose-landmarker
fine-tune (which is not supported in mediapipe_model_maker 0.2.1.4).

WHAT IT DOES
────────────
Instead of fine-tuning keypoint coordinates, this model learns to predict the
interior knee angle directly from a video frame crop centred on the knee:

    input  : 224×224 RGB crop around knee0  (from the COCO dataset)
    output : knee interior angle [80°, 185°]  (from OptiTrack, via COCO kps)

Directly optimising angle rather than pixel locations is architecturally
simpler, compatible with TF 2.21 in the existing .venv, and directly measures
our target metric: angular RMSE < 3°.

HYPERPARAMETER STRATEGY  (as specified in research plan)
─────────────────────────────────────────────────────────
1. Learning-rate schedule
   • Phase 1 (frozen backbone)   : lr = 0.001,  5 epochs
     All MobileNetV3 layers frozen; only the regression head trains.
     Gets the head oriented before touching the backbone weights.
   • Phase 2 (partial unfreeze)  : CosineDecayRestarts from lr = 0.001
     down to lr = 0.0001 over MAX_EPOCHS, restarting every 10 epochs.
     Unfreeze the last UNFREEZE_LAYERS of MobileNetV3.
     Warm restarts help escape shallow local minima in the clinical geometry.

2. Batch size / Early stopping
   • Batch size: 32 (or 16 on memory-limited machines; see --batch-size)
   • EarlyStopping(monitor='val_mae_deg', patience=PATIENCE, restore_best_weights=True)
   • ReduceLROnPlateau as a secondary fallback for stubborn plateaus.

3. Architecture
   • MobileNetV3-Small: compact, fast inference in the viewer, good transfer.
   • Unfreeze the last UNFREEZE_LAYERS layers (default 30) in Phase 2.
   • Head: GlobalAvgPool → Dropout(0.3) → Dense(128, swish) → Dense(1, linear)
   • Output NOT clipped during training; OAC enforced at inference.

4. Physics-informed loss
   • Primary  : Huber(delta=5°) — MSE in the linear region, MAE beyond 5°.
     Huber's linear tail down-weights gross-error frames (blurs, occlusions)
     without ignoring them entirely.
   • Auxiliary : shank-length penalty — computed from predicted vs. expected
     pixel shank length (OLC).  Applied with weight ALPHA_OLC = 0.05.
     This is physics-informed: the shank length is fixed per trial.

5. Data augmentation  (all label-invariant for interior knee angle)
   • Brightness / contrast jitter  : simulates varying clinical lighting.
   • Gaussian noise                : simulates video compression artefacts.
   • Rotation ±ROT_MAX_DEG         : interior angle is rotation-invariant
                                     so no label adjustment needed.
   • Crop jitter ±CROP_JITTER_PX   : simulates small knee-tracking errors.
   No horizontal flip (would swap left/right ankle side inconsistently).

OUTPUTS
───────
  models/angle_regressor/           TF SavedModel  (use in viewer)
  models/angle_regressor.tflite     INT8-quantised TFLite  (optional)
  models/angle_regressor_metrics.json
  training_data/crops/              cached 224×224 JPEG crops (optional)

VIEWER INTEGRATION
──────────────────
  After training, enable the regressor in the viewer:
    python pendulastic_viewer.py --angle-model models/angle_regressor

  The viewer will use MediaPipe for keypoint visualisation and the regressor
  for the angle fed into the PT-score calculation.
"""

import argparse
import json
import os
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")

# On Windows, stdout/stderr default to the cp1252 codepage when not attached
# to a real console (e.g. redirected to a file or piped), which cannot
# encode characters like the arrow/checkmark used in this script's progress
# output and crashes with UnicodeEncodeError partway through training.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

import cv2
import numpy as np
import pandas as pd

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT      = Path(__file__).parent
DATA_ROOT = ROOT / "training_data"
ANN_DIR   = DATA_ROOT / "annotations"
IMG_DIR   = DATA_ROOT / "images"
CROP_DIR  = DATA_ROOT / "crops"
MODEL_DIR = ROOT / "models"

COCO_JSON     = ANN_DIR / "coco_keypoints.json"
OUTPUT_MODEL  = MODEL_DIR / "angle_regressor"
OUTPUT_TFLITE = MODEL_DIR / "angle_regressor.tflite"
METRICS_JSON  = MODEL_DIR / "angle_regressor_metrics.json"

# ── Hyperparameters (defaults; override via CLI) ──────────────────────────────

CROP_SIZE_PX    = 380       # pixel crop around knee before resize
NET_INPUT       = 224       # MobileNetV3 input side
ANGLE_MIN       = 80.0      # OAC lower bound (degrees)
ANGLE_MAX       = 185.0     # OAC upper bound (degrees)

HEAD_LR         = 1e-3      # Phase 1 learning rate (head only)
HEAD_EPOCHS     = 5         # Phase 1 epochs
# Phase 2 fine-tunes unfrozen pretrained backbone layers, not a fresh head --
# using HEAD_LR's magnitude here overwrote the pretrained MobileNetV3
# features on the very first Phase 2 epoch: a run with this same value
# jumped from Phase 1's 25.5 deg val RMSE to 32.4 deg at Phase 2 epoch 1 and
# kept climbing every epoch after (35.0, 38.0, 39.5 deg) with no sign of
# recovering. 10x lower is the standard fine-tuning-vs-head-training ratio.
FINETUNE_LR     = 1e-4      # Phase 2 initial LR (CosineDecayRestarts peak)
FINETUNE_LR_MIN = 1e-5      # Phase 2 cosine minimum
UNFREEZE_LAYERS = 30        # Phase 2: unfreeze this many layers from the top
MAX_EPOCHS      = 60        # Phase 2 maximum epochs
PATIENCE        = 6         # EarlyStopping patience (epochs)
BATCH_SIZE      = 32

ROT_MAX_DEG     = 10.0      # max rotation for augmentation (degrees)
CROP_JITTER_PX  = 15        # max shift of crop centre (pixels)
ALPHA_OLC       = 0.05      # shank-length penalty weight

HUBER_DELTA     = 5.0       # Huber loss delta (degrees)

# Participants held out for validation (match gen_training_data.py)
VAL_PARTICIPANTS = {
    "Participant_8_left_control",
    "Participant_8_right_control",
    "Participant_5_left_side",
    "Participant_5_right_side",
}

# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--batch-size",    type=int,   default=BATCH_SIZE)
    p.add_argument("--head-epochs",   type=int,   default=HEAD_EPOCHS)
    p.add_argument("--max-epochs",    type=int,   default=MAX_EPOCHS)
    p.add_argument("--patience",      type=int,   default=PATIENCE)
    p.add_argument("--unfreeze",      type=int,   default=UNFREEZE_LAYERS,
                   help="Number of backbone layers to unfreeze in Phase 2")
    p.add_argument("--cache-crops",   action="store_true",
                   help="Pre-extract and cache 224×224 crops to disk (faster subsequent runs)")
    p.add_argument("--no-tflite",     action="store_true",
                   help="Skip INT8 TFLite quantisation export")
    p.add_argument("--alpha-olc",     type=float, default=ALPHA_OLC)
    return p.parse_args()


# ── Angle utility ─────────────────────────────────────────────────────────────

def _angle_from_kps(kps):
    """Compute interior knee angle from COCO 17-keypoint array (51 floats)."""
    # Determine leg from which keypoints are labeled (v=2)
    right_v = kps[12*3+2] + kps[14*3+2] + kps[16*3+2]
    left_v  = kps[11*3+2] + kps[13*3+2] + kps[15*3+2]
    if right_v >= left_v:
        h = np.array([kps[12*3], kps[12*3+1]])
        k = np.array([kps[14*3], kps[14*3+1]])
        a = np.array([kps[16*3], kps[16*3+1]])
    else:
        h = np.array([kps[11*3], kps[11*3+1]])
        k = np.array([kps[13*3], kps[13*3+1]])
        a = np.array([kps[15*3], kps[15*3+1]])
    v1 = h - k;  v2 = a - k
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return None
    cos_a = np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0)
    return float(np.degrees(np.arccos(cos_a)))


def _knee_xy_from_kps(kps):
    """Return (x, y) of the labeled knee keypoint."""
    if kps[14*3+2] > 0:
        return kps[14*3], kps[14*3+1]
    return kps[13*3], kps[13*3+1]


def _shank_len_from_kps(kps):
    """Return pixel shank length from labeled keypoints."""
    if kps[14*3+2] > 0:
        k = np.array([kps[14*3], kps[14*3+1]])
        a = np.array([kps[16*3], kps[16*3+1]])
    else:
        k = np.array([kps[13*3], kps[13*3+1]])
        a = np.array([kps[15*3], kps[15*3+1]])
    return float(np.linalg.norm(a - k))


# ── Dataset loading ───────────────────────────────────────────────────────────

import re

def _participant_from_filename(fname):
    m = re.match(r"(Participant_\w+)_T\d+_frame_", fname)
    return m.group(1) if m else "unknown"


def load_samples(coco_json_path=COCO_JSON):
    """
    Returns a list of dicts:
      image_path, knee_x, knee_y, angle_deg, shank_len_px, participant, split
    """
    with open(coco_json_path, encoding="utf-8") as f:
        coco = json.load(f)

    id_to_img = {img["id"]: img for img in coco["images"]}
    samples = []
    for ann in coco["annotations"]:
        img = id_to_img[ann["image_id"]]
        kps = ann["keypoints"]

        # Prefer the OptiTrack angle stored directly in the annotation
        # (set by gen_training_data.py v2+).  Fall back to geometric
        # computation from keypoints for datasets generated by older versions.
        ang = ann.get("knee_angle_deg")
        if ang is None:
            ang = _angle_from_kps(kps)
        if ang is None or not (ANGLE_MIN <= ang <= ANGLE_MAX):
            continue

        kx, ky = _knee_xy_from_kps(kps)
        sl     = _shank_len_from_kps(kps)
        pid    = _participant_from_filename(img["file_name"])
        samples.append(dict(
            image_path  = IMG_DIR / img["file_name"],
            knee_x      = float(kx),
            knee_y      = float(ky),
            angle_deg   = ang,
            shank_len   = sl,
            participant = pid,
            split       = "val" if pid in VAL_PARTICIPANTS else "train",
            img_w       = img["width"],
            img_h       = img["height"],
        ))
    return samples


# ── Crop + augmentation ───────────────────────────────────────────────────────

def _crop(bgr, kx, ky, w, h, jitter_px=0, rot_deg=0.0):
    """
    Crop CROP_SIZE_PX × CROP_SIZE_PX around (kx, ky), optionally with jitter
    and rotation; returns 224×224 RGB float32 in [0,1].
    """
    kx += int(np.random.uniform(-jitter_px, jitter_px)) if jitter_px else 0
    ky += int(np.random.uniform(-jitter_px, jitter_px)) if jitter_px else 0
    half = CROP_SIZE_PX // 2
    x0, y0 = int(kx) - half, int(ky) - half
    x1, y1 = x0 + CROP_SIZE_PX, y0 + CROP_SIZE_PX

    # Pad to ensure crop is fully valid
    pad = max(0, -x0, -y0, x1 - w, y1 - h)
    if pad > 0:
        bgr = cv2.copyMakeBorder(bgr, pad, pad, pad, pad, cv2.BORDER_REFLECT)
        x0 += pad; y0 += pad; x1 += pad; y1 += pad

    crop = bgr[y0:y1, x0:x1].copy()

    if abs(rot_deg) > 0.1:
        cx, cy = CROP_SIZE_PX // 2, CROP_SIZE_PX // 2
        M = cv2.getRotationMatrix2D((cx, cy), rot_deg, 1.0)
        crop = cv2.warpAffine(crop, M, (CROP_SIZE_PX, CROP_SIZE_PX),
                              flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REFLECT)

    crop = cv2.resize(crop, (NET_INPUT, NET_INPUT))
    return cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0


def _augment_image(img_f32):
    """Brightness/contrast + Gaussian noise; label-invariant."""
    alpha = float(np.random.uniform(0.7, 1.3))   # contrast
    beta  = float(np.random.uniform(-0.10, 0.10)) # brightness
    img_f32 = np.clip(img_f32 * alpha + beta, 0.0, 1.0)
    noise = np.random.normal(0, 0.01, img_f32.shape).astype(np.float32)
    return np.clip(img_f32 + noise, 0.0, 1.0)


# ── tf.data pipeline ──────────────────────────────────────────────────────────

def build_dataset(samples, batch_size, augment=False, cache_crops=False):
    """Build a tf.data.Dataset from sample dicts."""
    import tensorflow as tf

    # Pre-normalise angle to [0, 1]
    def _norm(a):
        return (a - ANGLE_MIN) / (ANGLE_MAX - ANGLE_MIN)

    paths  = [str(s["image_path"]) for s in samples]
    kxs    = np.array([s["knee_x"]    for s in samples], dtype=np.float32)
    kys    = np.array([s["knee_y"]    for s in samples], dtype=np.float32)
    angles = np.array([_norm(s["angle_deg"]) for s in samples], dtype=np.float32)
    shanks = np.array([s["shank_len"] for s in samples], dtype=np.float32)
    ws     = np.array([s["img_w"]     for s in samples], dtype=np.int32)
    hs     = np.array([s["img_h"]     for s in samples], dtype=np.int32)

    def _load(path, kx, ky, ang, sl, iw, ih):
        bgr = cv2.imread(path.numpy().decode())
        if bgr is None:
            return np.zeros((NET_INPUT, NET_INPUT, 3), np.float32), ang, sl

        rot_deg   = float(np.random.uniform(-ROT_MAX_DEG, ROT_MAX_DEG)) if augment else 0.0
        jitter    = CROP_JITTER_PX if augment else 0
        img_f32   = _crop(bgr, float(kx), float(ky), int(iw), int(ih),
                          jitter_px=jitter, rot_deg=rot_deg)
        if augment:
            img_f32 = _augment_image(img_f32)
        # MobileNetV3 preprocessing: scale to [-1, 1]
        img_f32 = img_f32 * 2.0 - 1.0
        return img_f32.astype(np.float32), np.float32(ang), np.float32(sl)

    def _tf_load(path, kx, ky, ang, sl, iw, ih):
        img, a, s = tf.py_function(
            _load, [path, kx, ky, ang, sl, iw, ih],
            [tf.float32, tf.float32, tf.float32]
        )
        img.set_shape([NET_INPUT, NET_INPUT, 3])
        a.set_shape([])
        s.set_shape([])
        return img, {"angle": a, "shank": s}

    ds = tf.data.Dataset.from_tensor_slices(
        (paths, kxs, kys, angles, shanks, ws, hs)
    )
    if augment:
        ds = ds.shuffle(buffer_size=min(5000, len(samples)), reshuffle_each_iteration=True)

    ds = ds.map(_tf_load, num_parallel_calls=tf.data.AUTOTUNE)

    if cache_crops:
        CROP_DIR.mkdir(exist_ok=True)
        ds = ds.cache(str(CROP_DIR / ("train" if augment else "val")))

    ds = ds.batch(batch_size).prefetch(tf.data.AUTOTUNE)
    return ds


# ── Model ─────────────────────────────────────────────────────────────────────

def build_model(unfreeze_layers=0):
    """
    MobileNetV3-Small pretrained on ImageNet.
    unfreeze_layers=0 → all frozen (Phase 1, head only).
    unfreeze_layers=N → last N layers unfrozen (Phase 2).
    """
    import tensorflow as tf
    from tensorflow import keras

    base = keras.applications.MobileNetV3Small(
        include_top   = False,
        input_shape   = (NET_INPUT, NET_INPUT, 3),
        weights       = "imagenet",
        minimalistic  = False,
    )
    base.trainable = False

    if unfreeze_layers > 0:
        for layer in base.layers[-unfreeze_layers:]:
            layer.trainable = True

    x = base.output
    x = keras.layers.GlobalAveragePooling2D()(x)
    x = keras.layers.Dropout(0.30)(x)
    x = keras.layers.Dense(128, activation="swish")(x)
    angle_out = keras.layers.Dense(1, name="angle")(x)   # normalised [0,1]
    shank_out = keras.layers.Dense(1, name="shank")(x)   # shank length in px (for OLC)

    return keras.Model(inputs=base.input, outputs={"angle": angle_out, "shank": shank_out})


# ── Loss + metrics ────────────────────────────────────────────────────────────

def build_loss_and_metrics(alpha_olc, median_shank_px):
    """
    Returns (loss_dict, loss_weights, metrics_dict) for model.compile().

    Angle head : Huber(delta=HUBER_DELTA/angle_range) — physics-driven scale
    Shank head : MSE against median shank length (OLC penalty)
    """
    import tensorflow as tf

    angle_range = ANGLE_MAX - ANGLE_MIN   # 105 degrees

    # Normalised Huber delta
    huber_delta_norm = HUBER_DELTA / angle_range

    class HuberAngle(tf.keras.losses.Loss):
        def call(self, y_true, y_pred):
            return tf.keras.losses.huber(y_true, y_pred, delta=huber_delta_norm)

    # Shank loss: penalise deviation from the per-dataset median (OLC)
    class ShankOLC(tf.keras.losses.Loss):
        def call(self, y_true, y_pred):
            # Squared error in normalised (fraction-of-image-width) space, not
            # raw px^2 -- raw px^2 runs 1e4-1e5 vs the angle head's Huber loss
            # at ~1e-2, so even with alpha_olc's 0.05 weight the shank task's
            # gradient dominated the shared MobileNetV3 trunk and starved the
            # angle head of signal (observed: shank_loss fell steadily while
            # val_angle_rmse_deg plateaued at 25-27 deg, nowhere near the <3
            # deg target). Normalising both losses to comparable scale lets
            # alpha_olc actually control the OLC penalty's relative weight.
            pred_norm = tf.squeeze(y_pred, axis=-1)
            true_norm = y_true / 1920.0   # per-sample px length -> same scale as pred
            return tf.reduce_mean(tf.square(pred_norm - true_norm))

    class MAEDeg(tf.keras.metrics.Metric):
        """Mean absolute error in degrees (for monitoring)."""
        def __init__(self, name="mae_deg", **kw):
            super().__init__(name=name, **kw)
            self._sum = self.add_weight(name="sum", initializer="zeros")
            self._cnt = self.add_weight(name="cnt", initializer="zeros")

        def update_state(self, y_true, y_pred, sample_weight=None):
            pred  = tf.squeeze(y_pred, axis=-1)
            err   = tf.abs(pred - y_true) * angle_range
            self._sum.assign_add(tf.reduce_sum(err))
            self._cnt.assign_add(tf.cast(tf.size(err), tf.float32))

        def result(self):
            return self._sum / (self._cnt + 1e-9)

        def reset_state(self):
            self._sum.assign(0.); self._cnt.assign(0.)

    class RMSEDeg(tf.keras.metrics.Metric):
        """RMSE in degrees — our primary success metric (target < 3°)."""
        def __init__(self, name="rmse_deg", **kw):
            super().__init__(name=name, **kw)
            self._sq  = self.add_weight(name="sq",  initializer="zeros")
            self._cnt = self.add_weight(name="cnt", initializer="zeros")

        def update_state(self, y_true, y_pred, sample_weight=None):
            pred  = tf.squeeze(y_pred, axis=-1)
            err   = (pred - y_true) * angle_range
            self._sq.assign_add(tf.reduce_sum(tf.square(err)))
            self._cnt.assign_add(tf.cast(tf.size(err), tf.float32))

        def result(self):
            return tf.sqrt(self._sq / (self._cnt + 1e-9))

        def reset_state(self):
            self._sq.assign(0.); self._cnt.assign(0.)

    return (
        {"angle": HuberAngle(), "shank": ShankOLC()},
        {"angle": 1.0,          "shank": alpha_olc},
        {"angle": [MAEDeg(), RMSEDeg()]},
    )


# ── Training ──────────────────────────────────────────────────────────────────

def train(args, train_samples, val_samples):
    import tensorflow as tf
    from tensorflow import keras

    n_tr = len(train_samples)
    n_va = len(val_samples)
    print(f"\n  Train: {n_tr:,}  |  Val: {n_va:,}")

    # Compute median shank length for OLC anchor
    median_shank = float(np.median([s["shank_len"] for s in train_samples]))
    print(f"  Median shank (OLC anchor): {median_shank:.1f} px")

    train_ds = build_dataset(train_samples, args.batch_size, augment=True,
                             cache_crops=args.cache_crops)
    val_ds   = build_dataset(val_samples,   args.batch_size, augment=False,
                             cache_crops=args.cache_crops)

    loss_d, weights, metrics_d = build_loss_and_metrics(args.alpha_olc, median_shank)
    best_rmse = float("inf")
    history_all = {}

    # ── Phase 1: train head only ──────────────────────────────────────────
    print(f"\n[Phase 1]  Head only  lr={HEAD_LR}  epochs={args.head_epochs}")
    model = build_model(unfreeze_layers=0)
    model.compile(
        optimizer = keras.optimizers.Adam(HEAD_LR),
        loss      = loss_d,
        loss_weights = weights,
        metrics   = metrics_d,
    )
    h1 = model.fit(
        train_ds, epochs=args.head_epochs,
        validation_data=val_ds, verbose=1,
    )
    history_all["phase1"] = h1.history

    # ── Phase 2: partial unfreeze + cosine decay + early stopping ─────────
    restart_every = max(10, args.max_epochs // 3)
    lr_schedule   = keras.optimizers.schedules.CosineDecayRestarts(
        initial_learning_rate = FINETUNE_LR,
        first_decay_steps     = restart_every * (n_tr // args.batch_size + 1),
        t_mul                 = 1.0,
        m_mul                 = 0.9,
        alpha                 = FINETUNE_LR_MIN / FINETUNE_LR,
    )

    print(f"\n[Phase 2]  Unfreeze last {args.unfreeze} layers  "
          f"lr={FINETUNE_LR}→{FINETUNE_LR_MIN}  max_epochs={args.max_epochs}")

    model_p2 = build_model(unfreeze_layers=args.unfreeze)
    # Transfer Phase 1 weights to Phase 2 model
    model_p2.set_weights(model.get_weights())

    model_p2.compile(
        optimizer    = keras.optimizers.Adam(lr_schedule),
        loss         = loss_d,
        loss_weights = weights,
        metrics      = metrics_d,
    )

    callbacks = [
        keras.callbacks.EarlyStopping(
            monitor              = "val_angle_rmse_deg",
            patience             = args.patience,
            restore_best_weights = True,
            mode                 = "min",
            verbose              = 1,
        ),
        # No ReduceLROnPlateau here: the optimizer's learning rate is a
        # CosineDecayRestarts schedule object, not a plain float, and Keras
        # raises TypeError if a callback tries to imperatively overwrite a
        # schedule-driven LR. The schedule's own warm restarts already serve
        # the plateau-escape role ReduceLROnPlateau would otherwise play.
        keras.callbacks.ModelCheckpoint(
            filepath          = str(MODEL_DIR / "angle_regressor_ckpt.keras"),
            monitor           = "val_angle_rmse_deg",
            save_best_only    = True,
            mode              = "min",
            verbose           = 0,
        ),
    ]

    h2 = model_p2.fit(
        train_ds, epochs=args.max_epochs,
        validation_data=val_ds,
        callbacks=callbacks,
        verbose=1,
    )
    history_all["phase2"] = h2.history

    # Best val RMSE
    val_rmse_hist = h2.history.get("val_angle_rmse_deg", [999.0])
    best_rmse     = float(min(val_rmse_hist))
    best_epoch    = int(np.argmin(val_rmse_hist)) + 1
    print(f"\n  Best val RMSE: {best_rmse:.2f}°  at epoch {best_epoch}")
    if best_rmse < 3.0:
        print("  ✓ TARGET ACHIEVED: RMSE < 3°")
    else:
        print(f"  Gap to target: {best_rmse - 3.0:.2f}°")

    return model_p2, history_all, best_rmse, median_shank


# ── Export ────────────────────────────────────────────────────────────────────

def export_model(model, output_dir, tflite_path, skip_tflite, representative_samples=None):
    import tensorflow as tf

    output_dir.mkdir(parents=True, exist_ok=True)
    model.export(str(output_dir))
    print(f"\n  SavedModel: {output_dir}")

    if not skip_tflite:
        print("  Converting to INT8 TFLite ...")
        converter = tf.lite.TFLiteConverter.from_saved_model(str(output_dir))
        converter.optimizations    = [tf.lite.Optimize.DEFAULT]
        converter.inference_input_type  = tf.float32
        converter.inference_output_type = tf.float32

        # Full-integer (TFLITE_BUILTINS_INT8) quantization requires a
        # representative_dataset to calibrate activation ranges; without
        # samples, fall back to weight-only dynamic-range quantization
        # (still int8 weights, just no INT8 op-set requirement) rather than
        # crashing.
        if representative_samples:
            rep_ds = build_dataset(representative_samples[:100], batch_size=1,
                                   augment=False)

            def representative_dataset():
                for img, _ in rep_ds.take(100):
                    yield [img]

            converter.target_spec.supported_ops = [
                tf.lite.OpsSet.TFLITE_BUILTINS_INT8,
                tf.lite.OpsSet.TFLITE_BUILTINS,
            ]
            converter.representative_dataset = representative_dataset
        else:
            print("  WARNING: no representative samples given -- falling back to "
                 "dynamic-range (weight-only) quantization.")

        tflite_model = converter.convert()
        tflite_path.parent.mkdir(parents=True, exist_ok=True)
        tflite_path.write_bytes(tflite_model)
        print(f"  TFLite ({tflite_path.stat().st_size // 1024} kB): {tflite_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = parse_args()

    print("=" * 64)
    print("  Pendulastic Knee Angle Regressor  —  Training")
    print("=" * 64)

    if not COCO_JSON.exists():
        print(f"\nERROR: {COCO_JSON} not found.  Run gen_training_data.py first.")
        sys.exit(1)

    print("\nLoading dataset ...")
    samples = load_samples()
    if not samples:
        print("No valid samples found.")
        sys.exit(1)

    train_s = [s for s in samples if s["split"] == "train"]
    val_s   = [s for s in samples if s["split"] == "val"]

    print(f"  Total samples : {len(samples):,}")
    print(f"  Train         : {len(train_s):,}  "
          f"({len({s['participant'] for s in train_s})} participants)")
    print(f"  Val           : {len(val_s):,}  "
          f"({len({s['participant'] for s in val_s})} participants — held out)")
    print(f"  Angle range   : {min(s['angle_deg'] for s in samples):.1f}° – "
          f"{max(s['angle_deg'] for s in samples):.1f}°")

    if not val_s:
        print("\nWARNING: No validation participants found.  "
              "Check VAL_PARTICIPANTS names match your dataset.")

    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    model, history, best_rmse, median_shank = train(args, train_s, val_s)

    export_model(model, OUTPUT_MODEL, OUTPUT_TFLITE, skip_tflite=args.no_tflite,
                representative_samples=val_s)

    # Save metrics
    metrics = {
        "best_val_rmse_deg": best_rmse,
        "target_met":        best_rmse < 3.0,
        "n_train":           len(train_s),
        "n_val":             len(val_s),
        "median_shank_px":   median_shank,
        "batch_size":        args.batch_size,
        "head_epochs":       args.head_epochs,
        "max_epochs":        args.max_epochs,
        "patience":          args.patience,
        "unfreeze_layers":   args.unfreeze,
        "alpha_olc":         args.alpha_olc,
        "phase2_val_rmse_history": history["phase2"].get("val_angle_rmse_deg", []),
    }
    with open(METRICS_JSON, "w") as f:
        json.dump(metrics, f, indent=2)

    print("\n" + "=" * 64)
    print(f"  Best val RMSE : {best_rmse:.2f}°")
    print(f"  SavedModel    : {OUTPUT_MODEL}")
    print(f"  TFLite        : {OUTPUT_TFLITE}")
    print(f"  Metrics       : {METRICS_JSON}")
    print()
    print("  To use in the viewer:")
    print(f"    python pendulastic_viewer.py --angle-model {OUTPUT_MODEL}")
    print("=" * 64)


if __name__ == "__main__":
    main()
