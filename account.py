"""The account page and the signed-in user's own data: their details and sign-in methods, their
measurement profiles and try-on results, and a download of everything the app keeps about them
(their try-on photos have their own routes, in tryon_photos.py).

Every route works on the user from the session cookie only. Folders are looked up under that user's
own folder, so someone else's job or run ID gives 404, the same as one that doesn't exist.
"""
import json
import mimetypes
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from firebase_admin import auth as fb_auth
from pydantic import BaseModel

import config
import jobs
import prompts
import tryon_photos
import user_store
from auth import User, current_user, page_user, same_origin
from models.Entities.Users import User as UserRecord   # the stored record; auth.User is only who's signed in
from storage import StoredPath

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
    if not config.AUTH_ENABLED:   # the demo user has no Firebase account, so nothing to link or change
        return {"google": False, "password": False}
    providers = {p.provider_id for p in fb_auth.get_user(user.uid).provider_data}
    return {"google": "google.com" in providers, "password": "password" in providers}


# ── Measurement profiles and try-ons ──────────────────────────────────────────

def _read_json(path: StoredPath) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):   # missing, or still being written
        return None


def _created(item_id: str) -> str:
    """Job and run IDs start with when they were made: 20261006-101405-... -> 2026-10-06T10:14:05."""
    return datetime.strptime(item_id[:15], "%Y%m%d-%H%M%S").isoformat()


def _folders(user: User, kind: str) -> list[StoredPath]:
    """The user's measurement profile or try-on folders, newest first."""
    base = jobs.user_dir(user.uid) / kind
    if not base.is_dir():
        return []
    return sorted((d for d in base.iterdir() if d.is_dir() and jobs.JOB_ID.fullmatch(d.name)), reverse=True)


def _file_url(kind: str, path: StoredPath) -> str:
    return f"/me/files/{kind}/{path.parent.name}/{path.name}"   # served by own_file below


def _files(folder: StoredPath) -> dict[str, StoredPath]:
    """The files in one profile or try-on folder, by name. One listing, rather than a lookup per file:
    with the bucket, each lookup is a network call."""
    return {p.name: p for p in folder.iterdir() if p.is_file()}


def _json(files: dict[str, StoredPath], name: str) -> dict | None:
    return _read_json(files[name]) if name in files else None


def _image(kind: str, files: dict[str, StoredPath], stem: str) -> str | None:
    """The address of <stem>.* (front.jpg, result.png...), if the folder has one."""
    name = next((n for n in sorted(files) if n.startswith(f"{stem}.")), None)
    return _file_url(kind, files[name]) if name else None


def _each(build, folders: list[StoredPath]) -> list[dict]:
    """build(folder) for every folder, a few at a time (bucket reads wait on the network), in order."""
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(build, folders))


def _profile(folder: StoredPath) -> dict:
    files = _files(folder)
    measurement = _json(files, jobs.MEASUREMENT_FILE)
    error = _json(files, jobs.ERROR_FILE)
    return {
        "job_id": folder.name,
        "created": _created(folder.name),
        # "measuring" also covers a job that was interrupted and never finished
        "status": "ready" if measurement else "failed" if error else "measuring",
        "error": (error or {}).get("error"),
        "inputs": _json(files, jobs.INPUTS_FILE),
        "body_shape": (measurement or {}).get("body_shape"),
        "photo": _image(jobs.PROFILES_DIR, files, "front"),
        # All three views, for pages that show every photo from the scan
        "photos": {view: _image(jobs.PROFILES_DIR, files, view) for view in prompts.MEASURE_VIEWS},
        "_measurement": measurement,   # for the download; removed from the API's answer
    }


def _saved_tryon(folder: StoredPath) -> dict:
    files = _files(folder)
    garments = {slot: _image(jobs.TRYONS_DIR, files, slot) for slot in config.GARMENT_SLOTS}
    prompt = files.get("prompt.txt")
    return {
        "saved_id": folder.name,
        "saved": _created(folder.name),
        "measurement_profile": (_json(files, jobs.TRYON_META_FILE) or {}).get("measurement_profile"),
        "result": _image(jobs.TRYONS_DIR, files, "result"),
        "person": _image(jobs.TRYONS_DIR, files, "person"),
        "garments": {slot: url for slot, url in garments.items() if url},
        "_prompt": prompt,   # for the download only, which reads it
    }


def _public(item: dict) -> dict:
    """Without the fields only the download includes (their names start with _)."""
    return {k: v for k, v in item.items() if not k.startswith("_")}


@router.get("/me/measurement-profiles")
def measurement_profiles(user: User = Depends(current_user)):
    return [_public(p) for p in _each(_profile, _folders(user, jobs.PROFILES_DIR))]


@router.get("/me/saved-tryons")
def saved_tryons(user: User = Depends(current_user)):
    return [_public(t) for t in _each(_saved_tryon, _folders(user, jobs.TRYONS_DIR))]


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
    headers = {"Cache-Control": "private, max-age=86400"}
    if isinstance(path, Path):
        return FileResponse(path, headers=headers)
    # From the bucket: read and passed on (photos are a few MB at most)
    return Response(path.read_bytes(), media_type=mimetypes.guess_type(name)[0], headers=headers)


# ── Download ──────────────────────────────────────────────────────────────────

@router.get("/me/export")
def export(user: User = Depends(current_user)):
    """Everything kept about the user as one JSON file (POPIA lets people ask for this). Photos are
    listed by their address on this site rather than included."""
    profiles = [{**_public(p), "measurement": p["_measurement"]}
                for p in _each(_profile, _folders(user, jobs.PROFILES_DIR))]
    saved = [{**_public(t), "prompt": t["_prompt"].read_text(encoding="utf-8") if t["_prompt"] else None}
             for t in _each(_saved_tryon, _folders(user, jobs.TRYONS_DIR))]
    data = {
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "account": _record(user).to_dict(),
        "sign_in_methods": sign_in_methods(user),
        "measurement_profiles": profiles,
        "saved_tryons": saved,
        "tryon_photos": tryon_photos.current(user),
    }
    return JSONResponse(data, headers={"Content-Disposition": 'attachment; filename="mannequin-ai-my-information.json"'})
