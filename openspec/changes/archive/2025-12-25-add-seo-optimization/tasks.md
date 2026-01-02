# Implementation Tasks

## 1. Translation Keys
- [x] 1.1 Add SEO translation keys to `translations/en.json` (meta_description_home, meta_description_api, meta_description_privacy, meta_description_stats, meta_keywords)
- [x] 1.2 Add SEO translation keys to `translations/cs.json` (same keys, Czech translations)

## 2. OG Preview Image
- [x] 2.1 Create 1200x630 PNG preview image for social sharing (based on existing branding)
- [x] 2.2 Save as `app/assets/images/og-preview.png`

## 3. Base Template Meta Tags
- [x] 3.1 Update `app/templates/base.html` to add `{% block meta %}` for page-specific meta tags
- [x] 3.2 Add canonical URL meta tag with `{% block canonical %}`
- [x] 3.3 Add Open Graph base tags (og:site_name, og:type, og:locale)
- [x] 3.4 Add Twitter Card base tags (twitter:card)
- [x] 3.5 Add hreflang alternate links for en/cs variants

## 4. Page-Specific Meta Tags
- [x] 4.1 Update `index.html` with page-specific meta description, OG tags, Twitter tags
- [x] 4.2 Update `api_docs.html` with page-specific meta description, OG tags, Twitter tags
- [x] 4.3 Update `privacy.html` with page-specific meta description, OG tags, Twitter tags
- [x] 4.4 Update `stats.html` with page-specific meta description, OG tags, Twitter tags

## 5. Structured Data (JSON-LD)
- [x] 5.1 Add WebApplication JSON-LD schema to `index.html`

## 6. Technical SEO Endpoints
- [x] 6.1 Add `/robots.txt` endpoint in `app/main.py`
- [x] 6.2 Add `/sitemap.xml` endpoint in `app/main.py`

## 7. Testing & Validation
- [x] 7.1 Test meta tags render correctly for both languages
- [x] 7.2 Validate robots.txt format
- [x] 7.3 Validate sitemap.xml format
- [ ] 7.4 Test OG tags with Facebook Sharing Debugger (manual)
- [ ] 7.5 Test Twitter Cards with Card Validator (manual)
- [x] 7.6 Run existing tests to ensure no regressions
