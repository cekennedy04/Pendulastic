import warnings, os
warnings.filterwarnings("ignore", category=UserWarning, module="mmcv")
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

import numpy as np
from mmpose.apis import init_model, inference_topdown

config = r"C:\Users\cladi\mmpose\configs\body_2d_keypoint\topdown_heatmap\coco\td-hm_hrnet-w48_8xb32-210e_coco-256x192.py"
ckpt   = r"C:\Users\cladi\Pendulastic\models\PosePipeline\checkpoints\td-hm_hrnet-w48_8xb32-210e_coco-256x192-0e67c616_20220913.pth"

print("Loading model...", flush=True)
model = init_model(config, ckpt, device="cpu")
print("Model loaded. Running inference on dummy frame...", flush=True)

dummy = np.zeros((480, 640, 3), dtype=np.uint8)
bbox  = np.array([[0, 0, 640, 480]], dtype=np.float32)
results = inference_topdown(model, dummy, bboxes=bbox, bbox_format="xyxy")
kps = results[0].pred_instances.keypoints
scs = results[0].pred_instances.keypoint_scores
print(f"keypoints shape: {kps.shape}")
print(f"scores shape:    {scs.shape}")
print(f"L_KNEE score:    {scs[0,13]:.3f}  coords: {kps[0,13]}")
print("SMOKE TEST PASSED")
