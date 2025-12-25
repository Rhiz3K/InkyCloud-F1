"""Configuration management for F1 E-Ink calendar service."""

import logging
from functools import lru_cache
from typing import Optional, TypeVar

import pytz
from dotenv import load_dotenv
from pydantic import Field, HttpUrl, TypeAdapter, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


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
    APP_HOST: str = Field("0.0.0.0", description="Host address the app binds to")
    APP_PORT: int = Field(8000, gt=0, lt=65536, description="Port the app listens on")
    DEBUG: bool = Field(False, description="Enable debug logging")
    SITE_URL: str = Field(
        "https://f1.inkycloud.click",
        description="Base URL for the site (used in SEO meta tags, sitemap, etc.)",
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
    UMAMI_ENABLED: bool = Field(False, description="Toggle Umami analytics")
    ANALYTICS_HOSTNAME: str = Field("", description="Hostname for analytics tracking")

    # API settings
    JOLPICA_API_URL: str = Field(
        "https://api.jolpi.ca/ergast/f1/current/next.json",
        description="Jolpica F1 API endpoint",
    )
    REQUEST_TIMEOUT: int = Field(10, gt=0, description="HTTP request timeout in seconds")

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

    # Backup settings
    BACKUP_ENABLED: bool = Field(False, description="Toggle S3 database backup")
    BACKUP_CRON: str = Field("0 3 * * *", description="Cron expression for backup schedule")
    BACKUP_RETENTION_DAYS: int = Field(30, ge=0, description="Days to retain backups (0=disabled)")

    # S3 settings (for backup)
    S3_ENDPOINT_URL: Optional[str] = Field(default=None, description="S3-compatible endpoint URL")
    S3_ACCESS_KEY_ID: Optional[str] = Field(default=None, description="S3 access key ID")
    S3_SECRET_ACCESS_KEY: Optional[str] = Field(default=None, description="S3 secret access key")
    S3_BUCKET_NAME: Optional[str] = Field(default=None, description="S3 bucket name for backups")
    S3_REGION: str = Field("auto", description="S3 region (use 'auto' for Cloudflare R2)")

    @field_validator("APP_PORT", mode="before")
    @classmethod
    def validate_port(cls, value: object, info: ValidationInfo) -> int:
        if info.field_name is None:
            return 8000
        default: int = cls.model_fields[info.field_name].default
        try:
            port = int(value)  # type: ignore[arg-type]
            if 0 < port < 65536:
                return port
        except (TypeError, ValueError):
            pass
        return _warn_invalid(info.field_name, value, default, "must be a positive integer < 65536")

    @field_validator("REQUEST_TIMEOUT", mode="before")
    @classmethod
    def validate_timeout(cls, value: object, info: ValidationInfo) -> int:
        if info.field_name is None:
            return 10
        default: int = cls.model_fields[info.field_name].default
        try:
            timeout = int(value)  # type: ignore[arg-type]
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
        if isinstance(value, str) and value in pytz.all_timezones:
            return value
        return _warn_invalid(info.field_name, value, default, "unknown timezone")

    @field_validator("UMAMI_API_URL", "JOLPICA_API_URL", mode="before")
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
            return _warn_invalid(info.field_name, value, default, "must be a valid URL")

    @field_validator("BACKUP_RETENTION_DAYS", mode="before")
    @classmethod
    def validate_retention_days(cls, value: object, info: ValidationInfo) -> int:
        if info.field_name is None:
            return 30
        default: int = cls.model_fields[info.field_name].default
        try:
            days = int(value)  # type: ignore[arg-type]
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


@lru_cache()
def get_config() -> Config:
    """Load configuration once and reuse across the application."""
    return Config()  # type: ignore[call-arg]


def _reset_config_cache_for_tests() -> None:
    """Allow tests to rebuild configuration with fresh environment variables."""

    get_config.cache_clear()


config = get_config()
