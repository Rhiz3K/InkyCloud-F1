# Change: Fix type annotations in config.py

## Why
Static type checkers (Pyright/Pylance) report ~20 type errors in `app/config.py`. 
These errors degrade IDE support (no autocomplete, false warnings), obscure real 
issues, and violate Python typing best practices. The code works at runtime but 
fails static analysis.

## What Changes
- Add `TypeVar` to `_warn_invalid()` helper to preserve return types
- Add guard clauses for `ValidationInfo.field_name` which can be `None`
- Add explicit type casts in validator return statements
- Convert `HttpUrl` fields to `str` type (simpler, matches actual usage pattern)
- Fix `f1_service.py` to use `str()` cast for API URL (aligns with documented convention)

## Impact
- Affected specs: configuration (new capability)
- Affected code: `app/config.py`, `app/services/f1_service.py`
- Risk: Low - primarily type annotation changes, minimal behavioral change
- Breaking changes: None - external API unchanged
