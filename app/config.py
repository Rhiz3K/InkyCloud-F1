"""Configuration management for F1 E-Ink calendar service."""

import logging
from functools import lru_cache
from typing import Optional, TypeVar

from dotenv import load_dotenv
from pydantic import (
    AliasChoices,
    Field,
    HttpUrl,
    SecretStr,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.utils.timezones import is_valid_timezone

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

# Ordered language codes used across routing, UI, and API docs.
LANGUAGE_CODES: tuple[str, ...] = (
    "cs",
    "de",
    "en",
    "es",
    "fr",
    "it",
    "ja",
    "nl",
    "pl",
    "pt-BR",
    "sk",
    "tr",
    "zh-CN",
)

# Valid language codes (allowlist for security - prevents path injection)
# Defined as module-level constant for easy import across the application
VALID_LANGUAGES: frozenset[str] = frozenset(LANGUAGE_CODES)


T = TypeVar("T")


def _warn_invalid(field_name: str, raw_value: object, default: T, reason: str) -> T:
    """Log a user-friendly message and return the safe default."""
    logger.warning(
        "Invalid value for %s=%r; %s. Falling back to %r.",
        field_name,
        raw_value,
        reason,
        default,
    )
    return default


class Config(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # Application settings
    APP_HOST: str = Field(
        # skipcq: BAN-B104 - required to serve Docker/self-hosted deployments;
        # bound explicitly by env/network config
        "0.0.0.0",
        description="Host address the app binds to",
    )
    APP_PORT: int = Field(
        8000,
        gt=0,
        lt=65536,
        validation_alias=AliasChoices("APP_PORT", "PORT"),
        description="Port the app listens on",
    )
    DEBUG: bool = Field(False, description="Enable debug logging")
    SITE_URL: str = Field(
        "https://f1.inkycloud.click",
        description="Base URL for the site (used in SEO meta tags, sitemap, etc.)",
    )
    FORWARDED_ALLOW_IPS: str = Field(
        "127.0.0.1",
        description="Trusted reverse proxy IPs/CIDRs for forwarded headers",
    )
    SKIP_PERSISTENCE_CHECK: bool = Field(
        False,
        description="Skip the persistent volume safety check on startup",
    )

    # Sentry/GlitchTip settings
    SENTRY_DSN: Optional[str] = Field(default=None, description="Sentry DSN")
    SENTRY_ENVIRONMENT: str = Field("production", description="Sentry environment name")
    SENTRY_TRACES_SAMPLE_RATE: float = Field(0.1, ge=0.0, le=1.0, description="Tracing sample rate")

    # Umami Analytics settings
    UMAMI_WEBSITE_ID: Optional[str] = Field(default=None, description="Umami website identifier")
    UMAMI_API_URL: str = Field(
        "https://analytics.example.com/api/send",
        description="Umami analytics endpoint",
    )
    GITHUB_API_BASE_URL: str = Field(
        "https://api.github.com",
        description="GitHub REST API base URL",
    )
    UMAMI_ENABLED: bool = Field(False, description="Toggle Umami analytics")
    ANALYTICS_HOSTNAME: str = Field("", description="Hostname for analytics tracking")

    # API settings
    JOLPICA_API_URL: str = Field(
        "https://api.jolpi.ca/ergast/f1/current/next.json",
        description="Jolpica F1 API endpoint",
    )
    REQUEST_TIMEOUT: int = Field(10, gt=0, description="HTTP request timeout in seconds")

    RATE_LIMIT_ENABLED: bool = Field(True, description="Enable lightweight in-memory rate limiting")
    IMAGE_RATE_LIMIT_PER_MINUTE: int = Field(
        120, gt=0, description="Per-IP BMP requests allowed per minute"
    )
    PERF_METRICS_RATE_LIMIT_PER_MINUTE: int = Field(
        240, gt=0, description="Per-IP perf metrics posts allowed per minute"
    )
    DATA_API_RATE_LIMIT_PER_MINUTE: int = Field(
        120, gt=0, description="Per-IP live F1 data API requests allowed per minute"
    )
    STATS_RATE_LIMIT_PER_MINUTE: int = Field(
        60, gt=0, description="Per-IP statistics reads allowed per minute"
    )

    ADMIN_API_TOKEN: Optional[SecretStr] = Field(
        default=None,
        description="Optional bearer token required for read-only operational API endpoints",
    )

    # Internationalization
    DEFAULT_LANG: str = Field("en", description="Default language code")
    DEFAULT_TIMEZONE: str = Field("Europe/Prague", description="Default timezone")

    # Display settings
    DISPLAY_WIDTH: int = Field(800, frozen=True)
    DISPLAY_HEIGHT: int = Field(480, frozen=True)

    # Database settings
    # Default paths are optimized for Docker containers (/app is WORKDIR in Dockerfile)
    # For local development, override with environment variables:
    #   DATABASE_PATH=./data/f1.db
    #   IMAGES_PATH=./data/images
    DATABASE_PATH: str = Field("/app/data/f1.db", description="SQLite database path")
    IMAGES_PATH: str = Field("/app/data/images", description="Directory for cached images")

    # Scheduler settings
    SCHEDULER_ENABLED: bool = Field(True, description="Toggle background scheduler")
    STATS_RETENTION_DAYS: int = Field(
        90,
        ge=0,
        description=(
            "Days to retain API/request/performance statistics. Default 90 bounds database and "
            "backup growth; set 0 only to explicitly retain history forever"
        ),
    )

    # Weather integration
    WEATHER_ENABLED: bool = Field(True, description="Toggle weather forecast display")
    WEATHER_CACHE_MINUTES: int = Field(
        60, gt=0, description="Weather data cache duration in minutes"
    )
    OPEN_METEO_URL: str = Field(
        "https://api.open-meteo.com/v1/forecast",
        description="Open-Meteo forecast API endpoint",
    )
    OPEN_METEO_ARCHIVE_URL: str = Field(
        "https://archive-api.open-meteo.com/v1/archive",
        description="Open-Meteo archive API endpoint",
    )

    # Backup settings
    BACKUP_ENABLED: bool = Field(False, description="Toggle S3 database backup")
    BACKUP_CRON: str = Field("0 3 * * *", description="Cron expression for backup schedule")
    BACKUP_RETENTION_DAYS: int = Field(30, ge=0, description="Days to retain backups (0=disabled)")

    # S3 settings (for backup)
    S3_ENDPOINT_URL: Optional[str] = Field(default=None, description="S3-compatible endpoint URL")
    S3_ACCESS_KEY_ID: Optional[SecretStr] = Field(default=None, description="S3 access key ID")
    S3_SECRET_ACCESS_KEY: Optional[SecretStr] = Field(
        default=None, description="S3 secret access key"
    )
    S3_BUCKET_NAME: Optional[str] = Field(default=None, description="S3 bucket name for backups")
    S3_REGION: str = Field("auto", description="S3 region (use 'auto' for Cloudflare R2)")

    @field_validator("APP_PORT", mode="before")
    @classmethod
    def validate_port(cls, value: object, info: ValidationInfo) -> int:
        """
        Validate and normalize a port value from configuration.

        If `info.field_name` is None, returns 8000. Otherwise, attempts to parse
        `value` as an integer in range 1-65535. On success returns the parsed
        port; on failure logs a warning and returns the field's default.

        Parameters:
            cls: The Config class (used to access the field default).
            value: The raw value to validate (typically from environment).
            info: Validator context; `info.field_name` selects the field default.

        Returns:
            int: The validated port number, or the field default if invalid.
        """
        if info.field_name is None:
            return 8000
        default: int = cls.model_fields[info.field_name].default
        try:
            port = int(value)  # type: ignore[call-overload]
            if 0 < port < 65536:
                return port
        except (TypeError, ValueError):
            pass
        return _warn_invalid(info.field_name, value, default, "must be a positive integer < 65536")

    @field_validator(
        "REQUEST_TIMEOUT",
        "IMAGE_RATE_LIMIT_PER_MINUTE",
        "PERF_METRICS_RATE_LIMIT_PER_MINUTE",
        "DATA_API_RATE_LIMIT_PER_MINUTE",
        "STATS_RATE_LIMIT_PER_MINUTE",
        mode="before",
    )
    @classmethod
    def validate_timeout(cls, value: object, info: ValidationInfo) -> int:
        """
        Validate and coerce a configured request timeout into a positive integer.

        If the validator is invoked without a field name, returns 10. If `value`
        can be converted to an integer > 0, that integer is returned; otherwise
        the configured field default is returned after logging a warning.

        Parameters:
            value: The raw value to validate (may be any type).
            info: Validation metadata; if `info.field_name` is None, uses 10.

        Returns:
            An integer timeout in seconds (positive integer or field default).
        """
        if info.field_name is None:
            return 10
        default: int = cls.model_fields[info.field_name].default
        try:
            timeout = int(value)  # type: ignore[call-overload]
            if timeout > 0:
                return timeout
        except (TypeError, ValueError):
            pass
        return _warn_invalid(info.field_name, value, default, "must be a positive integer")

    @field_validator("SENTRY_TRACES_SAMPLE_RATE", mode="before")
    @classmethod
    def validate_sample_rate(cls, value: object, info: ValidationInfo) -> float:
        if info.field_name is None:
            return 0.1
        default: float = cls.model_fields[info.field_name].default
        try:
            rate = float(value)  # type: ignore[arg-type]
            if 0.0 <= rate <= 1.0:
                return rate
        except (TypeError, ValueError):
            pass
        return _warn_invalid(info.field_name, value, default, "must be between 0.0 and 1.0")

    @field_validator("DEFAULT_TIMEZONE", mode="before")
    @classmethod
    def validate_timezone(cls, value: object, info: ValidationInfo) -> str:
        if info.field_name is None:
            return "Europe/Prague"
        default: str = cls.model_fields[info.field_name].default
        if isinstance(value, str) and is_valid_timezone(value):
            return value
        return _warn_invalid(info.field_name, value, default, "unknown timezone")

    @field_validator("DEFAULT_LANG", mode="before")
    @classmethod
    def validate_default_lang(cls, value: object, info: ValidationInfo) -> str:
        if info.field_name is None:
            return "en"
        default: str = cls.model_fields[info.field_name].default
        if isinstance(value, str) and value in LANGUAGE_CODES:
            return value
        return _warn_invalid(info.field_name, value, default, "unsupported language code")

    @field_validator(
        "SITE_URL",
        "UMAMI_API_URL",
        "GITHUB_API_BASE_URL",
        "JOLPICA_API_URL",
        "OPEN_METEO_URL",
        "OPEN_METEO_ARCHIVE_URL",
        mode="before",
    )
    @classmethod
    def validate_url(cls, value: object, info: ValidationInfo) -> str:
        if info.field_name is None:
            return "https://example.com"
        default: str = cls.model_fields[info.field_name].default
        adapter = TypeAdapter(HttpUrl)
        try:
            validated = adapter.validate_python(value)
            return str(validated)
        except ValidationError:
            if info.field_name == "SITE_URL":
                default = "http://localhost:8000"
            return _warn_invalid(info.field_name, value, default, "must be a valid URL")

    @field_validator("BACKUP_RETENTION_DAYS", "STATS_RETENTION_DAYS", mode="before")
    @classmethod
    def validate_retention_days(cls, value: object, info: ValidationInfo) -> int:
        """
        Validate and coerce a retention-days setting to a non-negative integer.

        Parameters:
            value: Raw input to validate and convert to an integer.
            info: Validator context for field name and default. Falls back to 30.

        Returns:
            int: Parsed integer >= 0, or field default after logging a warning.
        """
        if info.field_name is None:
            return 30
        default: int = cls.model_fields[info.field_name].default
        try:
            days = int(value)  # type: ignore[call-overload]
            if days >= 0:
                return days
        except (TypeError, ValueError):
            pass
        return _warn_invalid(info.field_name, value, default, "must be a non-negative integer")

    @field_validator("S3_ENDPOINT_URL", mode="before")
    @classmethod
    def validate_s3_endpoint(cls, value: object, info: ValidationInfo) -> Optional[str]:
        if info.field_name is None:
            return None
        if value is None or value == "":
            return None
        adapter = TypeAdapter(HttpUrl)
        try:
            validated = adapter.validate_python(value)
            return str(validated)
        except ValidationError:
            logger.warning(
                "Invalid value for %s=%r; must be a valid URL. Backup will be disabled.",
                info.field_name,
                value,
            )
            return None

    @field_validator("ADMIN_API_TOKEN", mode="before")
    @classmethod
    def validate_admin_api_token(cls, value: object) -> SecretStr | None:
        """Treat an empty optional token as disabled; normalize configured secrets."""
        if value is None:
            return None
        raw_value = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        if not raw_value.strip():
            return None
        return SecretStr(raw_value.strip())

    @field_validator("DATABASE_PATH", "IMAGES_PATH", mode="before")
    @classmethod
    def validate_storage_path(cls, value: object, info: ValidationInfo) -> str:
        """Reject paths that SQLite/pathlib would reinterpret as the current directory."""
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{info.field_name} must be a non-empty path")
        return value.strip()

    @field_validator("DISPLAY_WIDTH", "DISPLAY_HEIGHT", mode="before")
    @classmethod
    def validate_fixed_display_dimensions(cls, value: object, info: ValidationInfo) -> int:
        """Keep the hardware canvas fixed even when matching environment variables exist."""
        expected = 800 if info.field_name == "DISPLAY_WIDTH" else 480
        try:
            parsed = int(value)  # type: ignore[call-overload]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{info.field_name} must be {expected}") from exc
        if parsed != expected:
            raise ValueError(f"{info.field_name} must be {expected}")
        return expected


@lru_cache()
def get_config() -> Config:
    """Load configuration once and reuse across the application."""
    return Config()  # type: ignore[call-arg]


def _reset_config_cache_for_tests() -> None:
    """Allow tests to rebuild configuration with fresh environment variables."""

    get_config.cache_clear()
    globals()["config"] = get_config()


config = get_config()
