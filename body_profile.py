"""The user's body profile: their twin's base body and measurements, from the body questionnaire or a
scan, plus anything they typed in on the twin page's editor. It's what the user's mesh is made from.

One Firestore document per user, body_profiles/<UID>, written through the Admin SDK (like user_store.py;
browsers never read Firestore directly). With sign-in off it's kept in memory instead, like the demo
user's record. Saving again (the questionnaire, a new scan, the editor) replaces the whole profile; the
app carries over what the user typed in themselves.

Every measurement says where it came from, so better sources can take over later without guessing:
    "base"           the chosen base body's own measurement (its mesh, scaled to the user's height)
    "questionnaire"  worked out from a clothing size; a range, since sizes differ between brands
    "scan"           estimated from the user's photos
    "manual"         typed in by the user, e.g. from a tape measure; trusted most
The frontend does the size conversions (lib/body-sizes.ts) and holds the base bodies' measurements
(lib/base-bodies.json), so it can show them as the user answers; this checks that what arrives is in a
believable range and stores it.
"""
import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from firebase_admin import firestore
from pydantic import BaseModel, Field, field_validator, model_validator

import config
import jobs
from auth import User, current_user, same_origin

log = logging.getLogger(__name__)

COLLECTION = "body_profiles"
VERSION = 1   # bump when the questionnaire or its size conversions change meaning

_in_memory: dict[str, dict] = {}   # sign-in off: profiles by UID, instead of Firestore

router = APIRouter()

# Believable ranges in cm, generous on purpose: this only stops typos and nonsense, not unusual bodies
LIMITS = {
    "height": (100, 230),
    "waist": (40, 200),
    "hip": (50, 220),
    "bust": (50, 200),
    "underbust": (45, 180),
    "inside_leg": (40, 120),
    # The rest of the mesh viewer's measurements (SMPL-Anthropometry's), from the base body unless entered
    "head": (40, 70),
    "neck": (20, 70),
    "shoulder_breadth": (20, 70),
    "arm_length": (30, 100),
    "bicep": (12, 70),
    "forearm": (12, 60),
    "wrist": (8, 35),
    "shoulder_to_crotch": (30, 100),
    "thigh": (25, 110),
    "calf": (15, 75),
    "ankle": (10, 45),
    # The scan's lengths (gemini_client.Measurements), also Anny's own length controls
    "upper_arm_length": (15, 50),
    "lower_arm_length": (15, 45),
    "upper_leg_length": (25, 70),
    "lower_leg_length": (25, 65),
    "nape_to_waist": (25, 65),
}
# UK/SA letters (with an inch band, e.g. 34DD) and EU letters (with a cm band, e.g. 75E) together
CUPS = ("AA", "A", "B", "C", "D", "DD", "E", "F", "FF", "G", "GG", "H", "HH", "I", "J")


class Measurement(BaseModel):
    value: float                       # cm; the best single number
    min: float | None = None           # cm; a range when it came from a clothing size
    max: float | None = None
    source: Literal["base", "questionnaire", "scan", "manual"]

    @model_validator(mode="after")
    def _range(self):
        if (self.min is None) != (self.max is None):
            raise ValueError("min and max go together")
        if self.min is not None and not self.min <= self.value <= self.max:
            raise ValueError("value must be within min..max")
        return self


class WaistAnswer(BaseModel):
    # jeans_inches: the waist number on jeans (W32). dress_size: UK/SA women's size 4-28 (6, 8, 10...)
    system: Literal["jeans_inches", "dress_size"]
    size: float = Field(ge=2, le=60)


class BraAnswer(BaseModel):
    band: int = Field(ge=26, le=120)   # 32 (inches, UK/SA) or 70 (cm, EU)
    band_system: Literal["inches", "cm"]
    cup: str

    @field_validator("cup")
    @classmethod
    def _cup(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in CUPS:
            raise ValueError(f"cup must be one of {', '.join(CUPS)}")
        return v


class BodyProfileIn(BaseModel):
    sex: Literal["female", "male"]                  # picks the base body; the questions don't depend on it
    body_type: Literal["slim", "average", "fuller"]
    weight_kg: float | None = Field(default=None, ge=25, le=300)
    waist: WaistAnswer | None = None                # None: skipped
    bra: BraAnswer | None = None                    # None: skipped or not relevant
    measurements: dict[str, Measurement]            # at least height; keys from LIMITS
    scan_job: str | None = None                     # the scan the measurements came from, if any

    @field_validator("scan_job")
    @classmethod
    def _scan_job(cls, v: str | None) -> str | None:
        if v is not None and not jobs.JOB_ID.fullmatch(v):
            raise ValueError("scan_job must be a scan's job ID")
        return v

    @field_validator("measurements")
    @classmethod
    def _measurements(cls, m: dict[str, Measurement]) -> dict[str, Measurement]:
        if "height" not in m:
            raise ValueError("height is required")
        for name, meas in m.items():
            if name not in LIMITS:
                raise ValueError(f"unknown measurement {name!r}")
            low, high = LIMITS[name]
            for v in (meas.value, meas.min, meas.max):
                if v is not None and not low <= v <= high:
                    raise ValueError(f"{name} must be between {low} and {high} cm")
        return m


def _ref(uid: str):
    return firestore.client().collection(COLLECTION).document(uid)


def get(uid: str) -> dict | None:
    if not config.AUTH_ENABLED:
        return _in_memory.get(uid)
    snap = _ref(uid).get()
    return snap.to_dict() if snap.exists else None


def save(uid: str, profile: BodyProfileIn) -> dict:
    record = {
        **profile.model_dump(),
        "version": VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if not config.AUTH_ENABLED:
        _in_memory[uid] = record
    else:
        _ref(uid).set(record)   # replaces the whole profile: redoing the questionnaire starts clean
    return record


@router.get("/me/body-profile")
def body_profile(user: User = Depends(current_user)):
    """The user's body profile, or {"profile": null} before they've done the questionnaire."""
    return {"profile": get(user.uid)}


@router.put("/me/body-profile", dependencies=[Depends(same_origin)])
def save_body_profile(body: BodyProfileIn, user: User = Depends(current_user)):
    """Save the user's body profile, replacing the previous one."""
    try:
        return {"profile": save(user.uid, body)}
    except Exception:   # Firestore unreachable or refusing: say so plainly rather than a bare 500
        log.exception("couldn't save the body profile")
        raise HTTPException(503, "Couldn't save your answers right now. Please try again shortly.")
