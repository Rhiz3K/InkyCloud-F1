## Context
Projekt používá ruff pro linting/formatting, pytest pro testy, a Sentry pro error tracking. Chybí:
- Statická analýza bezpečnostních issues
- Vulnerability scanning závislostí (SCA)
- Test coverage tracking a metriky
- Detekce hardcoded secrets

DeepSource poskytuje tyto funkce zdarma pro open-source projekty.

## Goals / Non-Goals
**Goals:**
- Automatická analýza každého PR a push
- Coverage tracking s historií
- Vulnerability alerting pro závislosti
- Secrets detection v kódu

**Non-Goals:**
- Nahrazení ruff (DeepSource doplňuje, nenahrazuje)
- Self-hosted DeepSource instance (používáme cloud)
- Breaking existing CI (musí zůstat zpětně kompatibilní)

## Decisions

### Decision 1: OIDC autentizace místo DSN
- **What**: Použití GitHub OIDC tokenu pro autentizaci místo DEEPSOURCE_DSN secret
- **Why**: Bezpečnější (no long-lived secrets), jednodušší správa, native GitHub Actions support
- **Trade-off**: Vyžaduje `id-token: write` permission

### Decision 2: Mypy type checker enabled
- **What**: Aktivace `type_checker = "mypy"` v Python analyzer
- **Why**: Projekt používá type hints, mypy odhalí type inconsistencies
- **Trade-off**: Může odhalit existující type issues (initial noise)

### Decision 3: High complexity threshold
- **What**: `cyclomatic_complexity_threshold = "high"` (16-25)
- **Why**: Projekt má monolitické soubory (main.py 1697 lines, renderer.py 1562 lines)
- **Trade-off**: Medium by generovalo příliš mnoho false positives

### Decision 4: Exclude patterns
- **What**: Vyloučení `app/assets/**`, `.venv/**`, `__pycache__/**`
- **Why**: Assets obsahují JSON data a binární soubory, ne kód k analýze

## Risks / Trade-offs
- **Initial issues flood**: První analýza může odhalit stovky issues → řešit postupně
- **Mypy strictness**: Může vyžadovat doplnění type hints → dokumentovat v AGENTS.md
- **CI time increase**: Coverage + DeepSource report přidá ~30s → akceptovatelné

## Migration Plan
1. Merge konfigurace (`.deepsource.toml`, `ci.yml`, `pyproject.toml`)
2. Aktivovat repo na DeepSource dashboard
3. Sync SCA targets
4. Review initial analysis results
5. Triage a resolve critical/high issues
6. Update AGENTS.md s DeepSource workflow

## Open Questions
- Žádné (všechny rozhodnuty uživatelem)
