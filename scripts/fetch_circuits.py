"""Manually refresh separately licensed circuit facts; never used at runtime."""

from __future__ import annotations

import argparse
import copy
import html
import json
import re
import sys
import time
from collections import Counter
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.utils.atomic_io import atomic_write_bytes_sync

# Reviewed application IDs, Jolpica IDs and English article titles.
CIRCUITS = (
    ("albert_park", "albert_park", "Albert Park Circuit"),
    ("shanghai", "shanghai", "Shanghai International Circuit"),
    ("suzuka", "suzuka", "Suzuka Circuit"),
    ("bahrain", "bahrain", "Bahrain International Circuit"),
    ("jeddah", "jeddah", "Jeddah Corniche Circuit"),
    ("miami", "miami", "Miami International Autodrome"),
    ("villeneuve", "villeneuve", "Circuit Gilles Villeneuve"),
    ("monaco", "monaco", "Circuit de Monaco"),
    ("catalunya", "catalunya", "Circuit de Barcelona-Catalunya"),
    ("red_bull_ring", "red_bull_ring", "Red Bull Ring"),
    ("silverstone", "silverstone", "Silverstone Circuit"),
    ("spa", "spa", "Circuit de Spa-Francorchamps"),
    ("hungaroring", "hungaroring", "Hungaroring"),
    ("zandvoort", "zandvoort", "Circuit Zandvoort"),
    ("monza", "monza", "Monza Circuit"),
    ("madring", "madring", "Madring"),
    ("baku", "baku", "Baku City Circuit"),
    ("marina_bay", "marina_bay", "Marina Bay Street Circuit"),
    ("americas", "americas", "Circuit of the Americas"),
    ("rodriguez", "rodriguez", "Autódromo Hermanos Rodríguez"),
    ("interlagos", "interlagos", "Interlagos Circuit"),
    ("las_vegas", "vegas", "Las Vegas Strip Circuit"),
    ("losail", "losail", "Lusail International Circuit"),
    ("yas_marina", "yas_marina", "Yas Marina Circuit"),
    ("imola", "imola", "Imola Circuit"),
    ("sepang", "sepang", "Sepang International Circuit"),
)
FIELDS = ("circuit_length", "fastest_lap_time", "fastest_lap_driver", "fastest_lap_year")
USER_AGENT = "InkyCloud-F1-data/1.0 (https://f1.inkycloud.click; privacy@inkycloud.click)"
LIMIT = 2 * 1024 * 1024
BASE = "https://api.jolpi.ca/ergast/f1"
METHOD_FIRST = "season of the first race with a classified winner at this circuit in Jolpica"
METHOD_LAPS = (
    "most frequent winner lap count over the last 5 races at this circuit (tie: highest); "
    "planned distance, not shortened races"
)
Fetch = Callable[[str], Any]


def warn(message: str) -> None:
    """Report a recoverable problem."""
    print(f"warning: {message}", file=sys.stderr)


def parse_infobox(text: str) -> dict[str, str]:
    """Find a venue template and split top-level parameters in linear time."""
    stack: list[tuple[list[int], int, bool]] = []
    pattern = re.compile(r"\s*(?:infobox[ _])?motorsport[ _]venue", re.I)
    links = 0
    i = 0
    while i < len(text):
        if text.startswith("<!--", i):
            end = text.find("-->", i + 4)
            i = len(text) if end < 0 else end + 3
            continue
        token = text[i : i + 2]
        if token == "[[":
            links += 1
        elif token == "]]":
            links = max(0, links - 1)
        elif token == "{{":
            match = pattern.match(text, i + 2)
            end = match.end() if match else i + 2
            if match:
                # Scan the suffix once; preserve comments in parameter values.
                while end < len(text):
                    if text[end].isspace():
                        end += 1
                    elif text.startswith("<!--", end):
                        close = text.find("-->", end + 4)
                        end = len(text) if close < 0 else close + 3
                    else:
                        break
            venue = match is not None and (text.startswith("|", end) or text.startswith("}}", end))
            stack.append(([], links, venue))
        elif text[i] == "|" and stack and links == stack[-1][1]:
            stack[-1][0].append(i)
        elif token == "}}" and stack:
            separators, _, venue = stack.pop()
            if venue:
                result = {}
                for begin, end in zip(separators, separators[1:] + [i]):
                    key, sep, value = text[begin + 1 : end].partition("=")
                    if sep and key.strip() in {
                        "length_km",
                        "record_time",
                        "record_driver",
                        "record_year",
                        "record_class",
                    }:
                        result[key.strip()] = value
                return result
        i += 2 if token in ("{{", "}}", "[[", "]]") else 1
    return {}


def clean_wikitext(text: str) -> str:
    """Remove untrusted markup with a nonrecursive linear scanner."""
    text = html.unescape(text)
    output: list[str] = []
    frames: list[tuple[int, bool]] = []
    template_depth = 0
    ref_depth = 0
    i = 0
    while i < len(text):
        if text.startswith("<!--", i):
            end = text.find("-->", i + 4)
            i = len(text) if end < 0 else end + 3
            continue
        if text[i] == "<":
            end = text.find(">", i + 1)
            if end < 0:
                break
            tag = text[i + 1 : end].strip().lower()
            if re.match(r"ref(?:\s|/|$)", tag) and not tag.endswith("/"):
                ref_depth += 1
            elif tag.startswith("/ref"):
                ref_depth = max(0, ref_depth - 1)
            i = end + 1
            continue
        if text.startswith("{{", i):
            template_depth += 1
            i += 2
            continue
        if text.startswith("}}", i) and template_depth:
            template_depth -= 1
            i += 2
            continue
        if template_depth or ref_depth:
            i += 1
            continue
        if text.startswith("[[", i):
            namespace = re.compile(r"\s*(?:file|image|category):", re.I).match(text, i + 2)
            frames.append((len(output), namespace is not None))
            i += 2
            continue
        if text.startswith("]]", i) and frames:
            start, discard = frames.pop()
            if discard:
                del output[start:]
            i += 2
            continue
        if text[i] == "|" and frames:
            del output[frames[-1][0] :]
            i += 1
            continue
        if text[i : i + 5].lower() == "[http":
            end = text.find("]", i + 1)
            i = len(text) if end < 0 else end + 1
            continue
        output.append(text[i])
        i += 1
    if frames:
        del output[frames[0][0] :]
    cleaned = "".join(output).replace("'" * 3, "").replace("''", "")
    cleaned = re.sub(r"https?://\S+", "", cleaned, flags=re.I)
    return " ".join(cleaned.split())


def valid_year(value: Any, current_year: int) -> bool:
    """Accept a four-digit season in the supported range."""
    return (
        isinstance(value, str)
        and bool(re.fullmatch(r"[0-9]{4}", value))
        and (1950 <= int(value) <= current_year)
    )


def valid_driver(value: Any) -> bool:
    """Check the display-safe driver alphabet and both length constraints."""
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        return False
    return len(value.split()[-1]) <= 20 and all(
        "A" <= c <= "Z"
        or "a" <= c <= "z"
        or "\u00c0" <= c <= "\u017f"
        and c not in "×÷"
        or c in " -'’."
        for c in value
    )


def valid_time(value: Any) -> bool:
    """Check exact lap-time syntax and inclusive time bounds."""
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]:[0-5][0-9]\.[0-9]{3}", value):
        return False
    return 50 <= int(value[0]) * 60 + float(value[2:]) <= 180


def valid_length(value: Any) -> bool:
    """Check canonical kilometre syntax and inclusive length bounds."""
    return (
        isinstance(value, str)
        and bool(re.fullmatch(r"[0-9]+\.[0-9]{3}km", value))
        and (1 <= float(value[:-2]) <= 10)
    )


def valid_laps(value: Any) -> bool:
    """Accept only one to three ASCII digits describing 1 through 100 laps."""
    return (
        isinstance(value, str)
        and bool(re.fullmatch(r"[0-9]{1,3}", value))
        and (1 <= int(value) <= 100)
    )


def planned_laps(values: list[str]) -> int | None:
    """Return the modal valid winner distance, choosing the highest tie."""
    if not values or not all(valid_laps(v) for v in values):
        return None
    counts = Counter(int(v) for v in values)
    return max(counts, key=lambda n: (counts[n], n))


def extract_wikipedia_facts(text: str, current_year: int, circuit_id: str) -> dict[str, str]:
    """Extract validated facts, treating the three lap-record fields as a unit."""
    params = {k: clean_wikitext(v) for k, v in parse_infobox(text).items()}
    facts = {}
    length = re.search(r"[0-9]+(?:\.[0-9]+)?", params.get("length_km", ""))
    if length and 1 <= float(length.group()) <= 10:
        facts["circuit_length"] = f"{float(length.group()):.3f}km"
    else:
        warn(f"{circuit_id}: Wikipedia: missing or invalid circuit_length; keeping previous value")
    lap_time = re.search(
        r"(?<![0-9])[0-9]:[0-9]{2}\.[0-9]{3}(?![0-9])", params.get("record_time", "")
    )
    year = re.search(r"(?<![0-9])[0-9]{4}(?![0-9])", params.get("record_year", ""))
    driver = params.get("record_driver", "")
    record_class = params.get("record_class")
    if record_class is not None and not any(s in record_class for s in ("Formula One", "F1")):
        warn(f"{circuit_id}: Wikipedia: record_class is not Formula One; keeping previous value")
    elif (
        lap_time
        and year
        and valid_time(lap_time.group())
        and valid_year(year.group(), current_year)
        and valid_driver(driver)
    ):
        facts.update(zip(FIELDS[1:], (lap_time.group(), driver, year.group()), strict=True))
    else:
        warn(f"{circuit_id}: Wikipedia: missing or invalid lap record; keeping previous value")
    return facts


def fetch_json(client: httpx.Client, url: str) -> Any:
    """Fetch bounded HTTPS JSON without redirects or alternate deserializers."""
    if urlsplit(url).scheme != "https":
        raise ValueError("HTTPS required")
    with client.stream(
        "GET", url, headers={"User-Agent": USER_AGENT}, follow_redirects=False, timeout=20
    ) as response:
        if response.status_code != 200:
            raise ValueError(f"HTTP {response.status_code}")
        body = bytearray()
        for chunk in response.iter_bytes():
            if len(body) + len(chunk) > LIMIT:
                raise ValueError("response exceeds 2 MiB")
            body.extend(chunk)
    return json.loads(body)


class PacedFetcher:
    """Apply a separate minimum interval before each source's subsequent requests."""

    def __init__(self, client: httpx.Client, sleep: Callable[[float], None] = time.sleep):
        """Keep injectable sleep and per-source request state."""
        self.client = client
        self.sleep = sleep
        self.seen: set[str] = set()

    def __call__(self, url: str) -> Any:
        """Wait between requests to the same service, including failed requests."""
        host = urlsplit(url).netloc
        if host in self.seen:
            self.sleep(1.0 if host == "en.wikipedia.org" else 0.6)
        self.seen.add(host)
        return fetch_json(self.client, url)


def wikipedia_revision(payload: Any) -> tuple[str, int]:
    """Validate the latest-revision envelope, ignoring returned article titles."""
    pages = payload["query"]["pages"]
    if not isinstance(pages, list) or len(pages) != 1:
        raise ValueError("invalid Wikipedia pages")
    revision = pages[0]["revisions"][0]
    revid = revision["revid"]
    content = revision["slots"]["main"]["content"]
    if type(revid) is not int or revid <= 0 or not isinstance(content, str):
        raise ValueError("invalid revision")
    return content, revid


def races(payload: Any) -> tuple[int, list[Any]]:
    """Validate Jolpica's total and race-list envelope."""
    data = payload["MRData"]
    total = data["total"]
    if isinstance(total, str) and re.fullmatch(r"[0-9]+", total):
        total = int(total)
    items = data["RaceTable"]["Races"]
    if type(total) is not int or total < 0 or not isinstance(items, list):
        raise ValueError("invalid Jolpica envelope")
    return total, items


def build_outputs(
    old_wiki: dict[str, Any],
    old_jolpica: dict[str, Any],
    fetch_wikipedia: Fetch,
    fetch_jolpica: Fetch,
    *,
    only: list[str] | None = None,
    baseline: dict[str, Any] | None = None,
    today: date | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build both datasets without mutations or writes; raise on any length jump."""
    today = today or date.today()
    wiki = {cid: copy.deepcopy(old_wiki.get(cid, {})) for cid, _, _ in CIRCUITS}
    jolpica = {cid: copy.deepcopy(old_jolpica.get(cid, {})) for cid, _, _ in CIRCUITS}
    for cid, jid, title in CIRCUITS:
        target = jolpica[cid]
        if any(target.get(field) is not None for field in ("first_grand_prix", "number_of_laps")):
            target.setdefault("_provenance", {})["scope"] = (
                "historical, first_grand_prix, number_of_laps"
            )
        record = wiki[cid]
        for field in FIELDS:
            record.setdefault(field, None)
        record.setdefault("_provenance", {}).update(
            {
                "author": "Wikipedia contributors",
                "license": "CC-BY-SA-4.0",
                "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
                "changes": "Facts extracted from the article infobox (length_km, record_time, "
                "record_driver, record_year); wiki markup removed; length formatted in km",
            }
        )
        record["_provenance"].setdefault("fields", {})
        if only is not None and cid not in only:
            continue
        try:
            url = str(
                httpx.URL(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "prop": "revisions",
                        "rvprop": "ids|content",
                        "rvslots": "main",
                        "titles": title,
                        "redirects": "1",
                        "format": "json",
                        "formatversion": "2",
                        "maxlag": "5",
                    },
                )
            )
            text, revid = wikipedia_revision(fetch_wikipedia(url))
            facts = extract_wikipedia_facts(text, today.year, cid)
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as exc:
            warn(f"{cid}: Wikipedia: {exc}; keeping previous value")
        else:
            previous = record.get("circuit_length") or (baseline or {}).get(cid, {}).get(
                "circuit_length"
            )
            length = facts.get("circuit_length")
            if (
                length
                and valid_length(previous)
                and abs(float(length[:-2]) - float(previous[:-2]))
                > float(previous[:-2]) * 0.05 + 1e-12
            ):
                raise ValueError(f"{cid}: circuit_length jump exceeds 5%")
            for field, value in facts.items():
                record[field] = value
                record["_provenance"]["fields"][field] = {
                    "source": "https://en.wikipedia.org/w/index.php?title="
                    f"{quote(title.replace(' ', '_'), safe='')}&oldid={revid}",
                    "title": title,
                    "revid": revid,
                    "retrieved_on": today.isoformat(),
                }
        try:
            first_url = f"{BASE}/circuits/{jid}/results/1.json?limit=1"
            total, first = races(fetch_jolpica(first_url))
            season = first[0]["season"]
            if not valid_year(season, today.year):
                raise ValueError("invalid first_grand_prix")
            last_url = f"{BASE}/circuits/{jid}/results/1.json?limit=5&offset={max(0, total - 5)}"
            _, last = races(fetch_jolpica(last_url))
            laps = planned_laps([race["Results"][0]["laps"] for race in last])
            if laps is None or len(last) > 5:
                raise ValueError("invalid number_of_laps")
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as exc:
            warn(f"{cid}: Jolpica: {exc}; keeping previous value")
        else:
            target = jolpica[cid]
            provenance = target.setdefault("_provenance", {})
            fields = provenance.setdefault("fields", {})
            for field, output_value, source, method in (
                ("first_grand_prix", season, first_url, METHOD_FIRST),
                ("number_of_laps", laps, last_url, METHOD_LAPS),
            ):
                target[field] = output_value
                fields[field] = {
                    "source": source,
                    "method": method,
                    "retrieved_on": today.isoformat(),
                }
            provenance["scope"] = "historical, first_grand_prix, number_of_laps"
    return wiki, jolpica


def load_existing(path: Path, *, optional: bool = False) -> dict[str, Any]:
    """Read and validate existing records before any requests or writes."""
    if optional and not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    ids = {c[0] for c in CIRCUITS}
    if not isinstance(data, dict) or set(data) != ids:
        raise ValueError(f"{path}: expected exactly the reviewed circuit IDs")
    for record in data.values():
        if not isinstance(record, dict):
            raise ValueError(f"{path}: invalid record")
        provenance = record.get("_provenance", {})
        if not isinstance(provenance, dict) or not isinstance(provenance.get("fields", {}), dict):
            raise ValueError(f"{path}: invalid provenance")
    return data


def main(argv: list[str] | None = None) -> int:
    """Run the manual refresh, printing differences before atomic replacement."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="+", choices=[c[0] for c in CIRCUITS])
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    root = Path(__file__).resolve().parents[1] / "app/assets"
    parser.add_argument("--wikipedia-out", type=Path, default=root / "circuits_wikipedia.json")
    parser.add_argument("--jolpica-out", type=Path, default=root / "circuits_jolpica.json")
    args = parser.parse_args(argv)
    if args.wikipedia_out.resolve() == args.jolpica_out.resolve():
        parser.error("output paths must differ")
    try:
        old_wiki = load_existing(args.wikipedia_out, optional=True)
        old_jolpica = load_existing(args.jolpica_out)
        baseline = load_existing(args.baseline) if args.baseline else None
        with httpx.Client(
            timeout=20, follow_redirects=False, headers={"User-Agent": USER_AGENT}
        ) as client:
            fetch = PacedFetcher(client)
            wiki, jolpica = build_outputs(
                old_wiki, old_jolpica, fetch, fetch, only=args.only, baseline=baseline
            )
        for label, old, new, fields in (
            ("Wikipedia", old_wiki, wiki, FIELDS),
            ("Jolpica", old_jolpica, jolpica, ("first_grand_prix", "number_of_laps")),
        ):
            for cid in new:
                for field in fields:
                    print(
                        f"{label} {cid}.{field}: {old.get(cid, {}).get(field)!r}"
                        f" -> {new[cid].get(field)!r}"
                    )
        if not args.dry_run:
            for path, data in ((args.wikipedia_out, wiki), (args.jolpica_out, jolpica)):
                atomic_write_bytes_sync(
                    path, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
                )
    except (OSError, ValueError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
