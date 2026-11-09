"""The user's twin mesh: their base body reshaped to their measurements, saved with every body profile.

The frontend's twin editor reshapes the body live in the browser; this makes the same body here, so every
saved profile (from the editor, the body questions or a scan) has its own mesh, built from the profile
itself rather than trusting what a browser sent. It uses copies of the frontend's shape files, in
twin_shapes/ (made by the frontend's scripts/build_body_shapes.py, from Anny):
    <sex>_<build>.glb    the six base bodies, each with Anny's measurement sliders as morph targets
                         ("waist+" is the waist slider fully up, "waist-" fully down)
    body-shapes.json     how many cm each slider changes each measurement on each body, and a "version"
    base-bodies.json     each base body's own measurements
Anny's sliders are linear on each side of 0 and add up exactly, so the base body plus the blended moves is
the body Anny would make. fit_shape is the frontend's fitShape (lib/body-shapes.ts) line for line, so the
saved mesh is the one the editor showed; the version says whether both sides have the same files.

Saved in the user's folder, a new name each time (browsers keep these for a day), the previous one
deleted once the profile points at the new one:
    <user ID>/twin/twin-20261106-120501-a1b2c3.glb    the reshaped body (positions, normals, triangles)
    <user ID>/twin/twin-20261106-120501-a1b2c3.json   what it was made from: the base body, the slider
                                                      settings, and where each measurement landed
"""
import hashlib
import json
import logging
import math
import re
import secrets
import struct
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse

import jobs
from auth import User, current_user
from storage import StoredPath

log = logging.getLogger(__name__)

SHAPES_DIR = Path(__file__).parent / "twin_shapes"
BODIES = [f"{sex}_{shape}" for sex in ("female", "male") for shape in ("slim", "average", "fuller")]
TWIN_DIR = "twin"   # inside each user's folder
STAMP = "%Y%m%d-%H%M%S"
FILE_NAME = re.compile(r"twin-\d{8}-\d{6}-[0-9a-f]{6}\.(glb|json)")

# The solver's settings, the same as the frontend's (lib/body-shapes.ts): keep them in step
ROUNDS = 80
STAY_PUT = 0.02
HEIGHT_COUNTS = 4
MISS_LIMIT = 1.5   # cm: a bigger miss pulls no harder, so an unreachable number can't bend the rest out of shape
SOURCE_COUNTS = {"manual": 3, "scan": 1, "questionnaire": 0.7, "base": 0.3}

router = APIRouter()


# ── The shape files ───────────────────────────────────────────────────────────

def _read_glb(path: Path) -> tuple[dict, bytes]:
    data = path.read_bytes()
    json_len = struct.unpack("<I", data[12:16])[0]
    gltf = json.loads(data[20:20 + json_len])
    start = 20 + json_len
    bin_len = struct.unpack("<I", data[start:start + 4])[0]
    return gltf, data[start + 8:start + 8 + bin_len]


def _accessor(gltf: dict, binary: bytes, index: int, dtype: str, width: int) -> np.ndarray:
    acc = gltf["accessors"][index]
    view = gltf["bufferViews"][acc["bufferView"]]
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    count = acc["count"] * width
    return np.frombuffer(binary, dtype=dtype, count=count, offset=start).reshape(-1, width) if width > 1 \
        else np.frombuffer(binary, dtype=dtype, count=count, offset=start)


class BaseBody:
    """One base body: its vertices, triangles and the slider moves, in the GLB's frame (Y-up, metres)"""

    def __init__(self, path: Path):
        gltf, binary = _read_glb(path)
        prim = gltf["meshes"][0]["primitives"][0]
        self.positions = _accessor(gltf, binary, prim["attributes"]["POSITION"], "<f4", 3).astype(np.float64)
        self.indices = _accessor(gltf, binary, prim["indices"], "<u4", 1).copy()
        self.moves: dict[str, np.ndarray] = {}
        names = gltf["meshes"][0]["extras"]["targetNames"]
        for name, target in zip(names, prim["targets"]):
            sparse = gltf["accessors"][target["POSITION"]]["sparse"]
            move = np.zeros_like(self.positions)
            if sparse["count"]:
                iv = gltf["bufferViews"][sparse["indices"]["bufferView"]]
                vv = gltf["bufferViews"][sparse["values"]["bufferView"]]
                idx = np.frombuffer(binary, "<u4", sparse["count"], iv.get("byteOffset", 0))
                val = np.frombuffer(binary, "<i2", sparse["count"] * 3, vv.get("byteOffset", 0)).reshape(-1, 3)
                move[idx] = val / 32767.0   # 16-bit, for -1..1 m (KHR_mesh_quantization)
            self.moves[name] = move


@lru_cache(maxsize=1)
def shapes() -> dict:
    """The shape files, read once. Their version is worked out again from the files, the same way the
    frontend's build script does: a half-copied or edited set is logged rather than quietly used."""
    data = json.loads((SHAPES_DIR / "body-shapes.json").read_text(encoding="utf-8"))
    data["base"] = json.loads((SHAPES_DIR / "base-bodies.json").read_text(encoding="utf-8"))
    h = hashlib.sha256()
    for name in BODIES:
        h.update((SHAPES_DIR / f"{name}.glb").read_bytes())
    h.update(json.dumps(data["effects"], sort_keys=True).encode())
    h.update(json.dumps(data["base"], sort_keys=True).encode())
    if h.hexdigest()[:12] != data.get("version"):
        log.error("twin_shapes/ doesn't match its body-shapes.json version (%s, files give %s): copy all of "
                  "the frontend's shape files again", data.get("version"), h.hexdigest()[:12])
    return data


@lru_cache(maxsize=6)
def base_body(name: str) -> BaseBody:
    if name not in shapes()["base"]:
        raise ValueError(f"No base body {name!r}")
    return BaseBody(SHAPES_DIR / f"{name}.glb")


def version() -> str:
    return shapes()["version"]


# ── Fitting: the frontend's fitShape ──────────────────────────────────────────

def fit_shape(sex: str, body_type: str, targets: dict[str, float], counts: dict[str, float]) -> dict:
    """The slider settings that bring these measurements (cm, at the user's height) as close as they go,
    and where each one lands. The same steps as lib/body-shapes.ts, so the same answer."""
    base = shapes()["base"][f"{sex}_{body_type}"]
    effects = shapes()["effects"][f"{sex}_{body_type}"]
    sliders = shapes()["sliders"]
    height = targets.get("height", base["height"])
    scale = height / base["height"]
    keys = [k for k in targets if base.get(k) is not None and targets[k] is not None]
    goal = {k: base["height"] if k == "height" else targets[k] / scale for k in keys}
    importance = {k: HEIGHT_COUNTS if k == "height" else counts.get(k, 1) for k in keys}
    w = {s: 0.0 for s in sliders}

    def effect(s: str, amount: float) -> dict:
        return effects.get(f"{s}{'+' if amount >= 0 else '-'}", {})

    def predict(k: str) -> float:
        v = base[k]
        for s in sliders:
            if w[s] != 0:
                v += effect(s, w[s]).get(k, 0) * abs(w[s])
        return v

    for _ in range(ROUNDS):
        moved = 0.0
        for s in sliders:
            before = w[s]
            now = {k: predict(k) for k in keys}
            residual = {k: now[k] - effect(s, before).get(k, 0) * abs(before) - goal[k] for k in keys}
            pull = {k: importance[k] * (1 if abs(now[k] - goal[k]) <= MISS_LIMIT else MISS_LIMIT / abs(now[k] - goal[k]))
                    for k in keys}
            best_amount, best_cost = 0.0, math.inf
            for side in (1, -1):
                e = effects.get(f"{s}{'+' if side > 0 else '-'}", {})
                num = sum(pull[k] * residual[k] * e.get(k, 0) for k in keys)
                den = STAY_PUT + sum(pull[k] * e.get(k, 0) ** 2 for k in keys)
                a = min(1.0, max(0.0, -num / den))
                cost = STAY_PUT * a * a + sum(pull[k] * (residual[k] + a * e.get(k, 0)) ** 2 for k in keys)
                if cost < best_cost:
                    best_amount, best_cost = side * a, cost
            w[s] = best_amount
            moved = max(moved, abs(w[s] - before))
        if moved < 1e-4:
            break

    weights = {}
    for s in sliders:
        if w[s] > 0:
            weights[f"{s}+"] = w[s]
        elif w[s] < 0:
            weights[f"{s}-"] = -w[s]
    # JavaScript's Math.round (halves up), not Python's round (halves to even)
    predicted = {k: math.floor(predict(k) * scale * 2 + 0.5) / 2 for k in keys}
    return {"weights": weights, "predicted": predicted}


def targets_of(profile: dict) -> tuple[dict[str, float], dict[str, float]]:
    """A profile's measurements as fitting targets, and how much each counts (by where it came from)"""
    targets, counts = {}, {}
    for k, m in profile["measurements"].items():
        targets[k] = m["value"]
        counts[k] = SOURCE_COUNTS[m["source"]]
    return targets, counts


# ── Building the mesh ─────────────────────────────────────────────────────────

def _normals(positions: np.ndarray, indices: np.ndarray) -> np.ndarray:
    """Vertex normals, each the sum of its triangles' (area-weighted) normals, like three.js's"""
    tri = indices.reshape(-1, 3)
    a, b, c = positions[tri[:, 0]], positions[tri[:, 1]], positions[tri[:, 2]]
    face = np.cross(b - a, c - a)
    normals = np.zeros_like(positions)
    for i in range(3):
        np.add.at(normals, tri[:, i], face)
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    return normals / np.where(length == 0, 1, length)


def build_glb(name: str, weights: dict[str, float]) -> bytes:
    """The base body with the slider moves blended in, as a plain GLB (no morph targets)"""
    body = base_body(name)
    positions = body.positions.copy()
    for target, amount in weights.items():
        positions += amount * body.moves[target]
    normals = _normals(positions, body.indices)

    pos = positions.astype("<f4").tobytes()
    nor = normals.astype("<f4").tobytes()
    idx = body.indices.astype("<u4").tobytes()
    binary = idx + pos + nor
    gltf = {
        "asset": {"version": "2.0", "generator": "gemini-ai-try-on twin_mesh.py"},
        "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "name": "twin"}],
        "meshes": [{"name": "twin", "primitives": [{"attributes": {"POSITION": 1, "NORMAL": 2}, "indices": 0, "mode": 4}]}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(idx), "target": 34963},
            {"buffer": 0, "byteOffset": len(idx), "byteLength": len(pos), "target": 34962},
            {"buffer": 0, "byteOffset": len(idx) + len(pos), "byteLength": len(nor), "target": 34962},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5125, "count": int(body.indices.size), "type": "SCALAR"},
            {"bufferView": 1, "componentType": 5126, "count": len(positions), "type": "VEC3",
             "min": positions.min(axis=0).astype("<f4").tolist(), "max": positions.max(axis=0).astype("<f4").tolist()},
            {"bufferView": 2, "componentType": 5126, "count": len(normals), "type": "VEC3"},
        ],
    }
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    binary += b"\0" * (-len(binary) % 4)
    total = 12 + 8 + len(js) + 8 + len(binary)
    return (struct.pack("<III", 0x46546C67, 2, total)
            + struct.pack("<II", len(js), 0x4E4F534A) + js
            + struct.pack("<II", len(binary), 0x004E4942) + binary)


def _folder(uid: str) -> StoredPath:
    return jobs.user_dir(uid) / TWIN_DIR


def make(uid: str, profile: dict, app_version: str | None) -> dict:
    """Builds and saves the mesh for this profile. Returns what the profile keeps about it. Blocks (bucket)."""
    name = f"{profile['sex']}_{profile['body_type']}"
    targets, counts = targets_of(profile)
    fit = fit_shape(profile["sex"], profile["body_type"], targets, counts)
    stem = f"twin-{datetime.now().strftime(STAMP)}-{secrets.token_hex(3)}"
    made = {
        "file": f"{stem}.glb",
        "base_body": name,
        "weights": {k: round(v, 4) for k, v in fit["weights"].items()},
        "predicted": fit["predicted"],
        "shapes_version": version(),
        # The app's own shape files, if it said: when they differ, the editor showed a slightly
        # different body from this one (one side has newer files)
        "app_shapes_version": app_version,
        "made_at": datetime.now().isoformat(timespec="seconds"),
    }
    folder = _folder(uid)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{stem}.glb").write_bytes(build_glb(name, fit["weights"]))
    (folder / f"{stem}.json").write_text(json.dumps({**made, "measurements": profile["measurements"]}, indent=2),
                                         encoding="utf-8")
    return made


def remove_others(uid: str, keep: str | None) -> None:
    """Deletes the user's older twin meshes, once the profile points at the new one. Blocks (bucket)."""
    folder = _folder(uid)
    if not folder.is_dir():
        return
    keep_stem = keep.rsplit(".", 1)[0] if keep else None
    for path in folder.iterdir():
        if FILE_NAME.fullmatch(path.name) and path.name.rsplit(".", 1)[0] != keep_stem:
            path.unlink(missing_ok=True)


@router.get("/me/twin/{name}")
def twin_file(name: str, user: User = Depends(current_user)):
    """One of the user's own twin files. The name is checked, so a crafted one can't leave the folder."""
    if not FILE_NAME.fullmatch(name):
        raise HTTPException(404, "Not found.")
    path = _folder(user.uid) / name
    if not path.is_file():
        raise HTTPException(404, "Not found.")
    # A new mesh gets a new name, so this one never changes: the browser can keep it
    headers = {"Cache-Control": "private, max-age=86400"}
    media = "model/gltf-binary" if name.endswith(".glb") else "application/json"
    if isinstance(path, Path):
        return FileResponse(path, media_type=media, headers=headers)
    return Response(path.read_bytes(), media_type=media, headers=headers)
