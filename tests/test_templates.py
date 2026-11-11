"""Tests for shared template helpers."""

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

from app.main import app
from app.web.templates import format_bytes

client = TestClient(app)


def _configure_soup(screen: str) -> BeautifulSoup:
    response = client.get(f"/configure/{screen}")
    assert response.status_code == 200
    return BeautifulSoup(response.text, "html.parser")


def test_configure_teams_renders_hidden_timezone_picker_once():
    soup = _configure_soup("teams")
    for wrapper_id in ("teamsTzDesktopWrapper", "teamsTzMobileWrapper"):
        wrappers = soup.find_all(id=wrapper_id)
        assert len(wrappers) == 1
        assert "teams-tz-wrapper" in wrappers[0]["class"]
        assert "hidden" in wrappers[0]["class"]
    assert len(soup.find_all(id="tz")) == 1


def test_configure_calendar_timezone_picker_is_unchanged():
    soup = _configure_soup("calendar")
    for element_id in ("tz", "tzList", "mobileTzContainer", "weatherContainer"):
        assert len(soup.find_all(id=element_id)) == 1, element_id
    # The page script mentions ".teams-tz-wrapper", so check markup rather than raw text.
    assert soup.select(".teams-tz-wrapper") == []
    assert soup.find(id="teamsTzDesktopWrapper") is None
    assert soup.find(id="teamsTzMobileWrapper") is None


@pytest.mark.parametrize("screen", ["calendar", "teams"])
def test_configure_timezone_picker_preserves_controls_and_handlers(screen):
    soup = _configure_soup(screen)
    for element_id in (
        "tz",
        "tzLabel",
        "tzLabelMobile",
        "tzSearch",
        "tzList",
        "tzSelectMobile",
        "desktopTzContainer",
        "mobileTzContainer",
    ):
        assert len(soup.find_all(id=element_id)) == 1, element_id
    assert soup.select_one("#tzLabel")["for"] == "tzSearch"
    assert soup.select_one("#tzLabelMobile")["for"] == "tzSelectMobile"
    assert soup.select_one("#tzSearch")["data-on-input"] == "onTzSearch"
    assert soup.select_one("#tzSelectMobile")["data-on-change"] == "selectTimezoneMobile"
    assert "lg:hidden" in soup.select_one("#mobileTzContainer")["class"]
    assert {"hidden", "lg:flex"} <= set(soup.select_one("#desktopTzContainer")["class"])
    assert soup.select("#desktopTzContainer #tzList")
    if screen == "calendar":
        assert soup.select("#desktopTzContainer #weatherContainer")
        assert soup.select("#desktopTzContainer #displayContainer")
    else:
        assert soup.select("#teamsTzDesktopWrapper #desktopTzContainer")
        assert soup.select("#teamsTzMobileWrapper #mobileTzContainer")


def test_format_bytes_uses_decimal_thresholds():
    assert format_bytes(999) == "999 B"
    assert format_bytes(1000) == "1.0 KB"
    assert format_bytes(1536) == "1.5 KB"
    assert format_bytes(1_000_000) == "1.00 MB"
    assert format_bytes(1_000_000_000) == "1.00 GB"
