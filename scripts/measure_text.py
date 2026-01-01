import sys

# Add project root to path
sys.path.append("/home/rhiz3k/Github/F1_eInk_cal")


from app.services.renderer import Renderer


def measure():
    # Mock translator
    translator = {}
    r = Renderer(translator)

    # "results_row" font
    font = r.fonts["results_row"]  # size 16

    # Typical time string
    text = "1:23.456"

    left, top, right, bottom = font.getbbox(text)
    width = right - left

    print(f"Font: {font.path}")
    print(f"Size: {font.size}")
    print(f"Text: '{text}'")
    print(f"Width: {width}")

    # Also measure "results_title" for column clearance
    title_font = r.fonts["results_title"]
    title_text = "QUALIFYING"
    left, top, right, bottom = title_font.getbbox(title_text)
    title_width = right - left
    print(f"Title: '{title_text}' Width: {title_width}")


if __name__ == "__main__":
    measure()
