"""User records in Firestore: one document per user, users/<UID>, holding models/Entities/Users.User.

Firebase Authentication keeps everything about signing in (providers, passwords). This is the app's own
data about each user, written at every sign-in. The server uses the Admin SDK, which isn't bound by
Firestore security rules, so the rules can stay at "deny all": browsers never read Firestore directly.
"""
from firebase_admin import firestore

from models.Entities.Users import User

COLLECTION = "users"


def _ref(uid: str):
    return firestore.client().collection(COLLECTION).document(uid)   # client is cached per app


def get(uid: str) -> User | None:
    snap = _ref(uid).get()
    return User.from_dict(snap.to_dict()) if snap.exists else None


def record_sign_in(claims: dict) -> User:
    """Create the record on first sign-in, or update it from the sign-in's verified claims.
    In a transaction, so two sign-ins at once can't both create it."""
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
