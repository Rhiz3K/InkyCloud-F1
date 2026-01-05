# Quick Spec: Weather Not Displaying on Production

## Overview
Weather functionality is not displaying on the production server (https://f1.inkycloud.click/). The issue has been identified and a fix already exists in branch `fix/add-missing-weather-enabled-config`. This spec covers merging the fix into main and deploying to production.

## Workflow Type
**simple** - This is a straightforward merge and deploy task. The fix is already implemented and tested.

## Task Scope
Merge existing weather config fix into main branch and deploy to production.

### Root Cause (Already Identified)
The `main` branch is missing `WEATHER_ENABLED` and `WEATHER_CACHE_MINUTES` config attributes that are referenced in `app/main.py` (lines 1245, 1398, 1498). This causes an `AttributeError` when weather is requested.

**The fix already exists** in branch `fix/add-missing-weather-enabled-config` (commit `3c80d5d`).

### Files Modified (in existing fix)
- `app/config.py` - Added missing config attributes:
  - `WEATHER_ENABLED: bool = Field(True)`
  - `WEATHER_CACHE_MINUTES: int = Field(60)`

### Action Required
1. Merge branch `fix/add-missing-weather-enabled-config` into `main`
2. Deploy to production

## Success Criteria
- [ ] Weather displays on https://f1.inkycloud.click/calendar.bmp?weather=race
- [ ] No `AttributeError` in production logs
- [ ] Dev and production behavior match

## Notes
- Open-Meteo API is free and requires no API key
- Weather works on dev because the fix branch is checked out locally
- Production runs from `main` which lacks the config attributes
