"""Where uploads and results are kept: a local folder or a Cloud Storage bucket (config.STORAGE_BACKEND).

The rest of the app works with paths under storage.ROOT and doesn't care which. ROOT is a pathlib.Path for
the local folder, or a BucketPath for the bucket. BucketPath does just the pathlib operations this app uses
(/, name, exists, is_file, is_dir, iterdir, glob("stem.*"), read/write text and bytes, mkdir, unlink), so the
same code reads and writes either, and the layout is the same in both:

    <user ID>/measurement_profiles/<job ID>/front.jpg ...
    <user ID>/saved_tryons/<saved ID>/result.png ...
    <user ID>/tryon_photos/full_body-<time>-<random>.jpg ...

A bucket has no real folders: ".../<job ID>/front.jpg" is one object whose name contains slashes. A folder
"exists" while any object's name starts with it, and mkdir does nothing.

Bucket calls go over the network and block, so code on the event loop runs them with asyncio.to_thread.
"""
import mimetypes
from collections.abc import Iterator
from functools import total_ordering
from pathlib import Path

import config

_client = None
_bucket = None


def _get_bucket():
    """The results bucket, with a client made on first use (credentials from ADC, like the Gemini calls)."""
    global _client, _bucket
    if _bucket is None:
        from google.cloud import storage as gcs   # imported here so local-folder mode never loads it
        _client = gcs.Client(project=config.PROJECT_ID)
        _bucket = _client.bucket(config.RESULTS_BUCKET)
    return _bucket


def _content_type(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


@total_ordering
class BucketPath:
    """An object name (or "folder" prefix) in the results bucket, used like a pathlib.Path."""

    __slots__ = ("_key", "_kind")

    def __init__(self, key: str = "", kind: str | None = None):
        self._key = key.strip("/")
        self._kind = kind   # "file" or "dir" when a listing already said which; saves a lookup

    # ── Names ──
    def __truediv__(self, part) -> "BucketPath":
        part = str(part)
        if part in ("", ".", "..") or "/" in part:   # one level at a time, and never upwards
            raise ValueError(f"Invalid path part: {part!r}")
        return BucketPath(f"{self._key}/{part}" if self._key else part)

    @property
    def name(self) -> str:
        return self._key.rsplit("/", 1)[-1]

    @property
    def parent(self) -> "BucketPath":
        return BucketPath(self._key.rsplit("/", 1)[0] if "/" in self._key else "", "dir")

    def __str__(self) -> str:
        return f"gs://{config.RESULTS_BUCKET}/{self._key}"

    def __repr__(self) -> str:
        return f"BucketPath({self._key!r})"

    def __eq__(self, other) -> bool:
        return isinstance(other, BucketPath) and self._key == other._key

    def __lt__(self, other: "BucketPath") -> bool:
        return self._key < other._key

    def __hash__(self) -> int:
        return hash(self._key)

    # ── Looking ──
    def _list(self, prefix: str, **kwargs):
        bucket = _get_bucket()
        return bucket.client.list_blobs(bucket, prefix=prefix, **kwargs)

    def is_file(self) -> bool:
        if self._kind is not None:
            return self._kind == "file"
        return _get_bucket().blob(self._key).exists()

    def is_dir(self) -> bool:
        if self._kind is not None:
            return self._kind == "dir"
        return any(True for _ in self._list(f"{self._key}/", max_results=1))

    def exists(self) -> bool:
        return self.is_file() or self.is_dir()

    def iterdir(self) -> Iterator["BucketPath"]:
        """What's directly inside: sub-folders, then files."""
        listing = self._list(f"{self._key}/" if self._key else "", delimiter="/")
        files = [BucketPath(blob.name, "file") for blob in listing]   # reading these fills in .prefixes
        folders = [BucketPath(prefix, "dir") for prefix in listing.prefixes]
        return iter(folders + files)

    def glob(self, pattern: str) -> Iterator["BucketPath"]:
        """Only "<stem>.*", the one pattern the app uses (front.* finds front.jpg, front.png...)."""
        stem = pattern[:-2]
        if not pattern.endswith(".*") or not stem or any(c in stem for c in "*?[]/"):
            raise NotImplementedError(f"BucketPath.glob only supports '<name>.*', not {pattern!r}")
        for blob in self._list(f"{self._key}/{stem}.", delimiter="/"):
            yield BucketPath(blob.name, "file")

    # ── Reading and writing ──
    def read_bytes(self) -> bytes:
        from google.api_core.exceptions import NotFound
        try:
            return _get_bucket().blob(self._key).download_as_bytes()
        except NotFound:
            raise FileNotFoundError(f"No such object: {self}") from None   # callers expect what pathlib raises

    def read_text(self, encoding: str = "utf-8") -> str:
        return self.read_bytes().decode(encoding)

    def write_bytes(self, data: bytes) -> int:
        _get_bucket().blob(self._key).upload_from_string(data, content_type=_content_type(self.name))
        return len(data)

    def write_text(self, text: str, encoding: str = "utf-8") -> int:
        return self.write_bytes(text.encode(encoding))

    def mkdir(self, parents: bool = False, exist_ok: bool = False) -> None:
        """Nothing to do: a bucket "folder" exists once something is saved in it."""

    def unlink(self, missing_ok: bool = False) -> None:
        from google.api_core.exceptions import NotFound
        try:
            _get_bucket().blob(self._key).delete()
        except NotFound:
            if not missing_ok:
                raise FileNotFoundError(f"No such object: {self}") from None


StoredPath = Path | BucketPath

# Where everything goes: the local folder (default) or the root of the results bucket
ROOT: StoredPath = BucketPath() if config.STORAGE_BACKEND == "gcs" else config.RUNS_DIR
