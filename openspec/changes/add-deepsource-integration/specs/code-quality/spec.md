## ADDED Requirements

### Requirement: Static Code Analysis
The system SHALL run automated static code analysis on every push and pull request using DeepSource Python analyzer with mypy type checking enabled.

#### Scenario: PR triggers analysis
- **WHEN** a pull request is opened or updated
- **THEN** DeepSource SHALL analyze the code changes
- **AND** report issues as PR checks

#### Scenario: Type checking enabled
- **WHEN** Python code is analyzed
- **THEN** mypy type checker SHALL validate type annotations
- **AND** report type inconsistencies as issues

### Requirement: Secrets Detection
The system SHALL detect hardcoded secrets and credentials in non-test files using DeepSource Secrets analyzer.

#### Scenario: Secret detected in code
- **WHEN** a hardcoded API key, password, or token is found
- **THEN** DeepSource SHALL raise a critical security issue
- **AND** the PR check SHALL fail

### Requirement: Docker Best Practices
The system SHALL analyze Dockerfile for security and efficiency best practices using DeepSource Docker analyzer.

#### Scenario: Dockerfile analyzed
- **WHEN** Dockerfile is modified
- **THEN** DeepSource SHALL check for best practice violations
- **AND** report issues for untrusted base images or insecure patterns

### Requirement: Test Coverage Tracking
The system SHALL track and report test coverage metrics using DeepSource Test Coverage analyzer with OIDC authentication.

#### Scenario: Coverage reported via OIDC
- **WHEN** CI pipeline completes tests
- **THEN** pytest-cov SHALL generate coverage.xml
- **AND** DeepSource CLI SHALL report coverage using GitHub OIDC token
- **AND** coverage metrics SHALL be visible in DeepSource dashboard

### Requirement: Dependency Vulnerability Scanning
The system SHALL scan project dependencies for known vulnerabilities using DeepSource SCA.

#### Scenario: Vulnerable dependency detected
- **WHEN** a dependency with known CVE is found in pyproject.toml
- **THEN** DeepSource SHALL report the vulnerability with severity
- **AND** suggest remediation if available

### Requirement: Code Formatting Autofix
The system SHALL offer automatic code formatting fixes using ruff formatter in DeepSource.

#### Scenario: Formatting issue detected
- **WHEN** code does not conform to ruff formatting rules
- **THEN** DeepSource MAY create an autofix PR
- **AND** the fix SHALL use project's ruff configuration (100 char line length)
