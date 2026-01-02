# Change: Add SEO Optimization for Production Release

## Why

Before production launch, the application needs proper SEO optimization to improve search engine visibility and social media sharing. Currently, the site lacks meta descriptions, Open Graph tags, Twitter Cards, structured data, robots.txt, and sitemap.xml - all critical for discoverability.

Reference: GitHub Issue #42

## What Changes

### Meta Tags
- Add dynamic `<meta name="description">` per page with localized content
- Add `<meta name="keywords">` with F1-related keywords
- Add `<meta name="author">` tag
- Add canonical URLs (`<link rel="canonical">`)

### Social Media Tags
- Add Open Graph tags (og:title, og:description, og:image, og:url, og:type, og:site_name)
- Add Twitter Card tags (twitter:card, twitter:title, twitter:description, twitter:image)
- Create dedicated OG preview image (1200x630 PNG)

### Technical SEO
- Add `/robots.txt` endpoint
- Add `/sitemap.xml` endpoint
- Add `<link rel="alternate" hreflang="x">` for language variants

### Structured Data
- Add JSON-LD WebApplication schema to homepage

### Translations
- Add SEO-related translation keys for meta descriptions

## Impact

- **Affected code**: `app/main.py`, `app/templates/base.html`, `app/templates/*.html`
- **Affected assets**: New `og-preview.png` image in `app/assets/images/`
- **Affected translations**: New keys in `translations/en.json` and `translations/cs.json`
- **New endpoints**: `/robots.txt`, `/sitemap.xml`
- **No breaking changes**: Backward compatible addition
