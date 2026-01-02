## ADDED Requirements

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
