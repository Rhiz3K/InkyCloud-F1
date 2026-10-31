"""Offline tests for the manual circuit fact refresh."""

import json
from datetime import date
from urllib.parse import urlsplit

import httpx
import pytest

from scripts import fetch_circuits as fc

TEXT = """{{Infobox motorsport venue
|length_km=<!-- source -->5.278
|record_time=1:20.901<br />({{cvt|123|km}})
|record_driver=[[Charles Leclerc]]
|record_year=2024
|record_class=Formula One
}}"""


def wiki(url):
    """Return an API-shaped revision without relying on a returned title."""
    return {
        "query": {"pages": [{"revisions": [{"revid": 42, "slots": {"main": {"content": TEXT}}}]}]}
    }


def jolpica(url):
    """Return a first season or recent winner distances."""
    races = (
        [{"season": "1950"}]
        if "limit=1" in url
        else [{"Results": [{"laps": str(n)}]} for n in [70, 70, 70, 70, 68]]
    )
    return {"MRData": {"total": "10", "RaceTable": {"Races": races}}}


def empty():
    """Return a complete empty dataset."""
    return {cid: {} for cid, _, _ in fc.CIRCUITS}


def test_clean_and_parse():
    """Handle comments, refs, templates, links, tags and entities."""
    assert (
        fc.clean_wikitext(
            "<!-- x -->A<ref name='x'>bad{{x}}</ref><ref name='y'/>"
            "{{a|{{b}}}}[[target|B]] [[C]] [[File:x|bad]] [[Category:y]]"
            "[https://x label]<b> D</b> &amp; ''E''"
        )
        == "AB C D & E"
    )
    params = fc.parse_infobox(TEXT)
    assert params["length_km"].startswith("<!--")
    facts = fc.extract_wikipedia_facts(TEXT, 2026, "monza")
    assert facts == dict(zip(fc.FIELDS, ("5.278km", "1:20.901", "Charles Leclerc", "2024")))
    assert fc.parse_infobox("{{Infobox_motorsport venue|record_driver=[[X|Y]]}}") == {
        "record_driver": "[[X|Y]]"
    }
    assert fc.extract_wikipedia_facts("", 2026, "monza") == {}
    assert "fastest_lap_time" not in fc.extract_wikipedia_facts(
        TEXT.replace("Formula One", "Sports cars"), 2026, "monza"
    )


@pytest.mark.parametrize(
    "validator,good,bad",
    [
        (fc.valid_length, ["1.000km", "10.000km"], ["0.999km", "10.001km", "5km", None]),
        (
            fc.valid_time,
            ["0:50.000", "3:00.000"],
            ["0:49.999", "3:00.001", "1:60.000", "12:00.000", None],
        ),
        (
            fc.valid_driver,
            ["Álex Albon", "O’Neil", "A " * 22 + "B" * 20],
            ["A" * 21, "A " * 23 + "B" * 20, "A\nB", "A×B", "A÷B", "AЖ", "", None],
        ),
        (fc.valid_laps, ["1", "100", "070"], ["0", "101", "1000", "١", 70, None]),
    ],
)
def test_validators(validator, good, bad):
    """Exercise inclusive bounds and rejected syntax."""
    assert all(validator(v) for v in good)
    assert not any(validator(v) for v in bad)


def test_year_and_mode():
    """Bound years and prefer planned distance over shortened races."""
    assert fc.valid_year("1950", 2026) and fc.valid_year("2026", 2026)
    assert not any(fc.valid_year(v, 2026) for v in ["1949", "2027", 2024, "202", None])
    assert fc.planned_laps(["70"] * 4 + ["68"]) == 70
    assert fc.planned_laps(["70", "68"]) == 70
    assert fc.planned_laps(["101"]) is None


def test_outputs_and_preservation(capsys):
    """Preserve historical data and old values when new values fail validation."""
    old = empty()
    old["monza"] = {"historical": {"race": [{"name": "José", "laps": 8}]}, "extra": 42}
    prior = empty()
    prior["monza"] = {
        "fastest_lap_time": "1:21.000",
        "fastest_lap_driver": "Old Driver",
        "fastest_lap_year": "2020",
    }

    def invalid(url):
        """Return an invalid new driver."""
        payload = wiki(url)
        payload["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"] = TEXT.replace(
            "Charles Leclerc", "Unsafe🚗"
        )
        return payload

    w, j = fc.build_outputs(prior, old, invalid, jolpica, only=["monza"], today=date(2026, 1, 1))
    assert w["monza"]["fastest_lap_driver"] == "Old Driver"
    assert (
        "warning: monza: Wikipedia: missing or invalid lap record; keeping previous value"
        in capsys.readouterr().err
    )
    assert j["monza"]["historical"] == old["monza"]["historical"]
    assert j["monza"]["extra"] == 42 and j["monza"]["number_of_laps"] == 70
    assert j["monza"]["first_grand_prix"] == "1950"
    assert type(j["monza"]["number_of_laps"]) is int
    assert "first_grand_prix" not in old["monza"]
    assert list(w) == [c[0] for c in fc.CIRCUITS]
    assert w["sepang"]["circuit_length"] is None
    assert w["monza"]["_provenance"]["fields"]["circuit_length"]["revid"] == 42


def test_transport():
    """Enforce HTTPS, headers, status, size and JSON parsing with MockTransport."""
    calls = []

    def handler(request):
        """Inspect requests and supply controlled responses."""
        calls.append(request)
        assert request.headers["User-Agent"] == fc.USER_AGENT
        if request.url.path == "/large":
            return httpx.Response(200, content=b" " * (fc.LIMIT + 1))
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"Location": "https://example.org/"})
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fc.fetch_json(client, "https://example.org/") == {"ok": True}
        for url in [
            "http://example.org/",
            "https://example.org/large",
            "https://example.org/redirect",
        ]:
            with pytest.raises(ValueError):
                fc.fetch_json(client, url)
        waits = []
        fetch = fc.PacedFetcher(client, waits.append)
        for url in [
            "https://en.wikipedia.org/",
            "https://en.wikipedia.org/",
            "https://api.jolpi.ca/",
            "https://api.jolpi.ca/",
        ]:
            fetch(url)
        assert waits == [1.0, 0.6]
    assert all(r.url.scheme == "https" for r in calls)


@pytest.mark.parametrize("dry_run,jump", [(True, False), (False, True), (False, False)])
def test_cli(tmp_path, monkeypatch, capsys, dry_run, jump):
    """Use MockTransport to verify no writes for dry runs and jump failures."""
    wp, jp = tmp_path / "wiki.json", tmp_path / "jolpica.json"
    prior = empty()
    if jump:
        prior["monza"] = {"circuit_length": "4.000km"}
    wp.write_text(json.dumps(prior))
    jp.write_text(json.dumps(empty()))
    before = (wp.read_bytes(), jp.read_bytes())

    def handler(request):
        """Serve both services entirely offline."""
        return httpx.Response(
            200,
            json=wiki(str(request.url))
            if request.url.host == "en.wikipedia.org"
            else jolpica(str(request.url)),
        )

    original = httpx.Client

    def client(**kwargs):
        """Install the offline transport in the production client."""
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(fc.httpx, "Client", client)
    args = ["--only", "monza", "--wikipedia-out", str(wp), "--jolpica-out", str(jp)]
    if dry_run:
        args.append("--dry-run")
    assert fc.main(args) == (1 if jump else 0)
    if dry_run or jump:
        assert (wp.read_bytes(), jp.read_bytes()) == before
    else:
        assert json.loads(wp.read_bytes())["monza"]["circuit_length"] == "5.278km"
        assert json.loads(jp.read_bytes())["monza"]["first_grand_prix"] == "1950"
        assert wp.read_bytes().endswith(b"\n")
    captured = capsys.readouterr()
    assert "jump exceeds" in captured.err if jump else " -> " in captured.out


def test_baseline_and_jump_boundary():
    """Use the baseline only without an existing length; permit an exact 5% change."""
    baseline = {"monza": {"circuit_length": "4.000km"}}
    with pytest.raises(ValueError, match="jump exceeds"):
        fc.build_outputs({}, empty(), wiki, jolpica, only=["monza"], baseline=baseline)
    old = {"monza": {"circuit_length": "5.278km"}}
    fc.build_outputs(old, empty(), wiki, jolpica, only=["monza"], baseline=baseline)

    def boundary(url):
        """Return a length exactly 5% above the baseline."""
        result = wiki(url)
        result["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"] = TEXT.replace(
            "5.278", "4.200"
        )
        return result

    fc.build_outputs({}, empty(), boundary, jolpica, only=["monza"], baseline=baseline)


def test_bad_response_and_nested_markup(capsys):
    """Fail safely on untrusted envelopes and deeply nested wikitext."""

    def broken(url):
        """Supply a wrong-type API response."""
        return {"query": [], "MRData": {"total": True, "RaceTable": {"Races": []}}}

    w, j = fc.build_outputs({}, empty(), broken, broken, only=["sepang"])
    assert all(w["sepang"][field] is None for field in fc.FIELDS)
    assert j["sepang"] == {}
    assert "warning:" in capsys.readouterr().err
    assert fc.clean_wikitext("{{" * 2000 + "bad" + "}}" * 2000 + "Good") == "Good"
    assert fc.clean_wikitext("[[" * 2000 + "Name" + "]]" * 2000) == "Name"
    assert fc.clean_wikitext("[[" + " " * 100 + "File:x|bad]]") == ""
    assert fc.clean_wikitext("&lt;b&gt;Good&lt;/b&gt; '''Name''' HTTPS://example.org") == (
        "Good Name"
    )


def test_unusable_existing_file(tmp_path, capsys):
    """Reject malformed existing files before any fetch or write."""
    path = tmp_path / "broken.json"
    path.write_text("[]")
    assert fc.main(["--jolpica-out", str(path)]) == 1
    assert path.read_text() == "[]"
    assert "error:" in capsys.readouterr().err


def test_comments_after_infobox_name(capsys):
    """Parse the Americas layout without using numbered historical records."""
    text = """{{Infobox motorsport venue
<!-- Completions are listed here -->
<!-- Former layouts below -->
| name = Circuit of the Americas
| events = {{Plain list|'''Current:'''<br/>
* '''[[Formula One]]'''
* ''[[A|B]]''<br>(2013–2017)}}
| length_km = 5.513
| length2_km = 3.427
| record_time2 = 1:20.000
}}"""
    assert fc.parse_infobox(text) == {"length_km": " 5.513\n"}
    assert fc.extract_wikipedia_facts(text, 2026, "americas") == {"circuit_length": "5.513km"}
    assert capsys.readouterr().err == (
        "warning: americas: Wikipedia: missing or invalid lap record; keeping previous value\n"
    )


def test_invalid_values_warn_with_circuit_id(capsys):
    """Identify the circuit and explain preservation for both sources."""
    prior = {"americas": {"circuit_length": "5.513km"}}
    old = {"americas": {"first_grand_prix": "2012", "number_of_laps": 56}}

    def invalid_wiki(url):
        """Return an empty infobox revision."""
        payload = wiki(url)
        payload["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"] = ""
        return payload

    def invalid_jolpica(url):
        """Return an invalid first season."""
        return {"MRData": {"total": "1", "RaceTable": {"Races": [{"season": "bad"}]}}}

    w, j = fc.build_outputs(prior, old, invalid_wiki, invalid_jolpica, only=["americas"])
    assert w["americas"]["circuit_length"] == "5.513km"
    assert j["americas"]["first_grand_prix"] == "2012"
    assert j["americas"]["number_of_laps"] == 56
    assert capsys.readouterr().err.splitlines() == [
        "warning: americas: Wikipedia: missing or invalid circuit_length; keeping previous value",
        "warning: americas: Wikipedia: missing or invalid lap record; keeping previous value",
        "warning: americas: Jolpica: invalid first_grand_prix; keeping previous value",
    ]


@pytest.mark.parametrize(
    "name,expected",
    [
        ("www.evil.example", False),
        ("evil.com", False),
        ("A.Kimi", False),
        ("A. Kimi Antonelli", True),
        ("Carlos Sainz Jr.", True),
        ("Sergio Pérez", True),
        ("Jean-Éric Vergne", True),
        ("O'Neil", True),
    ],
)
def test_driver_dots(name, expected):
    """Reject domain-like dots while preserving initials and supported names."""
    assert fc.valid_driver(name) is expected


@pytest.mark.parametrize(
    "field,previous",
    [("fastest_lap_time", "1:00.000"), ("number_of_laps", 50), ("number_of_laps", "50")],
)
@pytest.mark.parametrize("use_baseline", [False, True])
def test_additional_jump_guards(field, previous, use_baseline):
    """Guard valid existing and baseline times and integer or string lap counts."""
    prior = {"monza": {field: previous}}
    baseline = prior if use_baseline else None
    old = {} if use_baseline else prior
    with pytest.raises(ValueError, match=f"monza: {field} jump exceeds 5%"):
        fc.build_outputs(
            old if field == "fastest_lap_time" else {},
            old if field == "number_of_laps" else {},
            wiki,
            jolpica,
            only=["monza"],
            baseline=baseline,
        )


@pytest.mark.parametrize(
    "field,previous,new,convert",
    [
        ("fastest_lap_time", "1:00.000", "1:03.000", lambda v: int(v[0]) * 60 + float(v[2:])),
        ("number_of_laps", "60", 63, float),
        ("number_of_laps", 60, 57, float),
    ],
)
def test_jump_exact_boundary(field, previous, new, convert):
    """Allow exact five-percent changes including decreases."""
    fc.check_jump("monza", field, previous, new, convert)


@pytest.mark.parametrize(
    "title,redirects",
    [
        (None, []),
        ("Autodromo Actual", []),
        ("Monza Circuit", [{"from": "Alias", "to": "Monza Circuit"}]),
    ],
)
def test_resolved_title(title, redirects, capsys):
    """Record resolved titles and warn on either title changes or redirect metadata."""

    def fetch(url):
        """Return optional resolved title and redirect metadata."""
        payload = wiki(url)
        if title is not None:
            payload["query"]["pages"][0]["title"] = title
        payload["query"]["redirects"] = redirects
        return payload

    output, _ = fc.build_outputs({}, {}, fetch, jolpica, only=["monza"])
    actual = title or "Monza Circuit"
    for entry in output["monza"]["_provenance"]["fields"].values():
        assert entry["title"] == actual
        assert (
            entry["source"]
            == f"https://en.wikipedia.org/w/index.php?title={actual.replace(' ', '_')}&oldid=42"
        )
    assert ("redirected to" in capsys.readouterr().err) is bool(
        redirects or title == "Autodromo Actual"
    )


@pytest.mark.parametrize("title", [None, "", " ", "a" * 256, "Bad\nTitle", "Bad\x7fTitle"])
def test_invalid_resolved_title(title):
    """Reject unsafe resolved titles rather than storing them in provenance."""
    payload = wiki("")
    payload["query"]["pages"][0]["title"] = title
    with pytest.raises(ValueError, match="invalid Wikipedia title"):
        fc.wikipedia_revision(payload, "Monza Circuit")


def test_total_deadline(monkeypatch):
    """Reject a response whose streamed chunks exceed the total deadline."""
    ticks = [0.0, float(fc.TOTAL_DEADLINE) + 0.001]
    monkeypatch.setattr(fc.time, "monotonic", lambda: ticks.pop(0) if len(ticks) > 1 else ticks[0])
    with (
        httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={}))
        ) as client,
        pytest.raises(ValueError, match="request exceeded total deadline"),
    ):
        fc.fetch_json(client, "https://example.org/")


@pytest.mark.parametrize("field", ["fastest_lap_time", "number_of_laps", None])
def test_cli_new_values_and_guard_failures(tmp_path, monkeypatch, capsys, field):
    """Print baseline-aware NEW summaries and avoid both writes on new guard failures."""
    wp, jp, bp = (tmp_path / name for name in ("wiki.json", "jolpica.json", "baseline.json"))
    prior_wiki, prior_jolpica = empty(), empty()
    if field == "fastest_lap_time":
        prior_wiki["monza"][field] = "1:00.000"
    elif field == "number_of_laps":
        prior_jolpica["monza"][field] = 50
    baseline = empty()
    baseline["monza"]["circuit_length"] = "5.278km"
    for path, records in ((wp, prior_wiki), (jp, prior_jolpica), (bp, baseline)):
        path.write_text(json.dumps(records))
    before = wp.read_bytes(), jp.read_bytes()

    def fetch(self, url):
        """Serve source fixtures without requests or pacing."""
        return wiki(url) if urlsplit(url).hostname == "en.wikipedia.org" else jolpica(url)

    monkeypatch.setattr(fc.PacedFetcher, "__call__", fetch)
    result = fc.main(
        [
            "--only",
            "monza",
            "--wikipedia-out",
            str(wp),
            "--jolpica-out",
            str(jp),
            "--baseline",
            str(bp),
        ]
        + ([] if field else ["--dry-run"])
    )
    assert result == (1 if field else 0)
    assert (wp.read_bytes(), jp.read_bytes()) == before
    captured = capsys.readouterr()
    if field:
        assert f"{field} jump exceeds 5%" in captured.err
    else:
        assert "Wikipedia NEW monza.fastest_lap_time: None -> '1:20.901'" in captured.out
        assert "Wikipedia NEW monza.circuit_length" not in captured.out
        assert "New values (no previous value, jump guard not applied): 5" in captured.out
        assert "Jolpica monza.number_of_laps = 70" in captured.out
