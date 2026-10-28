"""Sign-in. Firebase checks the Google login; after that this app keeps the user signed in with its
own HttpOnly session cookie, and every route takes the user ID from that cookie, never from the request.

Flow:
  1. /login: the browser signs in through the Firebase JS SDK, with Google or with an email and
     password, and gets a Firebase ID token
  2. POST /session: the server verifies that token, saves the user's record (user_store.py) and swaps
     the token for a session cookie (HttpOnly, so page scripts can't read it). The browser sends the
     cookie on every request, SSE included.
  3. Each route: current_user (API) or page_user (pages) verifies the cookie and returns the user.

The UID is Firebase's, so it's the same user whichever way they sign in once the methods are linked.
Linking happens on /account, in the browser: Firebase only links for a user who has just signed in.
The account page and the user's own data (/me...) are in account.py.

With AUTH_ENABLED=false (config.py) none of this runs: every request is DEMO_USER, /login goes
straight into the app, and Firebase is never set up. For local development and demos only.
"""
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote, urlsplit

import firebase_admin
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from firebase_admin import auth as fb_auth
from pydantic import BaseModel

import config
import jobs
import user_store

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
COOKIE = "session"
RECENT_SIGN_IN_S = 5 * 60   # only swap ID tokens from a sign-in that just happened, as Firebase recommends


@dataclass(frozen=True)
class User:
    uid: str
    email: str | None
    name: str | None   # from the Google account; Apple only shares it once, at first sign-in
    photo_url: str | None   # Google's profile picture; none for Apple or email/password


# Every request's user when sign-in is off. Its records live in memory (see user_store.py).
DEMO_USER = User(uid=config.DEMO_USER_ID, email="demo@example.com", name="Demo User", photo_url=None)


class LoginRequired(Exception):
    """Raised by page_user; main.py turns it into a redirect to /login?next=<page>."""
    def __init__(self, next_path: str):
        self.next_path = next_path


def init() -> None:
    if not config.AUTH_ENABLED:
        log.warning("Sign-in is OFF (AUTH_ENABLED=false): every request is the demo user %r. "
                    "Never run a shared or public server like this.", DEMO_USER.uid)
        return
    if not config.FIREBASE_API_KEY or not config.FIREBASE_PROJECT_ID:
        raise RuntimeError("Sign-in isn't configured: set FIREBASE_API_KEY in .env (see README, Sign-in).")
    try:
        firebase_admin.get_app()
    except ValueError:   # not initialised yet. Credentials come from ADC, like the Gemini calls
        firebase_admin.initialize_app(options={"projectId": config.FIREBASE_PROJECT_ID})
    if not config.ALLOWED_EMAILS:
        log.warning("ALLOWED_EMAILS is empty: anyone with a Google account can sign in. List the people (or "
                    "@domains) who may, before running this anywhere others can reach it.")


def email_allowed(claims: dict) -> bool:
    """Whether this sign-in's email is on ALLOWED_EMAILS, by address or by domain. Only verified emails count:
    Google's always are, and email/password accounts must be verified before they get a session anyway."""
    if not config.ALLOWED_EMAILS:
        return True
    email = (claims.get("email") or "").lower()
    if not email or not claims.get("email_verified"):
        return False
    return email in config.ALLOWED_EMAILS or f"@{email.rpartition('@')[2]}" in config.ALLOWED_EMAILS


def safe_next(path: str | None) -> str:
    """Only same-site paths, so /login?next=https://evil.example can't send users elsewhere."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path


# ── Checks used by routes ─────────────────────────────────────────────────────

def _user_from_cookie(request: Request) -> User | None:
    cookie = request.cookies.get(COOKIE)
    if not cookie:
        return None
    try:
        # check_revoked asks Firebase each time, so a disabled or signed-out-everywhere user is
        # refused at once rather than when the cookie expires. Costs one network call per request.
        claims = fb_auth.verify_session_cookie(cookie, check_revoked=True)
    except (fb_auth.InvalidSessionCookieError, fb_auth.RevokedSessionCookieError, fb_auth.UserDisabledError):
        return None   # expired, revoked, disabled or tampered with: same as not signed in
    if not jobs.USER_ID.fullmatch(claims["uid"]):
        log.warning("refusing UID that isn't safe as a folder name: %r", claims["uid"])
        return None
    if not email_allowed(claims):   # taken off ALLOWED_EMAILS since signing in: signed out from now on
        return None
    return User(uid=claims["uid"], email=claims.get("email"), name=claims.get("name"),
                photo_url=claims.get("picture"))


# Plain `def`, so FastAPI runs these in a thread: the Firebase calls block
def current_user(request: Request) -> User:
    """For API routes: 401 when not signed in. The pages show the message."""
    if not config.AUTH_ENABLED:
        return DEMO_USER
    user = _user_from_cookie(request)
    if not user:
        raise HTTPException(401, "Your sign-in has expired. Reload the page to sign in again.")
    return user


def page_user(request: Request) -> User:
    """For pages: redirect to /login, then back here once signed in."""
    if not config.AUTH_ENABLED:
        return DEMO_USER
    user = _user_from_cookie(request)
    if not user:
        query = f"?{request.url.query}" if request.url.query else ""
        raise LoginRequired(request.url.path + query)
    return user


def same_origin(request: Request) -> None:
    """CSRF guard for routes that change something. The cookie is sent automatically, so without this
    another site could make a signed-in browser POST here. SameSite=Lax blocks most of that; checking
    Origin closes the rest. Browsers always send Origin on POST, and pages can't change it.
    Allowed: this server's own address, or one of TRUSTED_ORIGINS (the frontend, when its server forwards
    requests here: those arrive with the frontend's Origin but this server's Host)."""
    origin = request.headers.get("origin")
    if origin and (urlsplit(origin).netloc == request.headers.get("host")
                   or origin.rstrip("/").lower() in config.TRUSTED_ORIGINS):
        return
    raise HTTPException(403, "Request blocked: it didn't come from this site.")


# ── Routes ────────────────────────────────────────────────────────────────────

router = APIRouter()


class SessionRequest(BaseModel):
    id_token: str


@router.get("/login", include_in_schema=False)
def login_page(next: str | None = None):
    if not config.AUTH_ENABLED:   # nothing to sign in to: go where they were heading
        return RedirectResponse(safe_next(next), status_code=303)
    return FileResponse(STATIC / "login.html")


@router.get("/auth/config")
def firebase_web_config():
    """What the login page needs to start the Firebase JS SDK. Public values, not secrets."""
    if not config.AUTH_ENABLED:
        return {"enabled": False}   # the pages then skip starting Firebase (auth-client.js)
    return {"enabled": True, "apiKey": config.FIREBASE_API_KEY, "authDomain": config.FIREBASE_AUTH_DOMAIN,
            "projectId": config.FIREBASE_PROJECT_ID}


@router.post("/session", dependencies=[Depends(same_origin)])
def create_session(body: SessionRequest, response: Response):
    if not config.AUTH_ENABLED:
        raise HTTPException(404, "Sign-in is turned off on this server (AUTH_ENABLED=false).")
    try:
        claims = fb_auth.verify_id_token(body.id_token, check_revoked=True)
    except (fb_auth.InvalidIdTokenError, fb_auth.RevokedIdTokenError, fb_auth.UserDisabledError):
        raise HTTPException(401, "Sign-in failed. Please try again.")
    if time.time() - claims["auth_time"] > RECENT_SIGN_IN_S:
        raise HTTPException(401, "Please sign in again.")
    if not jobs.USER_ID.fullmatch(claims["uid"]):
        raise HTTPException(400, "This account can't be used here.")
    # Without this, anyone could sign up with someone else's address. Google and Apple verify emails themselves
    if claims["firebase"]["sign_in_provider"] == "password" and not claims.get("email_verified"):
        raise HTTPException(403, "Verify your email first: open the link we sent you, then sign in.")
    if not email_allowed(claims):
        log.info("sign-in refused, not on ALLOWED_EMAILS: %s", claims.get("email"))
        raise HTTPException(403, "This account doesn't have access to this app yet. Ask the team to add your "
                                 "email address.")

    try:
        user_store.record_sign_in(claims)
    except Exception:   # not just GoogleAPICallError: a transaction that fails to start surfaces as a ValueError
        log.exception("couldn't save the user record for %s", claims["uid"])   # the log shows the real cause
        raise HTTPException(503, "Sign-in is unavailable right now. Please try again shortly.")

    expires = timedelta(days=config.SESSION_DAYS)
    cookie = fb_auth.create_session_cookie(body.id_token, expires_in=expires)
    response.set_cookie(COOKIE, cookie, max_age=int(expires.total_seconds()), path="/",
                        httponly=True, secure=config.COOKIE_SECURE, samesite="lax")
    return {"ok": True}


@router.post("/logout", dependencies=[Depends(same_origin)])
def logout(response: Response):
    # Signs out this browser only. fb_auth.revoke_refresh_tokens(uid) would sign out every device.
    response.delete_cookie(COOKIE, path="/", httponly=True, secure=config.COOKIE_SECURE, samesite="lax")
    return {"ok": True}


def login_redirect_url(next_path: str) -> str:
    return f"/login?next={quote(safe_next(next_path), safe='')}"
