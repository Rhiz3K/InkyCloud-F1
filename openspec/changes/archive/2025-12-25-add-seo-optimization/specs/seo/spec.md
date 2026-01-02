## ADDED Requirements

### Requirement: Meta Tags per Page
The system SHALL include localized meta description tags on all HTML pages based on the UI language parameter.

#### Scenario: Homepage meta description
- **WHEN** a user visits the homepage with `lang=en`
- **THEN** the page includes `<meta name="description">` with English content describing the F1 E-Ink calendar service

#### Scenario: Homepage meta description Czech
- **WHEN** a user visits the homepage with `lang=cs`
- **THEN** the page includes `<meta name="description">` with Czech content describing the F1 E-Ink calendar service

#### Scenario: Canonical URL
- **WHEN** a user visits any HTML page
- **THEN** the page includes `<link rel="canonical">` pointing to the absolute URL of that page

### Requirement: Open Graph Tags
The system SHALL include Open Graph meta tags on all HTML pages for proper social media sharing on Facebook and LinkedIn.

#### Scenario: Homepage Open Graph tags
- **WHEN** a user shares the homepage URL on Facebook
- **THEN** Facebook displays the og:title, og:description, og:image (1200x630 PNG), og:url, og:type, and og:site_name

#### Scenario: OG image availability
- **WHEN** a social platform requests the OG image URL
- **THEN** the server returns a 1200x630 PNG image suitable for social sharing

### Requirement: Twitter Card Tags
The system SHALL include Twitter Card meta tags on all HTML pages for proper Twitter/X sharing.

#### Scenario: Homepage Twitter Card
- **WHEN** a user shares the homepage URL on Twitter/X
- **THEN** Twitter displays a summary_large_image card with twitter:title, twitter:description, and twitter:image

### Requirement: Robots.txt
The system SHALL serve a robots.txt file at the /robots.txt endpoint.

#### Scenario: Robots.txt content
- **WHEN** a crawler requests /robots.txt
- **THEN** the server returns a text/plain response allowing all crawlers and referencing the sitemap location

### Requirement: Sitemap.xml
The system SHALL serve a sitemap.xml file at the /sitemap.xml endpoint listing all public HTML pages.

#### Scenario: Sitemap content
- **WHEN** a crawler requests /sitemap.xml
- **THEN** the server returns an XML sitemap containing URLs for /, /privacy, /api/docs/html, and /stats with appropriate lastmod and changefreq attributes

### Requirement: Hreflang Alternate Links
The system SHALL include hreflang alternate link tags for supported language variants.

#### Scenario: English page hreflang
- **WHEN** a user visits any HTML page
- **THEN** the page includes `<link rel="alternate" hreflang="en">` and `<link rel="alternate" hreflang="cs">` pointing to the respective language versions

### Requirement: JSON-LD Structured Data
The system SHALL include JSON-LD structured data on the homepage for enhanced search engine understanding.

#### Scenario: WebApplication schema
- **WHEN** a search engine crawls the homepage
- **THEN** the page includes a JSON-LD script with WebApplication schema containing name, description, url, applicationCategory, and operatingSystem
