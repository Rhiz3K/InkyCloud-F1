#!/usr/bin/env python3
"""Download F1 team logos and save original color PNGs for color renderers."""

import asyncio
import logging
import subprocess
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TEAM_SLUGS = {
    "mclaren": "mclaren",
    "mercedes": "mercedes",
    "red_bull": "redbullracing",
    "ferrari": "ferrari",
    "williams": "williams",
    "racing_bulls": "racingbulls",
    "aston_martin": "astonmartin",
    "haas": "haasf1team",
    "sauber": "kicksauber",
    "alpine": "alpine",
}

SPECIAL_LOGO_URLS = {
    "audi": "https://upload.wikimedia.org/wikipedia/commons/a/ae/Logo_audi.jpg",
    "cadillac": "https://pngimg.com/d/cadillac_PNG42.png",
}

BASE_URL = "https://media.formula1.com/image/upload"
LOGO_HEIGHT = 200
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
}


def get_logo_url(team_id: str, team_slug: str) -> str:
    if team_id in SPECIAL_LOGO_URLS:
        return SPECIAL_LOGO_URLS[team_id]
    variant = "logo"
    return (
        f"{BASE_URL}/c_fit,h_{LOGO_HEIGHT}/q_auto/v1740000000/"
        f"common/f1/2025/{team_slug}/2025{team_slug}{variant}.webp"
    )


def download_with_curl(url: str) -> bytes:
    result = subprocess.run(
        [
            "curl",
            "-L",
            "-A",
            DEFAULT_HEADERS["User-Agent"],
            "-e",
            "https://commons.wikimedia.org/",
            "--fail",
            "--silent",
            "--show-error",
            url,
        ],
        check=True,
        capture_output=True,
    )
    return result.stdout


async def download_team_logo(
    client: httpx.AsyncClient, team_id: str, team_slug: str, output_dir: Path
) -> bool:
    url = get_logo_url(team_id, team_slug)
    output_path = output_dir / f"{team_id}.png"

    try:
        try:
            response = await client.get(url, headers=DEFAULT_HEADERS)
            response.raise_for_status()
            content = response.content
        except Exception:
            if team_id not in SPECIAL_LOGO_URLS:
                raise
            content = await asyncio.to_thread(download_with_curl, url)

        img = Image.open(BytesIO(content)).convert("RGBA")
        img.save(output_path, "PNG")
        logger.info("Saved %s color logo to %s (%s)", team_id, output_path, img.size)
        return True

    except Exception as e:
        logger.error("Failed to download %s: %s", team_id, e)
        return False


async def main():
    output_dir = Path(__file__).parent.parent / "app" / "assets" / "images" / "teams_color"
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Downloading team logos to %s", output_dir)

    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [
            download_team_logo(client, team_id, slug, output_dir)
            for team_id, slug in TEAM_SLUGS.items()
        ]
        tasks.extend(
            [
                download_team_logo(client, "audi", "audi", output_dir),
                download_team_logo(client, "cadillac", "cadillac", output_dir),
            ]
        )
        results = await asyncio.gather(*tasks)

    success = sum(results)
    logger.info("Downloaded %s/%s team logos", success, len(results))


if __name__ == "__main__":
    asyncio.run(main())
