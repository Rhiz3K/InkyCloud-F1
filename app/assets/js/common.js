/**
 * F1 InkyCloud - Common JavaScript
 * Shared utilities and functions across all pages
 */

const FALLBACK_LANGUAGE_CODES = [
    "cs",
    "de",
    "en",
    "es",
    "fr",
    "it",
    "ja",
    "nl",
    "pl",
    "pt-BR",
    "sk",
    "tr",
    "zh-CN",
];

const SUPPORTED_LANGUAGE_CODES =
    Array.isArray(window.SERVER_LANGUAGE_CODES) &&
    window.SERVER_LANGUAGE_CODES.length > 0
        ? window.SERVER_LANGUAGE_CODES
        : FALLBACK_LANGUAGE_CODES;

function isSupportedLanguage(code) {
    return SUPPORTED_LANGUAGE_CODES.includes(code);
}

function stripLanguagePrefix(path) {
    const normalizedPath = path || "/";
    const langPrefixes = SUPPORTED_LANGUAGE_CODES.map((code) => "/" + code);
    for (const prefix of langPrefixes) {
        if (normalizedPath === prefix || normalizedPath === prefix + "/") {
            return "/";
        }
        if (normalizedPath.startsWith(prefix + "/")) {
            return normalizedPath.substring(prefix.length);
        }
    }
    return normalizedPath;
}

/**
 * Get the base path without language prefix
 * @param {string} path - Current path
 * @returns {string} Path without language prefix
 */
function getBasePath(path) {
    return stripLanguagePrefix(path);
}

/**
 * Get current language from URL path
 * @returns {string} Current language code
 */
function getCurrentLang() {
    const path = window.location.pathname;
    for (const lang of SUPPORTED_LANGUAGE_CODES) {
        if (lang === "en") {
            continue;
        }
        if (path.startsWith("/" + lang + "/") || path === "/" + lang) {
            return lang;
        }
    }
    return "en";
}

/**
 * Build URL with language prefix
 * @param {string} basePath - Path without language prefix
 * @param {string} lang - Target language
 * @returns {string} Full URL with language prefix
 */
function buildLangUrl(basePath, lang) {
    const nextLang = isSupportedLanguage(lang) ? lang : "en";
    const normalizedBasePath = stripLanguagePrefix(basePath || "/");
    const url = new URL(window.location.origin);
    const currentSearch = new URLSearchParams(window.location.search);
    // Remove any ?lang= query param
    currentSearch.delete("lang");
    url.search = currentSearch.toString();

    if (nextLang === "en") {
        url.pathname = normalizedBasePath;
    } else {
        url.pathname = "/" + nextLang + normalizedBasePath;
    }
    return url.toString();
}

// Language cookie lifetime (30 days); stated on the privacy page and next to the switcher.
const LANGUAGE_COOKIE_MAX_AGE_SECONDS = 2592000;

// Called only from switchUiLanguage, so storage is written solely on an explicit choice.
function saveLanguagePreference(lang) {
    if (!isSupportedLanguage(lang)) return;
    try { localStorage.setItem("preferredLang", lang); } catch (_) {}
    try {
        // Secure only on HTTPS so plain-http localhost development keeps the preference.
        const secure = window.location.protocol === "https:" ? ";Secure" : "";
        document.cookie = `preferredLang=${lang};path=/;max-age=${LANGUAGE_COOKIE_MAX_AGE_SECONDS};SameSite=Lax${secure}`;
    } catch (_) {}
}

// Reads an earlier choice and redirects to it; never writes or refreshes the preference.
(function initLanguagePreference() {
    try {
        const currentLang = getCurrentLang();
        const path = window.location.pathname || "/";
        if (stripLanguagePrefix(path) !== path) return;
        const storedLang = localStorage.getItem("preferredLang");
        if (!isSupportedLanguage(storedLang)) return;
        if (currentLang !== storedLang) {
            window.location.replace(buildLangUrl(getBasePath(path), storedLang));
        }
    } catch (_) {}
})();

function switchUiLanguage() {
    const lang = document.getElementById("uiLangSwitch").value;
    if (!isSupportedLanguage(lang)) return;
    saveLanguagePreference(lang);
    window.location.href = buildLangUrl(getBasePath(window.location.pathname), lang);
}

/**
 * Format bytes to human readable string
 * @param {number} bytes - Number of bytes
 * @returns {string} Formatted string (e.g., "1.5 MB")
 */
function formatBytes(bytes) {
    if (bytes < 1000) return bytes + " B";
    if (bytes < 1000000) return (bytes / 1000).toFixed(1) + " KB";
    if (bytes < 1000000000) return (bytes / 1000000).toFixed(2) + " MB";
    return (bytes / 1000000000).toFixed(2) + " GB";
}

/**
 * Copy text to clipboard with fallback
 * @param {string} text - Text to copy
 * @returns {Promise<boolean>} Success status
 */
async function copyToClipboard(text) {
    try {
        await navigator.clipboard.writeText(text);
        return true;
    } catch (err) {
        console.error("Failed to copy:", err);
        return false;
    }
}

/**
 * Toggle mobile menu - on homepage opens sidebar, on other pages opens nav overlay
 */
function toggleMobileMenu() {
    // Check if we're on homepage (settingsSidebar exists)
    const sidebar = document.getElementById("settingsSidebar");
    if (sidebar) {
        // Homepage - use existing toggleSidebar function
        if (typeof toggleSidebar === "function") {
            toggleSidebar();
        }
    } else {
        // Other pages - use mobile nav overlay
        openMobileNav();
    }
}

/**
 * Open mobile navigation overlay (non-home pages)
 */
function openMobileNav() {
    const overlay = document.getElementById("mobileNavOverlay");
    const menu = document.getElementById("mobileNavMenu");
    if (overlay && menu) {
        overlay.classList.remove("hidden");
        menu.classList.remove("-translate-x-full");
    }
}

/**
 * Close mobile navigation overlay
 */
function closeMobileNav() {
    const overlay = document.getElementById("mobileNavOverlay");
    const menu = document.getElementById("mobileNavMenu");
    if (overlay && menu) {
        overlay.classList.add("hidden");
        menu.classList.add("-translate-x-full");
    }
}

/** Format an ISO calendar date without applying the browser's timezone. */
function formatRaceDate(value) {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value || "");
    if (!match) return "";
    const date = new Date(`${value}T00:00:00Z`);
    if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== value) return "";
    return `${match[3]}.${match[2]}.`;
}

// Page handlers are top-level function declarations in nonce'd inline scripts
// (configure.html, configure/_*.html, api_docs.html). This file is loaded with
// defer, so it runs only after every inline page script has executed; the
// snapshot below therefore sees them, and later reassignment of window.X does
// not change what is dispatched.
const PAGE_HANDLER_NAMES = [
    "closeModalBtn",
    "copyCode",
    "switchTab",
    "tryCalendarBmp",
    "tryEndpoint",
    "tryTeamsBmp",
    "copyUrl",
    "filterTzContinent",
    "onTzSearch",
    "refreshPreview",
    "selectRaceFromList",
    "selectRaceMobile",
    "selectTeamsSeason",
    "selectTimezoneMobile",
    "selectYearMobile",
    "setDisplayType",
    "setTeamsExtra",
    "setWeatherType",
    "toggleSidebar",
    "updatePreview",
    "updateWeatherTypeAvailability",
];

/** Frozen allowlist of the only functions data-on-* attributes may call. */
const HANDLERS = Object.freeze(Object.fromEntries([
    ["toggleMobileMenu", toggleMobileMenu],
    ["switchUiLanguage", switchUiLanguage],
    ["closeMobileNav", closeMobileNav],
    ...PAGE_HANDLER_NAMES
        .filter((name) => typeof globalThis[name] === "function")
        .map((name) => [name, globalThis[name]]),
]));

/**
 * CSP-safe replacement for inline on* attributes. An element may declare
 * data-on-click / data-on-change / data-on-input="setWeatherType updatePreview"
 * (names from the HANDLERS allowlist, called in order) and optional
 * data-args='["x", 2]' (JSON array passed to every listed function). Each
 * function is called as HANDLERS[name].apply(element, args). Names not in
 * HANDLERS are ignored; there is no fallback to window.
 * @param {Event} event - Delegated click, change or input event
 */
function dispatchDeclaredHandler(event) {
    const attr = "data-on-" + event.type;
    const target = event.target;
    const element = target && typeof target.closest === "function" ? target.closest("[" + attr + "]") : null;
    if (!element) return;
    let args = [];
    const rawArgs = element.getAttribute("data-args");
    if (rawArgs) {
        try {
            const parsed = JSON.parse(rawArgs);
            if (Array.isArray(parsed)) args = parsed;
        } catch (err) {
            console.error("Invalid data-args:", err);
        }
    }
    for (const name of (element.getAttribute(attr) || "").split(/\s+/)) {
        if (Object.hasOwn(HANDLERS, name)) HANDLERS[name].apply(element, args);
    }
}

for (const type of ["click", "change", "input"]) {
    document.addEventListener(type, dispatchDeclaredHandler);
}

/**
 * CSP-safe replacement for inline style="width: ..." attributes: copies each
 * data-width value (a full CSS width such as "12.5%" or "max(2px, 3%)") to
 * the element's style via CSSOM.
 * @param {ParentNode} [root=document] - Subtree to scan
 */
function applyDeclaredWidths(root) {
    for (const el of (root || document).querySelectorAll("[data-width]")) {
        el.style.width = el.dataset.width;
    }
}

/**
 * CSP-safe replacement for inline onerror image fallbacks: swaps an
 * <img data-fallback-src> to its fallback once, so a failing fallback never loops.
 * @param {Element} img - Image that failed to load
 */
function applyImageFallback(img) {
    if (!img || img.tagName !== "IMG") return;
    const fallback = img.dataset.fallbackSrc;
    if (!fallback || img.dataset.fallbackApplied) return;
    img.dataset.fallbackApplied = "true";
    img.src = fallback;
}

// Image error events do not bubble, so listen in the capture phase.
document.addEventListener("error", (event) => applyImageFallback(event.target), true);

/**
 * Fall back images that already failed before this deferred script ran.
 * @param {ParentNode} [root=document] - Subtree to scan
 */
function applyFallbackToFailedImages(root) {
    for (const img of (root || document).querySelectorAll("img[data-fallback-src]")) {
        if (img.complete && img.naturalWidth === 0 && img.getAttribute("src") !== img.dataset.fallbackSrc) {
            applyImageFallback(img);
        }
    }
}

function initDeclaredPresentation() {
    applyDeclaredWidths(document);
    applyFallbackToFailedImages(document);
}

if (!document.readyState || document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initDeclaredPresentation);
} else {
    initDeclaredPresentation();
}

if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
}
