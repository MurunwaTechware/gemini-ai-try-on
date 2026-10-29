"""The user's try-on photos: the person photos every try-on starts with unless they pick another.

Two kinds, as on the twin page: a full body photo (clothes, shoes, bags) and a close-up (hats, sunglasses,
earrings). Each user's own uploads live in their folder, never anywhere shared:

    <user ID>/tryon_photos/full_body-20261029-130501-a1b2c3.jpg
    <user ID>/tryon_photos/close_up-20261029-130622-d4e5f6.png

Every replacement gets a new name, because browsers keep these photos for a day (they're served as
never-changing files); reusing a name would show the old photo after a change. The previous file is
deleted once the new one is saved, so only the current photo of each kind is kept.

Without an upload, the full body photo falls back to the front photo of the user's latest finished scan,
which follows nearly the same rules (whole body, facing the camera, fitted clothes). The close-up has
no fallback here: the app shows its own sample photo for whatever is still missing.

As everywhere else, the user comes from the session cookie only, so these routes can only ever read or
change the signed-in user's own photos.
"""
import asyncio
import mimetypes
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse

import config
import jobs
from auth import User, current_user, same_origin
from storage import StoredPath

PHOTOS_DIR = "tryon_photos"   # inside each user's folder
KINDS = ("full_body", "close_up")
Kind = Literal["full_body", "close_up"]
STAMP = "%Y%m%d-%H%M%S"
# full_body-20261029-130501-a1b2c3.jpg: the kind, when it was added, a random part, the image type
FILE_NAME = re.compile(r"(full_body|close_up)-(\d{8}-\d{6})-[0-9a-f]{6}\.(jpe?g|png|webp)")
SCANS_TO_CHECK = 10   # for the fallback: how many recent scans to look through for a finished one

router = APIRouter()


def _folder(user: User) -> StoredPath:
    return jobs.user_dir(user.uid) / PHOTOS_DIR


def _added(name: str) -> str:
    return datetime.strptime(FILE_NAME.fullmatch(name).group(2), STAMP).isoformat()


def _uploads(user: User) -> dict[str, list[StoredPath]]:
    """The user's uploaded photos by kind, newest first. Normally one each; more only for a moment
    while a replacement is being saved."""
    found: dict[str, list[StoredPath]] = {kind: [] for kind in KINDS}
    folder = _folder(user)
    if folder.is_dir():
        for path in folder.iterdir():
            match = FILE_NAME.fullmatch(path.name)
            if match and path.is_file():
                found[match.group(1)].append(path)
    for paths in found.values():
        paths.sort(key=lambda p: p.name, reverse=True)   # names start with the time, so newest first
    return found


def _scan_photo(user: User) -> dict | None:
    """The front photo of the user's latest finished scan, for when they haven't uploaded a full body photo."""
    base = jobs.user_dir(user.uid) / jobs.PROFILES_DIR
    if not base.is_dir():
        return None
    scans = sorted((d for d in base.iterdir() if d.is_dir() and jobs.JOB_ID.fullmatch(d.name)), reverse=True)
    for scan in scans[:SCANS_TO_CHECK]:
        names = sorted(p.name for p in scan.iterdir() if p.is_file())
        front = next((n for n in names if n.startswith("front.")), None)
        if jobs.MEASUREMENT_FILE in names and front:
            return {"url": f"/me/files/{jobs.PROFILES_DIR}/{scan.name}/{front}",   # served by account.own_file
                    "added": datetime.strptime(scan.name[:15], STAMP).isoformat(),
                    "source": "scan"}
    return None


def current(user: User) -> dict[str, dict | None]:
    """What each try-on photo is right now: the user's upload, else (full body only) their latest scan's
    front photo, else None, for which the app shows its own sample photo. Blocks (bucket calls)."""
    uploads = _uploads(user)
    photos: dict[str, dict | None] = {}
    for kind in KINDS:
        newest = uploads[kind][0] if uploads[kind] else None
        photos[kind] = ({"url": f"/me/tryon-photos/{kind}/{newest.name}", "added": _added(newest.name),
                         "source": "uploaded"} if newest else None)
    if photos["full_body"] is None:
        photos["full_body"] = _scan_photo(user)
    return photos


def _save(user: User, kind: str, data: bytes, mime: str) -> None:
    name = f"{kind}-{datetime.now().strftime(STAMP)}-{secrets.token_hex(3)}{mimetypes.guess_extension(mime) or '.png'}"
    folder = _folder(user)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    # Only after the new one is saved, so there's never a moment without a photo
    for old in _uploads(user)[kind]:
        if old.name != name:
            old.unlink(missing_ok=True)


def _remove(user: User, kind: str) -> None:
    for path in _uploads(user)[kind]:
        path.unlink(missing_ok=True)


@router.get("/me/tryon-photos")
def tryon_photos(user: User = Depends(current_user)):
    return current(user)


@router.put("/me/tryon-photos/{kind}", dependencies=[Depends(same_origin)])
async def replace_tryon_photo(kind: Kind, user: User = Depends(current_user), photo: UploadFile = File(...)):
    """Upload a new full body or close-up photo for this user. Answers with all their try-on photos."""
    if photo.content_type not in config.ALLOWED_MIME:
        raise HTTPException(415, f"The photo must be JPEG, PNG or WebP (got {photo.content_type}).")
    data = await photo.read()
    if not data:
        raise HTTPException(400, "The photo is empty.")
    if len(data) > config.MAX_IMAGE_BYTES:
        raise HTTPException(413, f"The photo is over {config.MAX_IMAGE_BYTES // (1024 * 1024)} MB. Resize it and try again.")
    await asyncio.to_thread(_save, user, kind, data, photo.content_type)
    return await asyncio.to_thread(current, user)


@router.delete("/me/tryon-photos/{kind}", dependencies=[Depends(same_origin)])
def remove_tryon_photo(kind: Kind, user: User = Depends(current_user)):
    """Delete the user's own photo of this kind, so the fallback (scan photo or sample) is used again."""
    _remove(user, kind)
    return current(user)


@router.get("/me/tryon-photos/{kind}/{name}")
def tryon_photo_file(kind: Kind, name: str, user: User = Depends(current_user)):
    """One of the user's own try-on photos. The name is checked, so a crafted one can't leave the folder."""
    match = FILE_NAME.fullmatch(name)
    if not match or match.group(1) != kind:
        raise HTTPException(404, "Not found.")
    path = _folder(user) / name
    if not path.is_file():
        raise HTTPException(404, "Not found.")
    # A replacement gets a new name, so this one never changes: the browser can keep it
    headers = {"Cache-Control": "private, max-age=86400"}
    if isinstance(path, Path):
        return FileResponse(path, headers=headers)
    return Response(path.read_bytes(), media_type=mimetypes.guess_type(name)[0], headers=headers)
