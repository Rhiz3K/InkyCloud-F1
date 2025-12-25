/**
 * F1 InkyCloud - Common JavaScript
 * Shared utilities and functions across all pages
 */

/**
 * Switch UI language by updating URL parameter and reloading
 */
function switchUiLanguage() {
    const lang = document.getElementById('uiLangSwitch').value;
    localStorage.setItem('preferredLang', lang);
    const url = new URL(window.location.href);
    url.searchParams.set('lang', lang);
    window.location.href = url.toString();
}

/**
 * Format bytes to human readable string
 * @param {number} bytes - Number of bytes
 * @returns {string} Formatted string (e.g., "1.5 MB")
 */
function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return Math.round(bytes / 1024) + ' KB';
    if (bytes < 1024 * 1024 * 1024) return Math.round(bytes / (1024 * 1024)) + ' MB';
    return (bytes / (1024 * 1024 * 1024)).toFixed(1) + ' GB';
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
        console.error('Failed to copy:', err);
        return false;
    }
}

/**
 * Toggle mobile menu - on homepage opens sidebar, on other pages opens nav overlay
 */
function toggleMobileMenu() {
    // Check if we're on homepage (settingsSidebar exists)
    const sidebar = document.getElementById('settingsSidebar');
    if (sidebar) {
        // Homepage - use existing toggleSidebar function
        if (typeof toggleSidebar === 'function') {
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
    const overlay = document.getElementById('mobileNavOverlay');
    const menu = document.getElementById('mobileNavMenu');
    if (overlay && menu) {
        overlay.classList.remove('hidden');
        menu.classList.remove('-translate-x-full');
    }
}

/**
 * Close mobile navigation overlay
 */
function closeMobileNav() {
    const overlay = document.getElementById('mobileNavOverlay');
    const menu = document.getElementById('mobileNavMenu');
    if (overlay && menu) {
        overlay.classList.add('hidden');
        menu.classList.add('-translate-x-full');
    }
}
