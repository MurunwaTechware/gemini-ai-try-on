"""
Pose validation for the body-scan photos
=========================================
Catches the common upload mistakes — a selfie / not-a-full-body photo, or a
front/side view in the wrong slot — BEFORE a measurement job starts, so the
user gets told "This is not a front view" instead of silently getting bad
measurements from three selfies.

Uses MediaPipe Pose (local, free, no external API call) to find body landmarks,
then classifies the photo as a full-body front view, a side view, or "not
usable". It is deliberately CONSERVATIVE — it only rejects photos it is
confident are wrong (the clear failures users actually hit), and it does not
try to tell left from right side (unreliable from one photo), so both side
slots accept "a side view". If anything goes wrong (model missing, library not
installed), it FAILS OPEN — it lets the upload through rather than block a real
user. The measurement prompt is the backstop.

SETUP
    pip install mediapipe opencv-python-headless numpy
    # One model file (~5 MB). It auto-downloads on first use to the path in
    # POSE_MODEL_PATH, or set POSE_MODEL_PATH to a file you've placed yourself:
    #   https://storage.googleapis.com/mediapipe-models/pose_landmarker/
    #     pose_landmarker_lite/float16/latest/pose_landmarker_lite.task

USAGE (in the upload handler, before creating the job):
    from pose_check import check_pose, PoseError
    try:
        check_pose(front_bytes, expected="front")
        check_pose(left_bytes,  expected="side")
        check_pose(right_bytes, expected="side")
    except PoseError as e:
        raise HTTPException(422, str(e))

TUNING
    The thresholds marked "TUNE" below were chosen by reasoning, not measured
    against your users' photos. Run `tune_pose.py` over a folder of real
    front/side/selfie photos and adjust them so valid photos pass and the bad
    ones you've seen get rejected. Start loose (reject only obvious selfies),
    tighten later.
"""

from __future__ import annotations

import os
import threading
import urllib.request
from dataclasses import dataclass

import numpy as np

try:
    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision
    _AVAILABLE = True
    _IMPORT_ERROR = None
except Exception as _e:   # noqa: BLE001
    _AVAILABLE = False
    _IMPORT_ERROR = f"{type(_e).__name__}: {_e}"
    # Say so LOUDLY at import time. This module fails open, so a broken install
    # means every photo sails through unchecked — correct behaviour, but it must
    # never be invisible. Without this line the only symptom is that the pose
    # check quietly stops doing anything.
    import logging as _logging
    _logging.getLogger(__name__).error(
        "pose_check DISABLED — could not import MediaPipe/OpenCV (%s). "
        "Uploads will NOT be pose-checked.", _IMPORT_ERROR
    )


class PoseError(Exception):
    """Raised when a photo doesn't match the expected pose. Message is user-facing."""


# --- model file ---------------------------------------------------------------
_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
              "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task")
POSE_MODEL_PATH = os.environ.get(
    "POSE_MODEL_PATH",
    os.path.join(os.path.dirname(__file__), "pose_landmarker_lite.task"),
)


def _ensure_model() -> str:
    """Return the model path, downloading it once if it isn't there yet."""
    if not os.path.exists(POSE_MODEL_PATH):
        urllib.request.urlretrieve(_MODEL_URL, POSE_MODEL_PATH)
    return POSE_MODEL_PATH


_landmarker = None

# The server handles uploads concurrently and runs this on worker threads, but a
# PoseLandmarker holds internal state and is not safe to call from two threads at
# once. One lock guards both building it and detecting with it. Detection is
# ~100-300ms, so this serialises pose checks under load — fine at this scale, and
# the alternative (one landmarker per thread) costs memory per thread.
_lock = threading.Lock()


def _get_landmarker():
    """One reusable PoseLandmarker (loading the model each call is slow).

    Call only while holding _lock.
    """
    global _landmarker
    if _landmarker is None:
        opts = mp_vision.PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=_ensure_model()),
            running_mode=mp_vision.RunningMode.IMAGE,
            num_poses=1,
        )
        _landmarker = mp_vision.PoseLandmarker.create_from_options(opts)
    return _landmarker


# MediaPipe Pose landmark indices (same in both APIs)
_L = {
    "nose": 0,
    "l_shoulder": 11, "r_shoulder": 12,
    "l_hip": 23, "r_hip": 24,
    "l_knee": 25, "r_knee": 26,
    "l_ankle": 27, "r_ankle": 28,
}

# --- thresholds (TUNE against real photos) ------------------------------------
_VIS = 0.5           # TUNE: a landmark counts as "visible" above this confidence

# Front vs side is decided by SHOULDER WIDTH ÷ TORSO HEIGHT, measured in pixels.
#
# Why not shoulder width as a fraction of the image (the old way)? Because that
# number shrinks whenever the photo is wide, letterboxed, or the person stands
# far back — none of which say anything about which way they are facing. A
# front-facing person in a wide landscape shot scored the same as a true side
# view. Torso height travels with the body, so the ratio is immune to aspect
# ratio, padding and camera distance.
#
# Roughly: facing the camera, shoulders are about as wide as the torso is tall
# (ratio ~0.7-1.0). Turned sideways, the shoulders collapse towards each other
# while the torso stays the same height (ratio ~0.1-0.3).
# Three outcomes, not two. A half-turned person is genuinely neither front nor
# side, and forcing a verdict on them means the answer flips with compression.
# Anything landing between the two thresholds is reported as "unclear" and
# accepted in EITHER slot — better than wrongly rejecting a real photo.
# Measured clusters: true side 0.00-0.06 | 45-degree 0.26-0.47 | true front 0.56-0.72
_SIDE_MAX = 0.25     # TUNE: ratio below this => confidently a side view
_FRONT_MIN = 0.50    # TUNE: ratio above this => confidently a front view
_MIN_TORSO_PX = 20   # below this the measurement is noise; don't judge on it
# ------------------------------------------------------------------------------


@dataclass
class PoseResult:
    ok: bool
    view: str            # "front" | "side" | "unknown"
    reason: str
    detail: dict


def _landmarks(image_bytes: bytes):
    """Detect one person. Returns (landmarks, image_width, image_height).

    The image size comes back too because MediaPipe gives x as a fraction of
    width and y as a fraction of height — so comparing a horizontal distance
    with a vertical one is meaningless until both are back in pixels.
    """
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise PoseError("That image couldn't be read. Please upload a JPEG, PNG or WebP photo.")
    height, width = img.shape[:2]
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    with _lock:
        result = _get_landmarker().detect(mp_image)
    if not result.pose_landmarks:
        return None, width, height
    return result.pose_landmarks[0], width, height   # first (only) person


def _vis(lms, name: str) -> float:
    return lms[_L[name]].visibility


def classify(image_bytes: bytes) -> PoseResult:
    """Classify one photo as a full-body front view, a side view, or unknown."""
    if not _AVAILABLE:
        return PoseResult(True, "unknown", f"pose check unavailable ({_IMPORT_ERROR})",
                          {"available": False, "import_error": _IMPORT_ERROR})

    try:
        lms, iw, ih = _landmarks(image_bytes)
    except PoseError:
        raise
    except Exception as e:   # noqa: BLE001 — never let a detection glitch block uploads
        return PoseResult(True, "unknown", f"pose check skipped ({e})", {"error": str(e)})

    if lms is None:
        return PoseResult(False, "unknown", "no person was detected in the photo", {"person": False})

    v = {k: _vis(lms, k) for k in _L}

    # 1) Full-body check: hips AND legs must be visible. A selfie fails here.
    hips_ok = v["l_hip"] > _VIS or v["r_hip"] > _VIS
    legs_ok = v["l_knee"] > _VIS or v["r_knee"] > _VIS or v["l_ankle"] > _VIS or v["r_ankle"] > _VIS
    if not (hips_ok and legs_ok):
        return PoseResult(False, "unknown",
                          "it looks like a close-up or selfie, not a full-body photo",
                          {"hips_ok": hips_ok, "legs_ok": legs_ok, "vis": v})

    # 2) Front vs side, measured against the body rather than against the frame.
    def px(name):
        lm = lms[_L[name]]
        return lm.x * iw, lm.y * ih          # back into pixels

    lsx, lsy = px("l_shoulder")
    rsx, rsy = px("r_shoulder")
    _, lhy = px("l_hip")
    _, rhy = px("r_hip")

    shoulder_px = abs(lsx - rsx)                         # collapses when turned sideways
    torso_px = abs((lhy + rhy) / 2 - (lsy + rsy) / 2)    # stays put when turned sideways
    ratio = shoulder_px / torso_px if torso_px >= _MIN_TORSO_PX else None

    detail = {
        "shoulder_px": round(shoulder_px, 1),
        "torso_px": round(torso_px, 1),
        "ratio": None if ratio is None else round(ratio, 3),
        "img": f"{iw}x{ih}",
        # kept for comparison with earlier tuning runs
        "shoulder_width": round(abs(lms[_L["l_shoulder"]].x - lms[_L["r_shoulder"]].x), 3),
        "shoulder_vis_gap": round(abs(v["l_shoulder"] - v["r_shoulder"]), 3),
        "vis": {k: round(x, 2) for k, x in v.items()},
    }

    # Torso too small to measure reliably. Claiming a view here would reject a
    # real photo over a measurement failure, so say "unclear" — which passes in
    # either slot — rather than guessing.
    if ratio is None:
        return PoseResult(True, "unclear", "full-body photo, orientation not measurable", detail)

    if ratio < _SIDE_MAX:
        return PoseResult(True, "side", "side-profile photo", detail)
    if ratio > _FRONT_MIN:
        return PoseResult(True, "front", "front-facing full-body photo", detail)
    return PoseResult(True, "unclear", "full-body photo, part-turned so the view is unclear", detail)


def check_pose(image_bytes: bytes, expected: str) -> PoseResult:
    """Raise PoseError if the photo clearly doesn't match `expected` ('front' or 'side')."""
    if expected not in ("front", "side"):
        raise ValueError("expected must be 'front' or 'side'")

    r = classify(image_bytes)

    if r.view == "unknown" and ("unavailable" in r.reason or "skipped" in r.reason):
        return r   # couldn't check — fail open

    if not r.ok:
        facing = "facing the camera" if expected == "front" else "turned to the side"
        raise PoseError(
            f"This doesn't look like a {expected}-view full-body photo — {r.reason}. "
            f"Please upload a photo showing your whole body, {facing}."
        )
    if r.view == "unclear":
        return r   # full body, but part-turned or unmeasurable — allow either slot

    if expected == "front" and r.view == "side":
        raise PoseError("This looks like a side photo, not a front view. "
                        "Please upload a photo facing the camera, showing your whole body.")
    if expected == "side" and r.view == "front":
        raise PoseError("This looks like a front photo, not a side view. "
                        "Please turn to the side and upload a full-body side photo.")
    return r


def pose_warning(image_bytes: bytes) -> str | None:
    """A SOFT, non-blocking note about an otherwise-acceptable photo, or None.

    Right now it only flags feet being cropped off: the knees are in frame (so the
    hard full-body check passed) but neither ankle is visible. We don't block on
    this — feet-cropped is a very common framing and the measurement is still
    usable (height is user-entered, not measured from the photo) — but it's worth
    telling the user so they can retake if they want a better fit.

    Reads an existing classify() result, so it adds no extra detection pass. Never
    raises and returns None whenever the check can't run (model missing, no person)."""
    try:
        r = classify(image_bytes)
    except Exception:   # noqa: BLE001 — a soft check must never break an upload
        return None
    if not r.ok or not _AVAILABLE:
        return None
    vis = r.detail.get("vis")
    if not isinstance(vis, dict):
        return None
    knees_seen = vis.get("l_knee", 0) > _VIS or vis.get("r_knee", 0) > _VIS
    ankles_seen = vis.get("l_ankle", 0) > _VIS or vis.get("r_ankle", 0) > _VIS
    if knees_seen and not ankles_seen:
        return "your feet were cut off"
    return None


# Manual test:  python pose_check.py <image.jpg> [front|side]
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python pose_check.py <image> [front|side]")
        raise SystemExit(1)
    data = open(sys.argv[1], "rb").read()
    res = classify(data)
    print(f"view={res.view}  ok={res.ok}  reason='{res.reason}'")
    print("detail:", res.detail)
    if len(sys.argv) > 2:
        try:
            check_pose(data, sys.argv[2]); print(f"ACCEPTED as {sys.argv[2]}")
        except PoseError as e:
            print(f"REJECTED ({sys.argv[2]}): {e}")
