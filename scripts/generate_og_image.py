"""Generate Open Graph preview image for social media sharing.

Creates a 1200x630 PNG image optimized for Facebook, Twitter, and LinkedIn previews.
Uses project branding (black background, racing red accent, TitilliumWeb font).

Usage:
    python scripts/generate_og_image.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Image dimensions (Open Graph standard)
WIDTH = 1200
HEIGHT = 630

# Colors (matching project branding)
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RACING_RED = (220, 38, 38)  # #DC2626

# Paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
FONTS_DIR = PROJECT_ROOT / "app" / "assets" / "fonts"
OUTPUT_PATH = PROJECT_ROOT / "app" / "assets" / "images" / "og-preview.png"


def load_font(name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load TitilliumWeb font or fall back to default."""
    font_path = FONTS_DIR / name
    if font_path.exists():
        return ImageFont.truetype(str(font_path), size)
    print(f"Warning: Font {name} not found, using default")
    return ImageFont.load_default()


def generate_og_image() -> None:
    """Generate the OG preview image."""
    # Create black background
    image = Image.new("RGB", (WIDTH, HEIGHT), BLACK)
    draw = ImageDraw.Draw(image)

    # Load fonts
    font_title = load_font("TitilliumWeb-Bold.ttf", 72)
    font_subtitle = load_font("TitilliumWeb-Regular.ttf", 32)
    font_small = load_font("TitilliumWeb-Regular.ttf", 24)

    # Draw racing red accent bar at top
    draw.rectangle([(0, 0), (WIDTH, 8)], fill=RACING_RED)

    # Draw F1 text with racing red
    f1_text = "F1"
    f1_bbox = draw.textbbox((0, 0), f1_text, font=font_title)
    f1_width = f1_bbox[2] - f1_bbox[0]

    # Main title: "F1 E-INK CALENDAR"
    title_text = " E-INK CALENDAR"
    title_bbox = draw.textbbox((0, 0), title_text, font=font_title)
    title_width = title_bbox[2] - title_bbox[0]

    total_width = f1_width + title_width
    start_x = (WIDTH - total_width) // 2
    title_y = 200

    # Draw "F1" in racing red
    draw.text((start_x, title_y), f1_text, font=font_title, fill=RACING_RED)

    # Draw rest of title in white
    draw.text((start_x + f1_width, title_y), title_text, font=font_title, fill=WHITE)

    # Subtitle
    subtitle_text = "Race schedules for your E-Ink display"
    subtitle_bbox = draw.textbbox((0, 0), subtitle_text, font=font_subtitle)
    subtitle_width = subtitle_bbox[2] - subtitle_bbox[0]
    subtitle_x = (WIDTH - subtitle_width) // 2
    draw.text((subtitle_x, 310), subtitle_text, font=font_subtitle, fill=WHITE)

    # Feature badges
    badges = ["800×480", "1-BIT BMP", "FREE & OPEN SOURCE"]
    badge_y = 420
    badge_spacing = 40
    badge_padding_x = 20
    badge_padding_y = 10

    # Calculate total width of all badges
    badge_sizes = []
    for badge in badges:
        bbox = draw.textbbox((0, 0), badge, font=font_small)
        badge_sizes.append((bbox[2] - bbox[0], bbox[3] - bbox[1]))

    total_badges_width = sum(w + badge_padding_x * 2 for w, _ in badge_sizes) + badge_spacing * (
        len(badges) - 1
    )
    badge_x = (WIDTH - total_badges_width) // 2

    for i, (badge, (bw, bh)) in enumerate(zip(badges, badge_sizes)):
        # Draw badge background
        rect_x1 = badge_x
        rect_y1 = badge_y
        rect_x2 = badge_x + bw + badge_padding_x * 2
        rect_y2 = badge_y + bh + badge_padding_y * 2

        # First badge in red, others in white outline
        if i == 0:
            draw.rectangle([(rect_x1, rect_y1), (rect_x2, rect_y2)], fill=RACING_RED)
            draw.text(
                (badge_x + badge_padding_x, badge_y + badge_padding_y),
                badge,
                font=font_small,
                fill=WHITE,
            )
        else:
            draw.rectangle([(rect_x1, rect_y1), (rect_x2, rect_y2)], outline=WHITE, width=2)
            draw.text(
                (badge_x + badge_padding_x, badge_y + badge_padding_y),
                badge,
                font=font_small,
                fill=WHITE,
            )

        badge_x = rect_x2 + badge_spacing

    # URL at bottom
    url_text = "f1.inkycloud.click"
    url_bbox = draw.textbbox((0, 0), url_text, font=font_small)
    url_width = url_bbox[2] - url_bbox[0]
    url_x = (WIDTH - url_width) // 2
    draw.text((url_x, 550), url_text, font=font_small, fill=RACING_RED)

    # Draw racing red accent bar at bottom
    draw.rectangle([(0, HEIGHT - 8), (WIDTH, HEIGHT)], fill=RACING_RED)

    # Save image
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUTPUT_PATH, "PNG", optimize=True)
    print(f"Generated OG preview image: {OUTPUT_PATH}")
    print(f"Size: {WIDTH}x{HEIGHT}")


if __name__ == "__main__":
    generate_og_image()
