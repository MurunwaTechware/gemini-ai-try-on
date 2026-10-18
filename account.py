"""The account page and the signed-in user's own data: their details and sign-in methods, their
measurement profiles and try-on results, and a download of everything the app keeps about them.

Every route works on the user from the session cookie only. Folders are looked up under that user's
own folder, so someone else's job or run ID gives 404, the same as one that doesn't exist.
"""
import json
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from firebase_admin import auth as fb_auth
from pydantic import BaseModel

import config
import jobs
import user_store
from auth import User, current_user, page_user, same_origin
from models.Entities.Users import User as UserRecord   # the stored record; auth.User is only who's signed in

STATIC = Path(__file__).parent / "static"
IMAGE_NAME = re.compile(r"[a-z]+\.(jpe?g|png|webp)")   # front.jpg, result.png; never the JSON or prompt
PHONE = re.compile(r"\+?[0-9 ()-]{7,20}")
MAX_NAME = 60

router = APIRouter()


@router.get("/account", include_in_schema=False, dependencies=[Depends(page_user)])
def account_page():
    return FileResponse(STATIC / "account.html")


# ── Details ───────────────────────────────────────────────────────────────────

def _record(user: User) -> UserRecord:
    """The user's stored record. A session from before records existed gets one now."""
    return user_store.get(user.uid) or user_store.record_sign_in(
        {"uid": user.uid, "email": user.email, "name": user.name, "picture": user.photo_url})


def _details(record: UserRecord) -> dict:
    return {"uid": record.uid, "email": record.email, "name": record.display_name,
            "first_name": record.first_name, "last_name": record.last_name, "phone": record.phone,
            "photo_url": record.photo_url}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return _details(_record(user))


class DetailsUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None


def _clean(field: str, value: str | None) -> str | None:
    value = " ".join((value or "").split()) or None   # trims and collapses spaces; empty clears the field
    if value is None:
        return None
    if field == "phone":
        if not PHONE.fullmatch(value) or sum(c.isdigit() for c in value) < 7:
            raise HTTPException(400, "Enter a phone number in digits, e.g. 082 123 4567 or +27 82 123 4567.")
    elif len(value) > MAX_NAME:
        raise HTTPException(400, f"Names can be up to {MAX_NAME} characters.")
    return value


@router.patch("/me", dependencies=[Depends(same_origin)])
def update_me(body: DetailsUpdate, user: User = Depends(current_user)):
    """Change the user's own name, surname or phone number. Only the fields sent are changed."""
    changes = {field: _clean(field, getattr(body, field)) for field in body.model_fields_set}
    if not changes:
        raise HTTPException(400, "Nothing to change.")
    _record(user)   # creates it for a session from before records existed
    return _details(user_store.update_details(user.uid, changes))


@router.get("/me/sign-in-methods")
def sign_in_methods(user: User = Depends(current_user)):
    """Which ways this account can sign in. Asks Firebase, since linking happens in the browser."""
    providers = {p.provider_id for p in fb_auth.get_user(user.uid).provider_data}
    return {"google": "google.com" in providers, "password": "password" in providers}


# ── Measurement profiles and try-ons ──────────────────────────────────────────

def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):   # missing, or still being written
        return None


def _created(item_id: str) -> str:
    """Job and run IDs start with when they were made: 20261006-101405-... -> 2026-10-06T10:14:05."""
    return datetime.strptime(item_id[:15], "%Y%m%d-%H%M%S").isoformat()


def _folders(user: User, kind: str) -> list[Path]:
    """The user's measurement profile or try-on folders, newest first."""
    base = jobs.user_dir(user.uid) / kind
    if not base.is_dir():
        return []
    return sorted((d for d in base.iterdir() if d.is_dir() and jobs.JOB_ID.fullmatch(d.name)), reverse=True)


def _file_url(kind: str, path: Path) -> str:
    return f"/me/files/{kind}/{path.parent.name}/{path.name}"   # served by own_file below


def _image(kind: str, folder: Path, stem: str) -> str | None:
    path = next(folder.glob(f"{stem}.*"), None)
    return _file_url(kind, path) if path else None


def _profile(folder: Path) -> dict:
    measurement = _read_json(folder / jobs.MEASUREMENT_FILE)
    error = _read_json(folder / jobs.ERROR_FILE)
    return {
        "job_id": folder.name,
        "created": _created(folder.name),
        # "measuring" also covers a job that was interrupted and never finished
        "status": "ready" if measurement else "failed" if error else "measuring",
        "error": (error or {}).get("error"),
        "inputs": _read_json(folder / jobs.INPUTS_FILE),
        "body_shape": (measurement or {}).get("body_shape"),
        "photo": _image(jobs.PROFILES_DIR, folder, "front"),
    }


def _saved_tryon(folder: Path) -> dict:
    garments = {slot: _image(jobs.TRYONS_DIR, folder, slot) for slot in config.GARMENT_SLOTS}
    return {
        "saved_id": folder.name,
        "saved": _created(folder.name),
        "measurement_profile": (_read_json(folder / jobs.TRYON_META_FILE) or {}).get("measurement_profile"),
        "result": _image(jobs.TRYONS_DIR, folder, "result"),
        "person": _image(jobs.TRYONS_DIR, folder, "person"),
        "garments": {slot: url for slot, url in garments.items() if url},
    }


@router.get("/me/measurement-profiles")
def measurement_profiles(user: User = Depends(current_user)):
    return [_profile(f) for f in _folders(user, jobs.PROFILES_DIR)]


@router.get("/me/saved-tryons")
def saved_tryons(user: User = Depends(current_user)):
    return [_saved_tryon(f) for f in _folders(user, jobs.TRYONS_DIR)]


@router.get("/me/files/{kind}/{item_id}/{name}")
def own_file(kind: str, item_id: str, name: str, user: User = Depends(current_user)):
    """A photo from the user's own profiles or try-ons. Each part is checked, so a crafted path can't
    climb out of the folder, and only images are served (not prompts or JSON)."""
    if kind not in (jobs.PROFILES_DIR, jobs.TRYONS_DIR) or not jobs.JOB_ID.fullmatch(item_id) \
            or not IMAGE_NAME.fullmatch(name):
        raise HTTPException(404, "Not found.")
    path = jobs.user_dir(user.uid) / kind / item_id / name
    if not path.is_file():
        raise HTTPException(404, "Not found.")
    # private: only this browser may cache it. Files never change once written.
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})


# ── Download ──────────────────────────────────────────────────────────────────

@router.get("/me/export")
def export(user: User = Depends(current_user)):
    """Everything kept about the user as one JSON file (POPIA lets people ask for this). Photos are
    listed by their address on this site rather than included."""
    profiles = [{**_profile(f), "measurement": _read_json(f / jobs.MEASUREMENT_FILE)}
                for f in _folders(user, jobs.PROFILES_DIR)]
    saved = []
    for f in _folders(user, jobs.TRYONS_DIR):
        prompt = f / "prompt.txt"
        saved.append({**_saved_tryon(f), "prompt": prompt.read_text(encoding="utf-8") if prompt.is_file() else None})
    data = {
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "account": _record(user).to_dict(),
        "sign_in_methods": sign_in_methods(user),
        "measurement_profiles": profiles,
        "saved_tryons": saved,
    }
    return JSONResponse(data, headers={"Content-Disposition": 'attachment; filename="mannequin-ai-my-information.json"'})
