"""API docs HTML page helpers."""

from __future__ import annotations

from typing import Any


def build_api_docs_context(ui_lang: str) -> dict[str, Any]:
    """Build localized strings and code snippets for api_docs.html."""
    if ui_lang == "cs":
        curl_comment1 = "# Stáhnout kalendář dalšího závodu"
        curl_comment2 = "# S českým jazykem a časovým pásmem"
        curl_comment3 = "# Konkrétní závod (rok a kolo)"
        curl_comment4 = "# BWR varianta pro černo-bílo-červený e-paper"
        python_docstring = "Stáhne F1 kalendář jako BMP obrázek."
        python_print = "Kalendář uložen jako calendar.bmp"
        python_usage = "# Použití"
        js_comment1 = "// Načíst a zobrazit kalendář"
        js_comment2 = "// Zobrazit v img elementu"
        js_comment3 = "// Stáhnout jako soubor"

        eg = "např."

        return {
            "code_curl": (
                f"{curl_comment1}\n"
                'curl -o calendar.bmp "https://f1-eink.example.com/calendar.bmp"\n\n'
                f"{curl_comment2}\n"
                'curl -o calendar.bmp "https://f1-eink.example.com/calendar.bmp?lang=cs&tz=Europe/Prague"\n\n'
                f"{curl_comment3}\n"
                'curl -o calendar.bmp "https://f1-eink.example.com/calendar.bmp?year=2025&round=5"\n\n'
                f"{curl_comment4}\n"
                'curl -o calendar.bmp "https://f1-eink.example.com/calendar.bmp?display=bwr"'
            ),
            "code_python": (
                "import httpx\n\n"
                'async def get_f1_calendar(lang: str = "en", tz: str = "Europe/Prague"):\n'
                f'    """{python_docstring}"""\n'
                "    async with httpx.AsyncClient() as client:\n"
                "        response = await client.get(\n"
                '            "https://f1-eink.example.com/calendar.bmp",\n'
                '            params={"lang": lang, "tz": tz}\n'
                "        )\n"
                "        response.raise_for_status()\n\n"
                '        with open("calendar.bmp", "wb") as f:\n'
                "            f.write(response.content)\n\n"
                f'        print("{python_print}")\n\n'
                f"{python_usage}\n"
                "import asyncio\n"
                'asyncio.run(get_f1_calendar(lang="cs"))'
            ),
            "code_javascript": (
                f"{js_comment1}\n"
                "async function loadF1Calendar(lang = 'en', tz = 'Europe/Prague') {\n"
                "    const url = new URL('https://f1-eink.example.com/calendar.bmp');\n"
                "    url.searchParams.set('lang', lang);\n"
                "    url.searchParams.set('tz', tz);\n\n"
                "    const response = await fetch(url);\n"
                "    const blob = await response.blob();\n\n"
                f"    {js_comment2}\n"
                "    const img = document.getElementById('calendar');\n"
                "    img.src = URL.createObjectURL(blob);\n"
                "}\n\n"
                f"{js_comment3}\n"
                "async function downloadCalendar() {\n"
                "    const response = await fetch('/calendar.bmp?lang=cs');\n"
                "    const blob = await response.blob();\n\n"
                "    const link = document.createElement('a');\n"
                "    link.href = URL.createObjectURL(blob);\n"
                "    link.download = 'f1-calendar.bmp';\n"
                "    link.click();\n"
                "}"
            ),
            "lang_desc": "Kód jazyka pro text kalendáře",
            "year_desc": "Rok sezóny pro konkrétní závod",
            "round_desc": "Číslo kola (1-24) pro konkrétní závod",
            "tz_desc": "Časové pásmo pro časy v harmonogramu (IANA formát)",
            "display_desc": "Režim výstupu pro 1bit, BWR nebo Spectra 6 displeje",
            "weather_desc": "Zapnutí nebo vypnutí překrytí s počasím",
            "weather_type_desc": "Typ zobrazených dat o počasí",
            "calendar_desc": (
                "Generuje F1 kalendář jako BMP obrázek (800×480) "
                "pro 1bit, 4bit BWR a Spectra 6 E-Ink displeje."
            ),
            "eg": eg,
            "dimensions_label": "Rozměry",
            "color_depth_label": "Barevná hloubka",
            "races_desc": "Seznam všech závodů pro danou sezónu",
            "race_desc": "Detailní informace o konkrétním závodě včetně harmonogramu",
            "stats_desc": "Statistiky požadavků (počet za hodinu a 24 hodin)",
            "health_desc": "Kontrola zdraví služby",
            "json_api_desc": "Dokumentace API ve formátu JSON",
            "laskakit_title": "Pro LaskaKit / zivyobraz.eu:",
            "laskakit_step1": "V zivyobraz.eu vyberte jako zdroj obsahu: URL s obrázkem",
            "laskakit_step2": "Vložte URL",
            "laskakit_step3": "Nastavte interval obnovování na 1-6 hodin",
            "close_btn": "Zavřít",
            "loading_text": "Načítání...",
            "error_text": "Chyba",
        }

    curl_comment1 = "# Download next race calendar"
    curl_comment2 = "# With Czech language and timezone"
    curl_comment3 = "# Specific race (year and round)"
    curl_comment4 = "# Black/white/red output for tri-color E-Ink"
    python_docstring = "Download F1 calendar as BMP image."
    python_print = "Calendar saved as calendar.bmp"
    python_usage = "# Usage"
    js_comment1 = "// Fetch and display calendar"
    js_comment2 = "// Display in img element"
    js_comment3 = "// Download as file"

    eg = "e.g."

    return {
        "code_curl": (
            f"{curl_comment1}\n"
            'curl -o calendar.bmp "https://f1-eink.example.com/calendar.bmp"\n\n'
            f"{curl_comment2}\n"
            'curl -o calendar.bmp "https://f1-eink.example.com/calendar.bmp?lang=cs&tz=Europe/Prague"\n\n'
            f"{curl_comment3}\n"
            'curl -o calendar.bmp "https://f1-eink.example.com/calendar.bmp?year=2025&round=5"\n\n'
            f"{curl_comment4}\n"
            'curl -o calendar.bmp "https://f1-eink.example.com/calendar.bmp?display=bwr"'
        ),
        "code_python": (
            "import httpx\n\n"
            'async def get_f1_calendar(lang: str = "en", tz: str = "Europe/Prague"):\n'
            f'    """{python_docstring}"""\n'
            "    async with httpx.AsyncClient() as client:\n"
            "        response = await client.get(\n"
            '            "https://f1-eink.example.com/calendar.bmp",\n'
            '            params={"lang": lang, "tz": tz}\n'
            "        )\n"
            "        response.raise_for_status()\n\n"
            '        with open("calendar.bmp", "wb") as f:\n'
            "            f.write(response.content)\n\n"
            f'        print("{python_print}")\n\n'
            f"{python_usage}\n"
            "import asyncio\n"
            'asyncio.run(get_f1_calendar(lang="cs"))'
        ),
        "code_javascript": (
            f"{js_comment1}\n"
            "async function loadF1Calendar(lang = 'en', tz = 'Europe/Prague') {\n"
            "    const url = new URL('https://f1-eink.example.com/calendar.bmp');\n"
            "    url.searchParams.set('lang', lang);\n"
            "    url.searchParams.set('tz', tz);\n\n"
            "    const response = await fetch(url);\n"
            "    const blob = await response.blob();\n\n"
            f"    {js_comment2}\n"
            "    const img = document.getElementById('calendar');\n"
            "    img.src = URL.createObjectURL(blob);\n"
            "}\n\n"
            f"{js_comment3}\n"
            "async function downloadCalendar() {\n"
            "    const response = await fetch('/calendar.bmp?lang=cs');\n"
            "    const blob = await response.blob();\n\n"
            "    const link = document.createElement('a');\n"
            "    link.href = URL.createObjectURL(blob);\n"
            "    link.download = 'f1-calendar.bmp';\n"
            "    link.click();\n"
            "}"
        ),
        "lang_desc": "Language code for calendar text",
        "year_desc": "Season year for specific race",
        "round_desc": "Round number (1-24) for specific race",
        "tz_desc": "Timezone for schedule times (IANA format)",
        "display_desc": "Output mode for 1bit, BWR, or Spectra 6 displays",
        "weather_desc": "Enable or disable the weather overlay",
        "weather_type_desc": "Which weather data variant to render",
        "calendar_desc": (
            "Generates F1 calendar as a BMP image (800×480) "
            "for 1bit, 4-bit BWR, and Spectra 6 E-Ink displays."
        ),
        "eg": eg,
        "dimensions_label": "Dimensions",
        "color_depth_label": "Color depth",
        "races_desc": "List of all races for a given season",
        "race_desc": "Detailed race information including schedule",
        "stats_desc": "Request statistics (last hour and 24h counts)",
        "health_desc": "Service health check",
        "json_api_desc": "API documentation in JSON format",
        "laskakit_title": "For LaskaKit / zivyobraz.eu:",
        "laskakit_step1": "In zivyobraz.eu select content source: URL with image",
        "laskakit_step2": "Paste URL",
        "laskakit_step3": "Set refresh interval to 1-6 hours",
        "close_btn": "Close",
        "loading_text": "Loading...",
        "error_text": "Error",
    }
