"""
Pose-check tuning harness
=========================
Run pose_check over a folder of REAL photos and see what it classifies each as,
so you can tune the thresholds in pose_check.py against actual images rather
than guessing.

SETUP — make a folder like this (names are just for you to compare against):
    testphotos/
        front_me.jpg          front_friend.jpg
        side_me.jpg           side_friend.jpg
        selfie_bad.jpg        headshot_bad.jpg     cat.jpg

RUN
    pip install mediapipe opencv-python-headless numpy
    python tune_pose.py testphotos

It prints, per image: the view it detected, whether it's "ok", and the raw
numbers. The one that decides front vs side is `ratio` — shoulder width
divided by torso height. Facing the camera gives roughly 0.7-1.0; turned
sideways gives roughly 0.1-0.3. Adjust _SIDE_RATIO in pose_check.py so the
threshold falls in the empty gap between your own two clusters.

`old_sw` is the previous metric (shoulder width as a fraction of image width),
printed only so you can see why it misjudged wide or letterboxed photos.

WHAT GOOD LOOKS LIKE
    - every real FRONT full-body photo  -> view=front, ok=True
    - every real SIDE full-body photo   -> view=side,  ok=True
    - every selfie / headshot / no-body -> ok=False  (this is the main bug to catch)
If a valid photo is wrongly rejected, loosen the threshold; if a selfie slips
through, tighten it. The printed numbers tell you which threshold to move.
"""

import sys
import os
import pose_check as pc


def main(folder: str):
    exts = (".jpg", ".jpeg", ".png", ".webp")
    files = sorted(f for f in os.listdir(folder) if f.lower().endswith(exts))
    if not files:
        print(f"No images in {folder}")
        return
    print(f"{'file':<28} {'view':<8} {'ok':<5} {'ratio':<7} {'shldr_px':<9} "
          f"{'torso_px':<9} {'old_sw':<7} reason")
    print("-" * 115)
    for f in files:
        data = open(os.path.join(folder, f), "rb").read()
        r = pc.classify(data)
        d = r.detail
        ratio = d.get("ratio", "-")
        shp = d.get("shoulder_px", "-")
        tp = d.get("torso_px", "-")
        old = d.get("shoulder_width", "-")
        print(f"{f:<28} {r.view:<8} {str(r.ok):<5} {str(ratio):<7} {str(shp):<9} "
              f"{str(tp):<9} {str(old):<7} {r.reason}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python tune_pose.py <folder-of-photos>")
        raise SystemExit(1)
    main(sys.argv[1])
