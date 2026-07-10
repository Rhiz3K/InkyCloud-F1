#!/usr/bin/env python3
"""Download F1 driver number logos and convert to 1-bit for E-Ink display."""

import asyncio
import logging
from pathlib import Path
from typing import cast

import httpx
from PIL import Image, ImageFilter, ImageOps

from app.utils.image_assets import atomic_save_image, decode_image_bytes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DRIVER_CODES = {
    "albon": "ALEALB01",
    "alonso": "FERALO01",
    "antonelli": "ANDANT01",
    "bearman": "OLIBEA01",
    "bortoleto": "GABBOR01",
    "colapinto": "FRACOL01",
    "gasly": "PIEGAS01",
    "hadjar": "ISAHAD01",
    "hamilton": "LEWHAM01",
    "hulkenberg": "NICHUL01",
    "lawson": "LIALAW01",
    "leclerc": "CHALEC01",
    "norris": "LANNOR01",
    "ocon": "ESTOCO01",
    "piastri": "OSCPIA01",
    "russell": "GEORUS01",
    "sainz": "CARSAI01",
    "stroll": "LANSTR01",
    "tsunoda": "YUKTSU01",
    "verstappen": "MAXVER01",
    "doohan": "JACDOO01",
}

BASE_URL = "https://media.formula1.com/image/upload"
SOURCE_WIDTH = 500
OUTPUT_WIDTH = 150


def get_number_logo_url(driver_code: str) -> str:
    path = "content/dam/fom-website/2018-redesign-assets/drivers/number-logos"
    return f"{BASE_URL}/f_png,c_limit,w_{SOURCE_WIDTH}/{path}/{driver_code}"


def convert_to_silhouette(img: Image.Image) -> Image.Image:
    """Convert colored number logo to clean black on white 1-bit image."""
    img_rgba = img.convert("RGBA")

    grayscale = Image.new("L", img_rgba.size, 255)
    for y in range(img_rgba.height):
        for x in range(img_rgba.width):
            r, g, b, a = cast(tuple[int, int, int, int], img_rgba.getpixel((x, y)))
            if a > 50:
                luminance = int(0.299 * r + 0.587 * g + 0.114 * b)
                alpha_factor = a / 255.0
                grayscale.putpixel((x, y), int(255 - (255 - luminance) * alpha_factor))

    grayscale = grayscale.filter(ImageFilter.MedianFilter(3))

    threshold = 200
    binary = grayscale.point(lambda p: 0 if p < threshold else 255)

    binary = ImageOps.invert(binary)
    binary = binary.filter(ImageFilter.MaxFilter(3))
    binary = ImageOps.invert(binary)

    scale = OUTPUT_WIDTH / binary.width
    new_height = int(binary.height * scale)
    binary = binary.resize((OUTPUT_WIDTH, new_height), Image.Resampling.LANCZOS)

    result = binary.point(lambda p: 0 if p < 128 else 255)
    result = result.convert("1")

    return result


async def download_driver_number(
    client: httpx.AsyncClient, driver_id: str, driver_code: str, output_dir: Path
) -> bool:
    url = get_number_logo_url(driver_code)
    output_path = output_dir / f"{driver_id}.png"

    try:
        response = await client.get(url)
        response.raise_for_status()

        img = decode_image_bytes(response.content)
        silhouette = convert_to_silhouette(img)
        atomic_save_image(output_path, silhouette, image_format="PNG")
        logger.info(f"Saved {driver_id} number to {output_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to download {driver_id}: {e}")
        return False


async def main() -> bool:
    output_dir = Path(__file__).parent.parent / "app" / "assets" / "images" / "drivers"
    output_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading driver numbers to {output_dir}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        tasks = [
            download_driver_number(client, driver_id, code, output_dir)
            for driver_id, code in DRIVER_CODES.items()
        ]
        results = await asyncio.gather(*tasks)

    success = sum(results)
    logger.info(f"Downloaded {success}/{len(DRIVER_CODES)} driver numbers")
    return success == len(DRIVER_CODES)


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
