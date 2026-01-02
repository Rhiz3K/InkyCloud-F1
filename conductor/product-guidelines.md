# Product Guidelines - F1 E-Ink Calendar

## Design Principles
- **1-bit Optimization:** All visual elements must be designed for strict black-and-white (1-bit) rendering. Avoid gradients, shadows, or anti-aliasing that may appear muddy on E-Ink displays.
- **High Contrast:** Use strong line weights and high-contrast typography to ensure readability in various lighting conditions.
- **Clean Layouts:** Maintain a clear information hierarchy. The most critical data (e.g., the countdown to the next session) should be the most prominent.

## Content & Voice
- **Functional Tone:** UI labels and session information should be concise and professional.
- **Clarity over Cleverness:** Use industry-standard terms (e.g., "FP1", "Qualifying", "Grand Prix") to ensure clarity for F1 fans.
- **Error Handling:** Provide helpful, actionable feedback for configuration errors without being overly technical.

## Localization (i18n)
- **JSON-based Translations:** All user-facing strings must be externalized in `translations/*.json` files to support community contributions.
- **Layout Robustness:** Design templates to handle variations in text length across supported languages (e.g., German translations are often longer than English).

## Technical Standards
- **Standardized Assets:** Circuit maps and flags must follow consistent sizing and naming conventions (e.g., ISO 3166-1 alpha-2 for flags).
- **Data Integrity:** All race session times must be stored in UTC and converted dynamically using IANA timezone identifiers.
- **Fallback Mechanisms:** Provide sensible defaults for missing assets or data to prevent layout breakage.
