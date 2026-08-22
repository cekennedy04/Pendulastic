#!/usr/bin/env python3
"""Probe script: load MeTRAbs from TF-Hub, inspect coco_19 joint names,
and time inference on a few sample images. Throwaway -- not part of the
calibration pipeline."""
import time
import tensorflow as tf
import tensorflow_hub as tfhub

print("Loading model (first call downloads + caches, can take a while)...")
t0 = time.time()
model = tfhub.load('https://bit.ly/metrabs_s')  # smallest/fastest variant for a CPU spike
print(f"  loaded in {time.time()-t0:.1f}s")

names = model.per_skeleton_joint_names['coco_19'].numpy()
names = [n.decode() for n in names]
print("coco_19 joint names:", names)

image = tf.image.decode_jpeg(tf.io.read_file('training_data/images/Participant_10_T1_frame_000000.jpg'))
print("image shape:", image.shape)

t0 = time.time()
pred = model.detect_poses(image, skeleton='coco_19')
print(f"  first inference: {time.time()-t0:.2f}s")
print("boxes:", pred['boxes'].shape)
print("poses2d:", pred['poses2d'].shape)

t0 = time.time()
for _ in range(5):
    pred = model.detect_poses(image, skeleton='coco_19')
print(f"  avg of next 5: {(time.time()-t0)/5:.2f}s/image")
