import sys
from pathlib import Path

from PIL import Image, ImageOps

track_path = Path("app/assets/tracks/albert_park.png")
if not track_path.exists():
    print(f"File not found: {track_path}")
    sys.exit(1)

track_image = Image.open(track_path)
print(f"Original size: {track_image.size}")

# Auto-crop
gray = track_image.convert("L")
# Threshold: keep only pixels darker than 240
binary = gray.point(lambda p: 0 if p < 240 else 255)
inverted = ImageOps.invert(binary)
bbox = inverted.getbbox()
print(f"BBox: {bbox}")

if bbox:
    cropped = track_image.crop(bbox)
    print(f"Cropped size: {cropped.size}")
    cropped.save("debug_track_cropped.png")
else:
    print("No bbox found!")
