"""Downscale/recompress paper_figures PNGs into a small-footprint copy for Google Slides upload
(base64 payload size, not local viewing, is the constraint) -- lossy, presentation-only copies."""
from PIL import Image
import os

SRC = "Model_Analysis_Outputs/paper_figures"
DST = "Model_Analysis_Outputs/paper_figures_compressed"
os.makedirs(DST, exist_ok=True)

TARGET_W = 480  # px

for fname in os.listdir(SRC):
    if not fname.endswith(".png"):
        continue
    src_path = os.path.join(SRC, fname)
    img = Image.open(src_path).convert("RGB")
    w, h = img.size
    if w > TARGET_W:
        new_h = int(h * TARGET_W / w)
        img = img.resize((TARGET_W, new_h), Image.LANCZOS)
    dst_path = os.path.join(DST, fname.replace(".png", ".jpg"))
    img.save(dst_path, "JPEG", quality=58, optimize=True)
    print(f"{fname}: {os.path.getsize(src_path)} -> {os.path.getsize(dst_path)}")
