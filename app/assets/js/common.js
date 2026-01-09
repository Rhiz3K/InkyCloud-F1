/**
 * F1 InkyCloud - Common JavaScript
 * Shared utilities and functions across all pages
 */

(function initLanguagePreference() {
    try {
        const storedLang = localStorage.getItem("preferredLang");
        if (!storedLang) return;

        const url = new URL(window.location.href);
        const urlLang = url.searchParams.get("lang");

        if (urlLang !== storedLang) {
            url.searchParams.set("lang", storedLang);
            window.location.replace(url.toString());
        }
    } catch (e) {
        console.error("Failed to apply language preference from localStorage:", e);
    }
})();

/**
 * Switch UI language by updating URL parameter and reloading
 */
function switchUiLanguage() {
    const lang = document.getElementById("uiLangSwitch").value;
    localStorage.setItem("preferredLang", lang);
    const url = new URL(window.location.href);
    url.searchParams.set("lang", lang);
    window.location.href = url.toString();
}

/**
 * Format bytes to human readable string
 * @param {number} bytes - Number of bytes
 * @returns {string} Formatted string (e.g., "1.5 MB")
 */
function formatBytes(bytes) {
    if (bytes === 0) return "0 B";
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + " KB";
    if (bytes < 1024 * 1024 * 1024)
        return Math.round(bytes / (1024 * 1024)) + " MB";
    return (bytes / (1024 * 1024 * 1024)).toFixed(1) + " GB";
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

/**
 * Real User Monitoring - Collect and send Web Vitals
 * Metrics: LCP, CLS, FCP, TTFB, INP
 */
(function () {
    const metrics = {};
    let metricsReported = false;

    function sendMetrics() {
        if (metricsReported) return;
        if (!metrics.lcp && !metrics.cls && !metrics.fcp) return;
        metricsReported = true;

        const payload = {
            page_path: window.location.pathname,
            lcp_ms: metrics.lcp,
            cls: metrics.cls,
            fcp_ms: metrics.fcp,
            ttfb_ms: metrics.ttfb,
            inp_ms: metrics.inp,
            connection_type: navigator.connection?.effectiveType || null,
            device_memory: navigator.deviceMemory || null,
        };

        if (navigator.sendBeacon) {
            navigator.sendBeacon("/api/perf-metrics", JSON.stringify(payload));
        } else {
            fetch("/api/perf-metrics", {
                method: "POST",
                body: JSON.stringify(payload),
                headers: { "Content-Type": "application/json" },
                keepalive: true,
            }).catch(() => {});
        }
    }

    try {
        new PerformanceObserver((list) => {
            const entries = list.getEntries();
            const last = entries[entries.length - 1];
            if (last) {
                metrics.lcp = Math.round(last.startTime);
            }
        }).observe({ type: "largest-contentful-paint", buffered: true });
    } catch (e) {}

    try {
        let clsValue = 0;
        new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) {
                if (!entry.hadRecentInput) {
                    clsValue += entry.value;
                }
            }
            metrics.cls = Math.round(clsValue * 1000) / 1000;
        }).observe({ type: "layout-shift", buffered: true });
    } catch (e) {}

    try {
        new PerformanceObserver((list) => {
            const entry = list.getEntries()[0];
            if (entry) {
                metrics.fcp = Math.round(entry.startTime);
            }
        }).observe({ type: "paint", buffered: true });
    } catch (e) {}

    try {
        new PerformanceObserver((list) => {
            const entries = list.getEntries();
            const last = entries[entries.length - 1];
            if (last) {
                metrics.inp = Math.round(last.duration);
            }
        }).observe({ type: "event", buffered: true, durationThreshold: 16 });
    } catch (e) {}

    const navEntry = performance.getEntriesByType("navigation")[0];
    if (navEntry) {
        metrics.ttfb = Math.round(navEntry.responseStart);
    }

    window.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "hidden") {
            sendMetrics();
        }
    });

    window.addEventListener("pagehide", sendMetrics);

    setTimeout(sendMetrics, 10000);
})();

if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
}
