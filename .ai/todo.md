# Issue 178
- [x] Add regression tests for time zone focus/selection and weather state.
- [x] Fix native time zone buttons, focus retention, weather semantics, URL label and heading.
- [x] Run JS checks and review diff.
- [ ] Verify browser controls and axe: blocked by missing Jean Run command and Chrome libglib-2.0.so.0.

## Review
- Eight new tests failed against the original implementation and pass with the fix.
- npm run test:js: 65 passed, 0 failed (temporary Node v24.22.2, checksum verified).
- npm run check:js: passed.
- git diff --check: passed.
- Required w-full/text-left utilities already exist in built CSS; focus style uses a nonce-protected template style block.
- Independent read-only review found no concrete correctness issues.
- API and cache behavior were not changed.
- No live environment URL was available. Browser keyboard/visual checks and axe remain unverified.
