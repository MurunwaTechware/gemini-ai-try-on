"""User records in Firestore: one document per user, users/<UID>, holding models/Entities/Users.User.

Firebase Authentication keeps everything about signing in (providers, passwords). This is the app's own
data about each user, written at every sign-in. The server uses the Admin SDK, which isn't bound by
Firestore security rules, so the rules can stay at "deny all": browsers never read Firestore directly.

With sign-in off (config.AUTH_ENABLED false) records are kept in memory instead and Firestore is never
called. In practice that's only the demo user's record; edits to it last until the server restarts.
"""
from firebase_admin import firestore

import config
from models.Entities.Users import User

COLLECTION = "users"
_in_memory: dict[str, User] = {}   # sign-in off: records by UID, instead of Firestore


def _ref(uid: str):
    return firestore.client().collection(COLLECTION).document(uid)   # client is cached per app


def get(uid: str) -> User | None:
    if not config.AUTH_ENABLED:
        return _in_memory.get(uid)
    snap = _ref(uid).get()
    return User.from_dict(snap.to_dict()) if snap.exists else None


def record_sign_in(claims: dict) -> User:
    """Create the record on first sign-in, or update it from the sign-in's verified claims.
    In a transaction, so two sign-ins at once can't both create it."""
    if not config.AUTH_ENABLED:
        user = _in_memory.get(claims["uid"])
        if user is None:
            user = _in_memory[claims["uid"]] = User.from_firebase_claims(claims)
        else:
            user.update_from_claims(claims)
        return user
    ref = _ref(claims["uid"])

    @firestore.transactional
    def run(tx) -> User:
        snap = ref.get(transaction=tx)
        if not snap.exists:
            user = User.from_firebase_claims(claims)
        else:
            user = User.from_dict(snap.to_dict())
            if not user.update_from_claims(claims):
                return user   # nothing new: skip the write
        tx.set(ref, user.to_dict())
        return user

    return run(firestore.client().transaction())


def update_details(uid: str, changes: dict[str, str | None]) -> User:
    """Apply the user's own edits (names, phone) and return the updated record."""
    if not config.AUTH_ENABLED:
        if uid not in _in_memory:
            raise LookupError(f"No user record for {uid}")
        _in_memory[uid].update_details(changes)
        return _in_memory[uid]
    ref = _ref(uid)

    @firestore.transactional
    def run(tx) -> User:
        snap = ref.get(transaction=tx)
        if not snap.exists:
            raise LookupError(f"No user record for {uid}")
        user = User.from_dict(snap.to_dict())
        user.update_details(changes)
        tx.set(ref, user.to_dict())
        return user

    return run(firestore.client().transaction())
