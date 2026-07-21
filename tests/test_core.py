"""Coverage for defensive configuration, model, dependency, and state edges."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app import config as config_module
from app import state
from app.models import DriverInfo, F1Response
from app.routes import deps
from app.services.f1_service import F1Service


def _info(field_name: str | None):
    return SimpleNamespace(field_name=field_name)


def test_config_validators_cover_contextless_and_unparseable_inputs():
    config = config_module.Config
    assert config.validate_port("bad", _info(None)) == 8000
    assert config.validate_port(object(), _info("APP_PORT")) == 8000
    assert config.validate_positive_int("bad", _info(None)) == 10
    assert config.validate_positive_int(object(), _info("REQUEST_TIMEOUT")) == 10
    assert config.validate_sample_rate("bad", _info(None)) == 0.1
    assert config.validate_sample_rate(object(), _info("SENTRY_TRACES_SAMPLE_RATE")) == 0.1
    assert config.validate_timezone("UTC", _info(None)) == "Europe/Prague"
    assert config.validate_default_lang("cs", _info(None)) == "en"
    assert config.validate_url("https://example.test", _info(None)) == "https://example.com"
    assert config.validate_retention_days("bad", _info(None)) == 30
    assert config.validate_retention_days(object(), _info("BACKUP_RETENTION_DAYS")) == 30
    assert config.validate_s3_endpoint("https://s3.example", _info(None)) is None


def test_optional_s3_endpoint_and_admin_token_validation():
    config = config_module.Config
    assert config.validate_s3_endpoint("https://s3.example", _info("S3_ENDPOINT_URL")) == (
        "https://s3.example/"
    )
    assert config.validate_s3_endpoint("not a url", _info("S3_ENDPOINT_URL")) is None
    token = config.validate_admin_api_token("  secret  ")
    assert isinstance(token, SecretStr)
    assert token.get_secret_value() == "secret"


def test_f1_response_race_property_covers_valid_empty_and_invalid_payloads():
    valid = {
        "season": "2026",
        "round": "1",
        "raceName": "Test GP",
        "Circuit": {
            "circuitId": "test",
            "circuitName": "Test Circuit",
            "Location": {"locality": "City", "country": "Country"},
        },
        "date": "2026-07-12",
    }
    assert F1Response(MRData={"RaceTable": {"Races": [valid]}}).race.raceName == "Test GP"
    assert F1Response(MRData={}).race is None
    assert F1Response(MRData={"RaceTable": {"Races": [{"raceName": "invalid"}]}}).race is None
    assert DriverInfo(code="DRV", given_name="Test", family_name="Driver").display_name == "Driver"


def test_f1_dependency_rejects_invalid_timezone_and_builds_service():
    with (
        patch("app.routes.deps.is_valid_timezone", return_value=False),
        pytest.raises(HTTPException) as error,
    ):
        deps.get_f1_service("Bad/Zone")
    assert error.value.status_code == 400

    with (
        patch("app.routes.deps.is_valid_timezone", return_value=True),
        patch("app.routes.deps.F1Service", spec=F1Service) as service,
    ):
        assert deps.get_f1_service("UTC") is service.return_value
    service.assert_called_once_with(timezone_name="UTC")


def test_requeue_api_calls_handles_empty_and_preserves_chronological_order():
    state._api_calls_buffer.clear()
    state.requeue_api_calls([])
    assert list(state._api_calls_buffer) == []

    state._api_calls_buffer.append({"id": "new"})
    state.requeue_api_calls([{"id": "old"}])
    assert list(state._api_calls_buffer) == [{"id": "old"}, {"id": "new"}]
