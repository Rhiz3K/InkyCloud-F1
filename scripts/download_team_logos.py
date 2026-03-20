#!/usr/bin/env python3
"""Download F1 team logos and save original color PNGs for color renderers."""

import asyncio
import logging
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
    "audi": (
        "https://uploads.audi-mediacenter.com/system/production/media/129183/images/"
        "3fa11fecbd09344e7b9370c9e4a26e923c2eb491/A251983_large.png"
    ),
    "cadillac": (
        "https://news.cadillac.com/content/Pages/news/us/en/2025/may/0503-f1/"
        "_jcr_content/boilerplate/image.img.png/Cadillac-Formula-1-Team-Logo.png"
    ),
}

BASE_URL = "https://media.formula1.com/image/upload"
LOGO_HEIGHT = 200


def get_logo_url(team_id: str, team_slug: str) -> str:
    if team_id in SPECIAL_LOGO_URLS:
        return SPECIAL_LOGO_URLS[team_id]
    variant = "logo"
    return (
        f"{BASE_URL}/c_fit,h_{LOGO_HEIGHT}/q_auto/v1740000000/"
        f"common/f1/2025/{team_slug}/2025{team_slug}{variant}.webp"
    )


async def download_team_logo(
    client: httpx.AsyncClient, team_id: str, team_slug: str, output_dir: Path
) -> bool:
    url = get_logo_url(team_id, team_slug)
    output_path = output_dir / f"{team_id}.png"

    try:
        response = await client.get(url)
        response.raise_for_status()

        img = Image.open(BytesIO(response.content)).convert("RGBA")
        img.save(output_path, "PNG")
        logger.info("Saved %s color logo to %s (%s)", team_id, output_path, img.size)
        return True

    except Exception as e:
        logger.error(f"Failed to download {team_id}: {e}")
        return False


async def main():
    output_dir = Path(__file__).parent.parent / "app" / "assets" / "images" / "teams_color"
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading team logos to {output_dir}")

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
    logger.info(f"Downloaded {success}/{len(TEAM_SLUGS)} team logos")


if __name__ == "__main__":
    asyncio.run(main())
