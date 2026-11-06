"""About you: the few quick answers from onboarding (sex, height, weight, favourite colour), so the body
questionnaire and the scan page can start from them instead of asking again.

One Firestore document per user, about_you/<UID>, written through the Admin SDK like body_profile.py; in
memory when sign-in is off. Every field is optional, and saving replaces the whole record. Age isn't here:
onboarding's age step has the date of birth, and the app works the age out from that.

The body profile (body_profile.py) keeps its own height, weight and sex, since those are what its
measurements were worked out at; this is only the starting point for setting it up.
"""
import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from firebase_admin import firestore
from pydantic import BaseModel, Field

import config
from auth import User, current_user, same_origin

log = logging.getLogger(__name__)

COLLECTION = "about_you"
# Kept in step with the app's colour swatches (lib/about-you.ts)
COLOURS = ("black", "white", "grey", "navy", "blue", "green", "olive", "yellow", "orange", "red",
           "pink", "purple", "brown", "beige")

_in_memory: dict[str, dict] = {}   # sign-in off: records by UID, instead of Firestore

router = APIRouter()


class AboutYouIn(BaseModel):
    sex: Literal["female", "male"] | None = None             # picks the base body
    height_cm: float | None = Field(default=None, ge=100, le=230)
    weight_kg: float | None = Field(default=None, ge=25, le=300)
    favourite_colour: Literal[COLOURS] | None = None          # type: ignore[valid-type]


def _ref(uid: str):
    return firestore.client().collection(COLLECTION).document(uid)


def get(uid: str) -> dict | None:
    if not config.AUTH_ENABLED:
        return _in_memory.get(uid)
    snap = _ref(uid).get()
    return snap.to_dict() if snap.exists else None


def save(uid: str, about: AboutYouIn) -> dict:
    record = {**about.model_dump(), "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if not config.AUTH_ENABLED:
        _in_memory[uid] = record
    else:
        _ref(uid).set(record)
    return record


@router.get("/me/about-you")
def about_you(user: User = Depends(current_user)):
    """The user's quick answers from onboarding, or {"about": null} before they've given any."""
    return {"about": get(user.uid)}


@router.put("/me/about-you", dependencies=[Depends(same_origin)])
def save_about_you(body: AboutYouIn, user: User = Depends(current_user)):
    try:
        return {"about": save(user.uid, body)}
    except Exception:   # Firestore unreachable or refusing: say so plainly rather than a bare 500
        log.exception("couldn't save about-you")
        raise HTTPException(503, "Couldn't save your answers right now. Please try again shortly.")
