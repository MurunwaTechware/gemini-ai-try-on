"""User entity: what this app keeps about a user, beyond what Firebase already holds.

Firebase Authentication owns sign-in: the UID, the linked providers (Google, email/password),
password hashes, email verification. This entity only holds app data keyed by that UID, so it
never stores a credential or a provider's own user ID.

Persistence-agnostic for now: to_dict/from_dict give a plain dict for Firestore, a JSON file,
or the row of whatever database is picked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

def _now() -> datetime:
    return datetime.now(timezone.utc)   # not datetime.UTC: that needs Python 3.11+


# What users can change themselves on /account. Email and picture come from the sign-in instead.
EDITABLE_FIELDS = ("first_name", "last_name", "phone")


def split_name(name: str | None) -> tuple[str | None, str | None]:
    """'Murunwa Maphiri' -> ('Murunwa', 'Maphiri'). Sign-in only gives one full name, so this is a guess the
    user can correct on /account. First word as the name keeps surnames like 'van der Merwe' whole."""
    first, _, rest = (name or "").strip().partition(" ")
    return first or None, rest.strip() or None


@dataclass(slots=True)
class User:
    uid: str                         # Firebase UID: the primary key, and the top folder in results/
    email: str | None = None         # copied from Firebase at sign-in; Firebase stays the source of truth
    # From the sign-in's name to start with (Apple only shares it on first sign-in); the user edits them on /account
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None         # entered by the user on /account
    # Google's profile picture (a URL on googleusercontent.com). Apple and email/password give none.
    photo_url: str | None = None
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)
    # Consent to storing their photos and measurements. None until accepted; the version records
    # which wording they agreed to, so a changed policy can ask again.
    terms_accepted_at: datetime | None = None
    terms_version: str | None = None

    @property
    def display_name(self) -> str | None:
        """Full name, for showing the user to themselves or others (e.g. in chat)."""
        return " ".join(n for n in (self.first_name, self.last_name) if n) or None

    @classmethod
    def from_firebase_claims(cls, claims: dict[str, Any]) -> User:
        """A new user from verified Firebase token or session-cookie claims (first sign-in)."""
        first, last = split_name(claims.get("name"))
        return cls(uid=claims["uid"], email=claims.get("email"), first_name=first, last_name=last,
                   photo_url=claims.get("picture"))

    def update_from_claims(self, claims: dict[str, Any]) -> bool:
        """Bring the record up to date at a later sign-in. Returns whether anything changed.
        Email and picture follow the Firebase account. The name is only filled in when missing: Apple
        sends it on the first sign-in only, and a name the user has set shouldn't be overwritten."""
        changed = False
        for attr, value in (("email", claims.get("email")), ("photo_url", claims.get("picture"))):
            if value and value != getattr(self, attr):
                setattr(self, attr, value)
                changed = True
        if not self.first_name and not self.last_name and claims.get("name"):
            self.first_name, self.last_name = split_name(claims["name"])
            changed = True
        if changed:
            self.updated_at = _now()
        return changed

    def update_details(self, changes: dict[str, str | None]) -> None:
        """The user's own edits from /account (already checked by the caller). None clears a field."""
        unknown = set(changes) - set(EDITABLE_FIELDS)
        if unknown:
            raise ValueError(f"Not editable: {', '.join(sorted(unknown))}")
        for attr, value in changes.items():
            setattr(self, attr, value)
        self.updated_at = _now()

    def has_accepted_terms(self, version: str) -> bool:
        return self.terms_accepted_at is not None and self.terms_version == version

    def accept_terms(self, version: str) -> None:
        self.terms_accepted_at = self.updated_at = _now()
        self.terms_version = version

    def to_dict(self) -> dict[str, Any]:
        """Datetimes as ISO 8601 strings in UTC."""
        return {
            "uid": self.uid,
            "email": self.email,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "phone": self.phone,
            "photo_url": self.photo_url,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "terms_accepted_at": self.terms_accepted_at.isoformat() if self.terms_accepted_at else None,
            "terms_version": self.terms_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> User:
        accepted = data.get("terms_accepted_at")
        return cls(
            uid=data["uid"],
            email=data.get("email"),
            first_name=data.get("first_name"),
            last_name=data.get("last_name"),
            phone=data.get("phone"),
            photo_url=data.get("photo_url"),
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            terms_accepted_at=datetime.fromisoformat(accepted) if accepted else None,
            terms_version=data.get("terms_version"),
        )
