// Shared by the sign-in and account pages: starts the Firebase JS SDK and turns its error codes into
// messages. Firebase only does the signing in; the server's HttpOnly cookie is what keeps the user signed in.
import { initializeApp } from "https://www.gstatic.com/firebasejs/12.19.0/firebase-app.js";
import { getAuth, setPersistence, inMemoryPersistence } from "https://www.gstatic.com/firebasejs/12.19.0/firebase-auth.js";

export async function startAuth() {
    const config = await (await fetch("/auth/config")).json();
    const auth = getAuth(initializeApp(config));
    // Keep nothing in browser storage: the server's cookie is the sign-in
    await setPersistence(auth, inMemoryPersistence);
    return auth;
}

const MESSAGES = {
    "auth/popup-blocked": "The browser blocked the sign-in window. Allow pop-ups for this site and try again.",
    "auth/unauthorized-domain": "This address isn't on Firebase's authorised domains list (Authentication → Settings).",
    "auth/operation-not-allowed": "This sign-in method isn't turned on in Firebase (Authentication → Sign-in method).",
    "auth/configuration-not-found": "Sign-in isn't set up in Firebase yet (Authentication → Get started).",
    "auth/network-request-failed": "Couldn't reach Google. Check your connection and try again.",
    "auth/too-many-requests": "Too many attempts. Wait a few minutes and try again.",
    // Firebase doesn't say whether the email exists (email enumeration protection), so neither do we
    "auth/invalid-credential": "Wrong email or password. If you signed up with Google, use Continue with Google.",
    "auth/invalid-email": "That email address doesn't look right.",
    "auth/missing-password": "Enter your password.",
    "auth/weak-password": "That password is too weak. Use at least 8 characters.",
    "auth/email-already-in-use": "There's already an account for this email. Sign in, or use Continue with Google.",
    "auth/account-exists-with-different-credential":
        "There's already an account for this email. Sign in with your email and password, then connect Google on your account page.",
    "auth/credential-already-in-use": "That Google account already belongs to a different account here.",
    "auth/provider-already-linked": "That's already set up on this account.",
    "auth/requires-recent-login": "Please confirm it's you again.",
};

export const errorMessage = (err, overrides = {}) => overrides[err.code] || MESSAGES[err.code] || err.message;

// Closing the Google window isn't worth an error message
export const isCancelled = (err) =>
    err.code === "auth/popup-closed-by-user" || err.code === "auth/cancelled-popup-request";
