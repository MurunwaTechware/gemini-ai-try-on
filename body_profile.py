"""The user's body profile: the onboarding questionnaire's answers and the starting measurements worked
out from them. It's the base for the user's mesh, before any scan.

One Firestore document per user, body_profiles/<UID>, written through the Admin SDK (like user_store.py;
browsers never read Firestore directly). With sign-in off it's kept in memory instead, like the demo
user's record. Saving again (redoing the questionnaire) replaces the whole profile.

Every measurement says where it came from, so better sources can take over later without guessing:
    "questionnaire"  worked out from a clothing size; a range, since sizes differ between brands
    "scan"           estimated from the user's photos
    "manual"         typed in by the user, e.g. from a tape measure; trusted most
The frontend does the size conversions (lib/body-sizes.ts), so it can show them as the user answers;
this checks that what arrives is in a believable range and stores it.
"""
import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from firebase_admin import firestore
from pydantic import BaseModel, Field, field_validator, model_validator

import config
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
}
# UK/SA letters (with an inch band, e.g. 34DD) and EU letters (with a cm band, e.g. 75E) together
CUPS = ("AA", "A", "B", "C", "D", "DD", "E", "F", "FF", "G", "GG", "H", "HH", "I", "J")


class Measurement(BaseModel):
    value: float                       # cm; the best single number
    min: float | None = None           # cm; a range when it came from a clothing size
    max: float | None = None
    source: Literal["questionnaire", "scan", "manual"]

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
    """Save the questionnaire. Doing it again replaces the previous answers."""
    try:
        return {"profile": save(user.uid, body)}
    except Exception:   # Firestore unreachable or refusing: say so plainly rather than a bare 500
        log.exception("couldn't save the body profile")
        raise HTTPException(503, "Couldn't save your answers right now. Please try again shortly.")
