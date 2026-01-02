# configuration Specification

## Purpose
TBD - created by archiving change fix-config-type-annotations. Update Purpose after archive.
## Requirements
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

### Requirement: Backup Configuration Settings

The configuration module SHALL support environment variables for S3 backup functionality.

#### Scenario: Backup toggle configuration

- **WHEN** `BACKUP_ENABLED` environment variable is set
- **THEN** the config SHALL expose `BACKUP_ENABLED` as a boolean (default: `false`)

#### Scenario: Backup schedule configuration

- **WHEN** `BACKUP_CRON` environment variable is set
- **THEN** the config SHALL expose `BACKUP_CRON` as a string cron expression (default: `"0 3 * * *"`)

#### Scenario: Backup retention configuration

- **WHEN** `BACKUP_RETENTION_DAYS` environment variable is set
- **THEN** the config SHALL expose `BACKUP_RETENTION_DAYS` as a non-negative integer (default: `30`)

### Requirement: S3 Configuration Settings

The configuration module SHALL support environment variables for S3-compatible storage.

#### Scenario: S3 endpoint configuration

- **WHEN** `S3_ENDPOINT_URL` environment variable is set
- **THEN** the config SHALL expose `S3_ENDPOINT_URL` as an optional string URL

#### Scenario: S3 credentials configuration

- **WHEN** `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY` are set
- **THEN** the config SHALL expose these as optional strings for boto3 authentication

#### Scenario: S3 bucket configuration

- **WHEN** `S3_BUCKET_NAME` environment variable is set
- **THEN** the config SHALL expose `S3_BUCKET_NAME` as an optional string

#### Scenario: S3 region configuration

- **WHEN** `S3_REGION` environment variable is set
- **THEN** the config SHALL expose `S3_REGION` as a string (default: `"auto"`)

