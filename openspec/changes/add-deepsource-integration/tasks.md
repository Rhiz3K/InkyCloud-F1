## 1. Configuration Files
- [ ] 1.1 Create `.deepsource.toml` with Python, Docker, Secrets, and Test Coverage analyzers
- [ ] 1.2 Configure Ruff code formatter for auto-fix in PRs
- [ ] 1.3 Set `max_line_length = 100` to match ruff config
- [ ] 1.4 Set `type_checker = "mypy"` for type checking
- [ ] 1.5 Set `cyclomatic_complexity_threshold = "high"`

## 2. CI/CD Integration
- [ ] 2.1 Add `pytest-cov` to dev dependencies in `pyproject.toml`
- [ ] 2.2 Update `ci.yml` checkout to use PR head commit (`ref: ${{ github.event.pull_request.head.sha }}`)
- [ ] 2.3 Add `id-token: write` permission for OIDC
- [ ] 2.4 Add pytest coverage step (`--cov=app --cov-report=xml`)
- [ ] 2.5 Add DeepSource CLI installation and report step with `--use-oidc`

## 3. External Setup (Manual)
- [ ] 3.1 Activate repository on DeepSource dashboard
- [ ] 3.2 Sync SCA dependency targets (Dependencies tab)
- [ ] 3.3 Verify first analysis run completes successfully

## 4. Validation
- [ ] 4.1 Run local tests with coverage: `pytest --cov=app --cov-report=xml`
- [ ] 4.2 Verify coverage.xml is generated correctly
- [ ] 4.3 Create test PR to verify DeepSource integration works
