## 1. Refactor _warn_invalid helper

- [x] 1.1 Add `TypeVar` import from typing
- [x] 1.2 Define `T = TypeVar("T")` for generic return type
- [x] 1.3 Update `_warn_invalid` signature to use `T` for default and return type

## 2. Fix field validators

- [x] 2.1 Add guard clause `if info.field_name is None` to `validate_port`
- [x] 2.2 Add guard clause to `validate_timeout`
- [x] 2.3 Add guard clause to `validate_sample_rate`
- [x] 2.4 Add guard clause to `validate_timezone`
- [x] 2.5 Add guard clause to `validate_url`

## 3. Fix HttpUrl type compatibility

- [x] 3.1 Change `UMAMI_API_URL` type from `HttpUrl` to `str`
- [x] 3.2 Change `JOLPICA_API_URL` type from `HttpUrl` to `str`
- [x] 3.3 Keep `validate_url` validator (validates format, returns `str`)
- [x] 3.4 No change needed in `f1_service.py` (type is now `str`, compatible with httpx)

## 4. Validation

- [x] 4.1 Run `ruff check . && ruff format .`
- [x] 4.2 Run `pytest tests/test_config.py -v`
- [x] 4.3 Run `pytest` (full test suite)
- [x] 4.4 Verify type errors reduced in `app/config.py`
