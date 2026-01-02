## ADDED Requirements

### Requirement: Type-Safe Configuration Validators
The configuration module SHALL pass static type analysis (Pyright strict mode) 
without errors while maintaining runtime behavior.

#### Scenario: Field name validation guard
- **WHEN** a field validator receives `ValidationInfo` with `field_name=None`
- **THEN** the validator SHALL return the input value unchanged or a safe default

#### Scenario: Type-preserving warning helper
- **WHEN** `_warn_invalid()` is called with a default value of type `T`
- **THEN** the function SHALL return a value of the same type `T`

#### Scenario: URL configuration fields
- **WHEN** URL configuration fields are defined
- **THEN** they SHALL use `str` type with validation to ensure httpx compatibility

### Requirement: Consistent HttpUrl Handling
All URL configuration values SHALL be stored as `str` type and validated 
for URL format. Code using these URLs with httpx SHALL NOT require explicit 
`str()` conversion.

#### Scenario: API URL usage
- **WHEN** `config.JOLPICA_API_URL` is passed to httpx client
- **THEN** it SHALL work directly without `str()` wrapper

#### Scenario: Analytics URL usage
- **WHEN** `config.UMAMI_API_URL` is passed to httpx client
- **THEN** it SHALL work directly without `str()` wrapper
