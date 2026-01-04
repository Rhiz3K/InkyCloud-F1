# Change: Add DeepSource Code Quality Integration

## Why
Projekt postrádá automatizovanou statickou analýzu kódu, detekci secrets, vulnerability scanning závislostí a tracking test coverage. DeepSource poskytuje všechny tyto funkce v jedné platformě s GitHub integrací.

## What Changes
- **NEW** `.deepsource.toml` - konfigurace analyzérů (Python, Docker, Secrets, Test Coverage)
- **MODIFIED** `.github/workflows/ci.yml` - přidání coverage reportingu s OIDC autentizací
- **MODIFIED** `pyproject.toml` - přidání `pytest-cov` závislosti
- Aktivace Mypy type checkeru
- Cyclomatic complexity threshold: HIGH (16-25)
- Ruff code formatter autofix v PR

## Impact
- Affected specs: NEW `code-quality` capability
- Affected code: `.github/workflows/ci.yml`, `pyproject.toml`
- New files: `.deepsource.toml`
- External: Vyžaduje aktivaci repo na DeepSource dashboard + SCA sync
