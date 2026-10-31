"""Render teams.bmp concepts for the empty bottom-right slot.

Usage: python scripts/teams_ideas_demo.py OUTDIR
"""

from __future__ import annotations

import datetime as dt
import json
import pickle
import sys
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from app.services.i18n import get_translator  # noqa: E402
from app.services.renderer_teams_extras import NextRaceContext, draw_extra_panel  # noqa: E402
from app.services.renderers import create_renderer  # noqa: E402
from app.services.weather_service import WeatherData  # noqa: E402

UTC = dt.timezone.utc
CIRCUITS = {  # circuitId -> (code, iso2)
    "albert_park": ("AUS", "au"), "shanghai": ("CHN", "cn"), "suzuka": ("JPN", "jp"),
    "miami": ("MIA", "us"), "villeneuve": ("CAN", "ca"), "monaco": ("MON", "mc"),
    "catalunya": ("BAR", "es"), "red_bull_ring": ("AUT", "at"), "silverstone": ("GBR", "gb"),
    "spa": ("BEL", "be"), "hungaroring": ("HUN", "hu"), "zandvoort": ("NED", "nl"),
    "monza": ("ITA", "it"), "madring": ("MAD", "es"), "baku": ("AZE", "az"),
    "sepang": ("MAL", "my"), "marina_bay": ("SIN", "sg"), "americas": ("USA", "us"),
    "rodriguez": ("MEX", "mx"), "interlagos": ("BRA", "br"), "vegas": ("LVG", "us"),
    "losail": ("QAT", "qa"), "yas_marina": ("ABU", "ae"),
}


def load_races() -> list[dict]:
    data = json.loads((ROOT / "app/assets/seasons/2026.json").read_text(encoding="utf-8"))
    if isinstance(data, dict):
        races = data.get("races") or data.get("MRData", {}).get("RaceTable", {}).get("Races", [])
    else:
        races = data
    return sorted((r for r in races if "round" in r), key=lambda r: int(r["round"]))


def race_start(race: dict) -> dt.datetime:
    time = (race.get("time") or "12:00:00Z").rstrip("Z")
    return dt.datetime.fromisoformat(f"{race['date']}T{time}").replace(tzinfo=UTC)


def build_context(display: str) -> NextRaceContext:
    races = load_races()
    now_utc = dt.datetime(2026, 10, 31, 11, 15, tzinfo=UTC)
    rounds = []
    for race in races:
        cid = race["Circuit"]["circuitId"]
        code, iso2 = CIRCUITS.get(cid, (cid[:3].upper(), cid[:2]))
        rounds.append({"round": int(race["round"]), "code": code, "iso2": iso2,
                       "date": dt.date.fromisoformat(race["date"])})
    nxt = next(r for r in races if race_start(r) > now_utc)
    return NextRaceContext(
        rounds=rounds,
        next_round=int(nxt["round"]),
        next_name_cs="GP Mexika",
        next_start_utc=race_start(nxt),
        now_utc=now_utc,
        weather=WeatherData(temperature_c=25.6, weather_code=2, precipitation_probability=1),
        weather_is_sample=False,  # real Open-Meteo forecast fetched 2026-10-31
        circuit_id=nxt["Circuit"]["circuitId"],
        display=display,
        circuit_name=nxt["Circuit"].get("circuitName", ""),
    )


def render(display: str, data, variant: str | None) -> bytes:
    renderer = create_renderer(display, get_translator("cs"), "cs")
    if variant:
        ctx = build_context(display)
        original = renderer._draw_teams_content

        def patched(image, draw, teams):
            original(image, draw, teams)
            draw_extra_panel(variant, image, draw, renderer.theme, ctx)

        renderer._draw_teams_content = patched
    return renderer.render_teams_drivers(data)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    data = pickle.loads(Path("/workspace/f1-teamsbox/teams_data.pkl").read_bytes())
    for display in ("1bit", "spectra6"):
        base = render(display, data, None)
        Image.open(BytesIO(base)).convert("RGB").save(out / f"teams_base_{display}.png")
        for variant in "ABCDEF":
            bmp = render(display, data, variant)
            (out / f"teams_{variant}_{display}.bmp").write_bytes(bmp)
            Image.open(BytesIO(bmp)).convert("RGB").save(out / f"teams_{variant}_{display}.png")
            print(f"wrote teams_{variant}_{display}")


if __name__ == "__main__":
    main()
