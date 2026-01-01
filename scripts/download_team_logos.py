#!/usr/bin/env python3
"""Download F1 team logos and convert to 1-bit for E-Ink display."""

import asyncio
import logging
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, ImageOps

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

DARK_LOGO_TEAMS = {"ferrari", "sauber"}

BASE_URL = "https://media.formula1.com/image/upload"
LOGO_HEIGHT = 200


def get_logo_url(team_id: str, team_slug: str) -> str:
    variant = "logo" if team_id in DARK_LOGO_TEAMS else "logowhite"
    return (
        f"{BASE_URL}/c_fit,h_{LOGO_HEIGHT}/q_auto/v1740000000/"
        f"common/f1/2025/{team_slug}/2025{team_slug}{variant}.webp"
    )


def convert_to_1bit(img: Image.Image, use_luminance: bool = False) -> Image.Image:
    if use_luminance:
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        grayscale = img.convert("L")
        binary = grayscale.point(lambda p: 255 if p > 128 else 0)
        return binary.convert("1")

    if img.mode == "RGBA":
        alpha = img.split()[3]
        inverted_alpha = ImageOps.invert(alpha)
        binary = inverted_alpha.point(lambda p: 0 if p < 128 else 255)
        return binary.convert("1")
    elif img.mode == "LA":
        _, alpha = img.split()
        inverted_alpha = ImageOps.invert(alpha)
        binary = inverted_alpha.point(lambda p: 0 if p < 128 else 255)
        return binary.convert("1")
    else:
        if img.mode != "RGB":
            img = img.convert("RGB")
        grayscale = img.convert("L")
        inverted = ImageOps.invert(grayscale)
        binary = inverted.point(lambda p: 0 if p < 128 else 255)
        return binary.convert("1")


async def download_team_logo(
    client: httpx.AsyncClient, team_id: str, team_slug: str, output_dir: Path
) -> bool:
    url = get_logo_url(team_id, team_slug)
    output_path = output_dir / f"{team_id}.png"

    try:
        response = await client.get(url)
        response.raise_for_status()

        img = Image.open(BytesIO(response.content))
        use_luminance = team_id in DARK_LOGO_TEAMS
        logo_1bit = convert_to_1bit(img, use_luminance=use_luminance)
        logo_1bit.save(output_path, "PNG")
        logger.info(f"Saved {team_id} logo to {output_path} ({logo_1bit.size})")
        return True

    except Exception as e:
        logger.error(f"Failed to download {team_id}: {e}")
        return False


async def main():
    output_dir = Path(__file__).parent.parent / "app" / "assets" / "images" / "teams"
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading team logos to {output_dir}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [
            download_team_logo(client, team_id, slug, output_dir)
            for team_id, slug in TEAM_SLUGS.items()
        ]
        results = await asyncio.gather(*tasks)

    success = sum(results)
    logger.info(f"Downloaded {success}/{len(TEAM_SLUGS)} team logos")


if __name__ == "__main__":
    asyncio.run(main())
