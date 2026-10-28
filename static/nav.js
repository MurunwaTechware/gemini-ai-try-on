// Top bar for the signed-in pages: a menu button, the brand, and the user's picture (a link to /account).
// The menu is a drawer: "My Account" slides to the account sections and Sign out; below it, the app's pages.
// Placeholder until the real frontend has its own. Add to a page with <script src="/static/nav.js" defer></script>.
(() => {
    const HEIGHT = 56;

    const style = document.createElement("style");
    style.textContent = `
        .tn-bar {
            position: fixed; top: 0; left: 0; right: 0; height: ${HEIGHT}px; z-index: 900;
            display: flex; align-items: center; justify-content: space-between; gap: 12px;
            padding: 0 20px; background: rgba(255,255,255,0.92); backdrop-filter: blur(8px);
            border-bottom: 1px solid #e5e5e7;
            font: 14px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; color: #1d1d1f;
        }
        .tn-left { display: flex; align-items: center; gap: 6px; }
        .tn-brand { font-weight: 700; font-size: 16px; color: #1d1d1f; text-decoration: none; }
        .tn-icon-btn {
            background: none; border: none; padding: 8px; margin-left: -8px; cursor: pointer; color: inherit;
            line-height: 0;
        }
        .tn-account { display: flex; color: inherit; text-decoration: none; }
        .tn-account[hidden] { display: none; }
        .tn-avatar {
            width: 32px; height: 32px; border-radius: 50%; flex: none; background: #0071e3; color: white;
            display: flex; align-items: center; justify-content: center; font-weight: 600; font-size: 13px;
            overflow: hidden;
        }
        .tn-avatar img { width: 100%; height: 100%; object-fit: cover; }
        .tn-signout-big:disabled { opacity: 0.6; cursor: default; }

        /* Menu drawer. Sized the same whatever the page's own CSS says */
        .tn-drawer, .tn-drawer * { box-sizing: border-box; }
        .tn-backdrop {
            position: fixed; inset: 0; z-index: 950; background: rgba(0,0,0,0.5);
            opacity: 0; visibility: hidden; transition: opacity 0.25s, visibility 0s 0.25s;
        }
        .tn-drawer {
            position: fixed; top: 0; bottom: 0; left: 0; z-index: 960; width: min(100%, 420px);
            display: flex; flex-direction: column; background: white; color: #000;
            overflow: clip;   /* not hidden: that can still be scrolled sideways, showing the wrong panel */
            font: 16px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            transform: translateX(-100%); visibility: hidden; transition: transform 0.25s, visibility 0s 0.25s;
        }
        .tn-open .tn-backdrop { opacity: 1; visibility: visible; transition: opacity 0.25s; }
        .tn-open .tn-drawer { transform: none; visibility: visible; transition: transform 0.25s; }
        .tn-drawer-head {
            flex: none; display: flex; align-items: center; gap: 18px; height: 80px; padding: 0 24px;
            background: #000; color: white;
        }
        .tn-drawer-head .tn-icon-btn { margin-left: -8px; }
        .tn-drawer-title { font-size: 20px; font-weight: 700; }
        .tn-panels { flex: 1; display: flex; width: 200%; min-height: 0; transition: transform 0.25s; }
        .tn-panels.tn-sub { transform: translateX(-50%); }
        .tn-panel { width: 50%; min-width: 0; overflow-y: auto; padding: 18px 24px 32px; }
        .tn-row {
            display: flex; align-items: center; gap: 12px; width: 100%; padding: 14px 0; border: none;
            background: none; font: inherit; font-size: 18px; color: inherit; text-align: left; cursor: pointer;
        }
        .tn-row .tn-chevron { margin-left: auto; flex: none; }
        .tn-page {
            display: block; padding: 18px 0; font-size: 20px; font-weight: 700; color: #000; text-decoration: none;
        }
        .tn-page:hover, .tn-page[aria-current="page"] { text-decoration: underline; text-underline-offset: 6px; }
        .tn-badge {
            width: 28px; height: 28px; border-radius: 50%; border: 2px solid #000; flex: none;
            display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700;
        }
        .tn-back { font-size: 17px; padding: 6px 0 22px; }
        .tn-hello { font-size: 22px; font-weight: 700; margin: 8px 0 18px; overflow-wrap: anywhere; }
        .tn-link {
            display: block; padding: 24px 0; font-size: 19px; color: #000; text-decoration: none;
            border-bottom: 1px solid #c8c8c8;
        }
        .tn-link:last-of-type { border-bottom: none; }
        .tn-link:hover { text-decoration: underline; }
        .tn-signout-big {
            display: block; width: 100%; margin-top: 12px; padding: 14px; border: 2px solid #000; background: white;
            color: #000; font: inherit; font-size: 20px; font-weight: 700; text-transform: uppercase; cursor: pointer;
        }
        .tn-signout-big:hover:not(:disabled) { background: #000; color: white; }
        @media (prefers-reduced-motion: reduce) {
            .tn-backdrop, .tn-drawer, .tn-panels, .tn-open .tn-backdrop, .tn-open .tn-drawer { transition: none; }
        }
    `;
    document.head.appendChild(style);

    const ICON = {
        menu: `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 6h18M3 12h18M3 18h18"/></svg>`,
        close: `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M5 5l14 14M19 5L5 19"/></svg>`,
        chevron: `<svg class="tn-chevron" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg>`,
        back: `<svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M20 12H4M10 6l-6 6 6 6"/></svg>`,
    };

    const bar = document.createElement("header");
    bar.className = "tn-bar";
    bar.innerHTML = `
        <div class="tn-left">
            <button class="tn-icon-btn tn-menu-btn" type="button" aria-label="Open menu" aria-expanded="false"
                    aria-controls="tn-drawer">${ICON.menu}</button>
            <a class="tn-brand" href="/">Mannequin AI</a>
        </div>
        <a class="tn-account" href="/account" aria-label="My account" hidden>
            <div class="tn-avatar" aria-hidden="true"></div>
        </a>`;

    // Two panels side by side: the menu itself, and My Account, which slides in over it
    const menu = document.createElement("div");
    menu.innerHTML = `
        <div class="tn-backdrop"></div>
        <div class="tn-drawer" id="tn-drawer" role="dialog" aria-modal="true" aria-label="Menu">
            <div class="tn-drawer-head">
                <button class="tn-icon-btn tn-close" type="button" aria-label="Close menu">${ICON.close}</button>
                <span class="tn-drawer-title">Menu</span>
            </div>
            <div class="tn-panels">
                <div class="tn-panel" data-panel="main">
                    <button class="tn-row tn-to-account" type="button">
                        <span class="tn-badge" aria-hidden="true"></span><span>My Account</span>${ICON.chevron}
                    </button>
                    <a class="tn-page" href="/">Upload Photos</a>
                    <a class="tn-page" href="/tryon">Virtual Try-on</a>
                </div>
                <div class="tn-panel" data-panel="account" inert>
                    <button class="tn-row tn-back" type="button">${ICON.back}<span>All</span></button>
                    <p class="tn-hello">Hi</p>
                    <a class="tn-link" href="/account#info">Account Info</a>
                    <a class="tn-link" href="/account#profiles">Measurement Profiles</a>
                    <a class="tn-link" href="/account#tryons">Saved Try-ons</a>
                    <button class="tn-signout-big" type="button">Sign out</button>
                </div>
            </div>
        </div>`;

    document.body.prepend(bar);
    document.body.append(menu);

    // The bar is fixed, so push the page down by its height on top of the page's own spacing
    const padTop = parseFloat(getComputedStyle(document.body).paddingTop) || 0;
    document.body.style.paddingTop = `${padTop + HEIGHT}px`;

    const $ = (sel) => bar.querySelector(sel);
    const $m = (sel) => menu.querySelector(sel);

    // Mark the page we're on, for the underline and for screen readers
    menu.querySelectorAll(".tn-page").forEach((a) => {
        if (a.pathname === location.pathname) a.setAttribute("aria-current", "page");
    });

    fetch("/me")
        .then((res) => (res.ok ? res.json() : null))
        .then((me) => {
            if (!me) return;   // not signed in: the page's own requests will say so
            const words = (me.name || "").trim().split(/\s+/).filter(Boolean);
            const first = me.first_name || words[0] || "";
            // textContent throughout: names are user data
            $(".tn-account").title = [me.name, me.email].filter(Boolean).join("\n");   // shown on hover
            $(".tn-avatar").textContent = (first || me.email || "?").charAt(0).toUpperCase();
            $m(".tn-hello").textContent = first ? `Hi, ${first}` : "Hi";
            const initials = [me.first_name || words[0], me.last_name || words[1]].filter(Boolean).map((n) => n[0]);
            $m(".tn-badge").textContent = (initials.join("") || (me.email || "?")[0]).toUpperCase();
            if (me.photo_url) {   // Google's picture; the initial stays if it doesn't load
                const img = new Image();
                img.alt = "";
                img.referrerPolicy = "no-referrer";   // Google's image server can refuse requests from other sites
                img.onload = () => $(".tn-avatar").replaceChildren(img);
                img.src = me.photo_url;
            }
            $(".tn-account").hidden = false;
        })
        .catch(() => {});

    // ── Menu ────────────────────────────────────────────────────────────────

    const menuBtn = $(".tn-menu-btn");
    const panels = $m(".tn-panels");

    function showPanel(name) {
        panels.classList.toggle("tn-sub", name === "account");
        // inert: the panel slid out of view can't be tabbed to or read out
        for (const panel of menu.querySelectorAll(".tn-panel")) panel.inert = panel.dataset.panel !== name;
        // preventScroll: focusing a panel mid-slide would otherwise scroll the drawer sideways and blank it
        (name === "account" ? $m(".tn-back") : $m(".tn-to-account")).focus({ preventScroll: true });
    }

    function openMenu() {
        menu.classList.add("tn-open");
        menuBtn.setAttribute("aria-expanded", "true");
        document.body.style.overflow = "hidden";   // the page behind shouldn't scroll
        showPanel("main");
    }

    function closeMenu() {
        if (!menu.classList.contains("tn-open")) return;
        menu.classList.remove("tn-open");
        menuBtn.setAttribute("aria-expanded", "false");
        document.body.style.overflow = "";
        menuBtn.focus({ preventScroll: true });
    }

    menuBtn.addEventListener("click", openMenu);
    $m(".tn-close").addEventListener("click", closeMenu);
    $m(".tn-backdrop").addEventListener("click", closeMenu);
    $m(".tn-to-account").addEventListener("click", () => showPanel("account"));
    $m(".tn-back").addEventListener("click", () => showPanel("main"));
    addEventListener("keydown", (event) => { if (event.key === "Escape") closeMenu(); });
    // On /account the links only change the #section, so the page stays; close the menu over it
    menu.querySelectorAll(".tn-link").forEach((a) => a.addEventListener("click", closeMenu));

    async function signOut(button) {
        button.disabled = true;
        try {
            await fetch("/logout", { method: "POST" });
        } finally {
            location.replace("/login");
        }
    }
    $m(".tn-signout-big").addEventListener("click", (e) => signOut(e.currentTarget));
})();
