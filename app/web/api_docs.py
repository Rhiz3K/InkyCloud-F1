"""API docs HTML page helpers.

Page strings live in ``translations/*.json`` under ``api_docs_*`` keys like every other page;
this module only builds the code examples, whose comments are localized for English and Czech.
"""

from __future__ import annotations

from typing import Any

from app.config import config

_CODE_COMMENTS: dict[str, dict[str, str]] = {
    "en": {
        "curl_next": "# Download next race calendar",
        "curl_localized": "# With Czech language and timezone",
        "curl_race": "# Specific race (year and round)",
        "curl_bwry": "# B/W/R/Y output for four-color E-Ink",
        "curl_auto": "# One URL: calendar in race week, teams otherwise",
        "python_docstring": "Download F1 calendar as BMP image.",
        "python_print": "Calendar saved as calendar.bmp",
        "python_usage": "# Usage",
        "js_fetch": "// Fetch and display calendar",
        "js_img": "// Display in img element",
        "js_download": "// Download as file",
    },
    "cs": {
        "curl_next": "# Stáhnout kalendář dalšího závodu",
        "curl_localized": "# S českým jazykem a časovým pásmem",
        "curl_race": "# Konkrétní závod (rok a kolo)",
        "curl_bwry": "# B/W/R/Y varianta pro černo-bílo-červeno-žlutý e-paper",
        "curl_auto": "# Jedna URL: v závodním týdnu kalendář, jinak týmy",
        "python_docstring": "Stáhne F1 kalendář jako BMP obrázek.",
        "python_print": "Kalendář uložen jako calendar.bmp",
        "python_usage": "# Použití",
        "js_fetch": "// Načíst a zobrazit kalendář",
        "js_img": "// Zobrazit v img elementu",
        "js_download": "// Stáhnout jako soubor",
    },
}


def build_code_examples(ui_lang: str, server_url: str) -> dict[str, str]:
    """Build the curl, Python and JavaScript examples (Czech comments for ``cs``)."""
    c = _CODE_COMMENTS.get(ui_lang, _CODE_COMMENTS["en"])
    return {
        "code_curl": (
            f"{c['curl_next']}\n"
            f'curl -o calendar.bmp "{server_url}/calendar.bmp"\n\n'
            f"{c['curl_localized']}\n"
            f'curl -o calendar.bmp "{server_url}/calendar.bmp?lang=cs&tz=Europe/Prague"\n\n'
            f"{c['curl_race']}\n"
            f'curl -o calendar.bmp "{server_url}/calendar.bmp?year=2026&round=6"\n\n'
            f"{c['curl_bwry']}\n"
            f'curl -o calendar.bmp "{server_url}/calendar.bmp?display=bwry"\n\n'
            f"{c['curl_auto']}\n"
            f'curl -o screen.bmp "{server_url}/auto.bmp?lang=cs&tz=Europe/Prague"'
        ),
        "code_python": (
            "import httpx\n\n"
            'async def get_f1_calendar(lang: str = "en", tz: str = "Europe/Prague"):\n'
            f'    """{c["python_docstring"]}"""\n'
            "    async with httpx.AsyncClient() as client:\n"
            "        response = await client.get(\n"
            f'            "{server_url}/calendar.bmp",\n'
            '            params={"lang": lang, "tz": tz}\n'
            "        )\n"
            "        response.raise_for_status()\n\n"
            '        with open("calendar.bmp", "wb") as f:\n'
            "            f.write(response.content)\n\n"
            f'        print("{c["python_print"]}")\n\n'
            f"{c['python_usage']}\n"
            "import asyncio\n"
            'asyncio.run(get_f1_calendar(lang="cs"))'
        ),
        "code_javascript": (
            f"{c['js_fetch']}\n"
            "async function loadF1Calendar(lang = 'en', tz = 'Europe/Prague') {\n"
            f"    const url = new URL('{server_url}/calendar.bmp');\n"
            "    url.searchParams.set('lang', lang);\n"
            "    url.searchParams.set('tz', tz);\n\n"
            "    const response = await fetch(url);\n"
            "    const blob = await response.blob();\n\n"
            f"    {c['js_img']}\n"
            "    const img = document.getElementById('calendar');\n"
            "    img.src = URL.createObjectURL(blob);\n"
            "}\n\n"
            f"{c['js_download']}\n"
            "async function downloadCalendar() {\n"
            "    const response = await fetch('/calendar.bmp?lang=cs');\n"
            "    const blob = await response.blob();\n\n"
            "    const link = document.createElement('a');\n"
            "    link.href = URL.createObjectURL(blob);\n"
            "    link.download = 'f1-calendar.bmp';\n"
            "    link.click();\n"
            "}"
        ),
    }


def build_api_docs_context(ui_lang: str, server_url: str) -> dict[str, Any]:
    """Build the code snippets and server values for api_docs.html."""
    return {
        **build_code_examples(ui_lang, server_url),
        "server_url": server_url,
        "default_timezone": config.DEFAULT_TIMEZONE,
    }
