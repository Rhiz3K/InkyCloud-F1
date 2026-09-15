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

function saveLanguagePreference(lang) {
    if (!isSupportedLanguage(lang)) return;
    try { localStorage.setItem("preferredLang", lang); } catch (_) {}
    try { document.cookie = `preferredLang=${lang};path=/;max-age=31536000;SameSite=Lax`; } catch (_) {}
}

(function initLanguagePreference() {
    try {
        const currentLang = getCurrentLang();
        const path = window.location.pathname || "/";
        if (stripLanguagePrefix(path) !== path) {
            saveLanguagePreference(currentLang);
            return;
        }
        const storedLang = localStorage.getItem("preferredLang");
        if (!isSupportedLanguage(storedLang)) return;
        saveLanguagePreference(storedLang);
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

if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
}
