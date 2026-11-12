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


@pytest.mark.parametrize("screen", ["calendar", "teams"])
def test_configure_has_single_h1_and_named_api_url(screen):
    soup = _configure_soup(screen)
    assert len(soup.find_all("h1")) == 1
    label = soup.find("label", attrs={"for": "apiUrl"})
    assert label is not None and label.get_text(strip=True) == "URL"
    assert soup.select_one("#apiUrl")["readonly"] is not None


@pytest.mark.parametrize("screen", ["calendar", "teams"])
def test_configure_option_buttons_expose_group_name_and_pressed_state(screen):
    soup = _configure_soup(screen)
    groups = {"displayTypeContainer": "displayLabel"}
    if screen == "calendar":
        groups["weatherTypeContainer"] = "weatherLabel"
    for group_id, label_id in groups.items():
        group = soup.select_one(f"#{group_id}")
        assert group["role"] == "group"
        assert group["aria-labelledby"] == label_id
        assert soup.select_one(f"#{label_id}").name == "span"
        pressed = [button["aria-pressed"] for button in group.find_all("button")]
        assert pressed.count("true") == 1, group_id
        assert set(pressed) == {"true", "false"}
    for heading_id in ("displayLabelMobile",) + (
        ("weatherLabelMobile",) if screen == "calendar" else ()
    ):
        heading = soup.select_one(f"#{heading_id}")
        assert heading.name == "span"
        group = soup.select_one(f'[aria-labelledby="{heading_id}"]')
        assert group["role"] == "group"
        assert len(group.find_all("button", attrs={"aria-pressed": "true"})) == 1
    # Orphan <label> elements (no control) confuse assistive technology.
    for label in soup.find_all("label"):
        assert label.get("for"), label
    filters = soup.select(".tz-filter-btn")
    assert len(filters) == 8
    assert all(button["type"] == "button" for button in filters)
    assert [button["aria-pressed"] for button in filters].count("true") == 1
    assert soup.select_one("#tzList")["aria-labelledby"] == "tzLabel"


def test_configure_calendar_hidden_weather_inputs_have_names():
    soup = _configure_soup("calendar")
    assert soup.select_one("#weatherToggle")["aria-labelledby"] == "weatherLabel"
    radios = soup.select('input[name="weatherType"]')
    assert len(radios) == 2
    for radio in radios:
        assert radio.get("id")
        assert soup.select_one(f"#{radio['aria-labelledby']}") is not None


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/configure/calendar",
        "/configure/teams",
        "/changelog",
        "/api/docs/html",
        "/credits",
        "/privacy",
        "/stats",
        "/missing-skip-link-page",
    ],
)
def test_pages_have_skip_link_to_main(path):
    response = client.get(path)
    soup = BeautifulSoup(response.text, "html.parser")
    skip = soup.body.find("a")
    assert skip["href"] == "#main-content"
    assert "sr-only" in skip["class"]
    assert skip.get_text(strip=True) == "Skip to content"
    main = soup.find("main")
    assert main["id"] == "main-content"
    assert main["tabindex"] == "-1"
    assert len(soup.find_all("h1")) == 1


def test_skip_link_is_translated():
    soup = BeautifulSoup(client.get("/cs/").text, "html.parser")
    assert soup.body.find("a").get_text(strip=True) == "Přeskočit na obsah"


@pytest.mark.parametrize("path", ["/changelog", "/api/docs/html"])
def test_page_headings_do_not_skip_levels(path):
    soup = BeautifulSoup(client.get(path).text, "html.parser")
    levels = [int(tag.name[1]) for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])]
    assert levels[0] == 1
    assert levels.count(1) == 1
    for previous, current in zip(levels, levels[1:], strict=False):
        assert current <= previous + 1, levels


def test_format_bytes_uses_decimal_thresholds():
    assert format_bytes(999) == "999 B"
    assert format_bytes(1000) == "1.0 KB"
    assert format_bytes(1536) == "1.5 KB"
    assert format_bytes(1_000_000) == "1.00 MB"
    assert format_bytes(1_000_000_000) == "1.00 GB"
