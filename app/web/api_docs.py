"""API docs HTML page helpers."""

from __future__ import annotations

from typing import Any

API_DOCS_TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "lang_desc": "Language code for calendar text",
        "year_desc": "Season year for specific race",
        "round_desc": "Round number (1-24) for specific race",
        "tz_desc": "Timezone for schedule times (IANA format)",
        "display_desc": "Output mode for 1bit, B/W/R, B/W/R/Y, or Spectra 6 displays",
        "weather_desc": "Enable or disable the weather overlay",
        "weather_type_desc": "Which weather data variant to render",
        "calendar_desc": (
            "Generates F1 calendar as a BMP image (800×480) "
            "for 1bit, 4-bit B/W/R, 4-bit B/W/R/Y, and Spectra 6 E-Ink displays."
        ),
        "eg": "e.g.",
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
    },
    "cs": {
        "lang_desc": "Kód jazyka pro text kalendáře",
        "year_desc": "Rok sezóny pro konkrétní závod",
        "round_desc": "Číslo kola (1-24) pro konkrétní závod",
        "tz_desc": "Časové pásmo pro časy v harmonogramu (IANA formát)",
        "display_desc": "Režim výstupu pro 1bit, B/W/R, B/W/R/Y nebo Spectra 6 displeje",
        "weather_desc": "Zapnutí nebo vypnutí překrytí s počasím",
        "weather_type_desc": "Typ zobrazených dat o počasí",
        "calendar_desc": (
            "Generuje F1 kalendář jako BMP obrázek (800×480) "
            "pro 1bit, 4bit B/W/R, 4bit B/W/R/Y a Spectra 6 E-Ink displeje."
        ),
        "eg": "např.",
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
    },
    "de": {
        "lang_desc": "Sprachcode für den Kalendertext",
        "year_desc": "Saisonjahr für ein bestimmtes Rennen",
        "round_desc": "Rundennummer (1-24) für ein bestimmtes Rennen",
        "tz_desc": "Zeitzone für Zeitangaben im Zeitplan (IANA-Format)",
        "display_desc": "Ausgabemodus für 1bit-, B/W/R-, B/W/R/Y- oder Spectra-6-Displays",
        "weather_desc": "Wetter-Overlay aktivieren oder deaktivieren",
        "weather_type_desc": "Welche Wetterdatenvariante gerendert werden soll",
        "calendar_desc": (
            "Erzeugt einen F1-Kalender als BMP-Bild (800×480) "
            "für 1bit-, 4-Bit-B/W/R-, 4-Bit-B/W/R/Y- und Spectra-6-E-Ink-Displays."
        ),
        "eg": "z. B.",
        "dimensions_label": "Abmessungen",
        "color_depth_label": "Farbtiefe",
        "races_desc": "Liste aller Rennen einer Saison",
        "race_desc": "Detaillierte Renninformationen inklusive Zeitplan",
        "stats_desc": "Anfragestatistiken (letzte Stunde und 24h)",
        "health_desc": "Service-Statusprüfung",
        "json_api_desc": "API-Dokumentation im JSON-Format",
        "laskakit_title": "Für LaskaKit / zivyobraz.eu:",
        "laskakit_step1": "In zivyobraz.eu als Inhaltsquelle auswählen: URL mit Bild",
        "laskakit_step2": "URL einfügen",
        "laskakit_step3": "Aktualisierungsintervall auf 1-6 Stunden setzen",
        "close_btn": "Schließen",
        "loading_text": "Wird geladen...",
        "error_text": "Fehler",
    },
    "sk": {
        "lang_desc": "Kód jazyka pre text kalendára",
        "year_desc": "Rok sezóny pre konkrétne preteky",
        "round_desc": "Číslo kola (1-24) pre konkrétne preteky",
        "tz_desc": "Časové pásmo pre časy v harmonograme (formát IANA)",
        "display_desc": "Výstupný režim pre 1bit, B/W/R, B/W/R/Y alebo Spectra 6 displeje",
        "weather_desc": "Zapnúť alebo vypnúť vrstvu počasia",
        "weather_type_desc": "Ktorý variant údajov o počasí vyrenderovať",
        "calendar_desc": (
            "Generuje F1 kalendár ako BMP obrázok (800×480) "
            "pre 1bit, 4-bit B/W/R, 4-bit B/W/R/Y a Spectra 6 E-Ink displeje."
        ),
        "eg": "napr.",
        "dimensions_label": "Rozmery",
        "color_depth_label": "Farebná hĺbka",
        "races_desc": "Zoznam všetkých pretekov danej sezóny",
        "race_desc": "Detailné informácie o konkrétnych pretekoch vrátane harmonogramu",
        "stats_desc": "Štatistiky požiadaviek (posledná hodina a 24 hodín)",
        "health_desc": "Kontrola zdravia služby",
        "json_api_desc": "Dokumentácia API vo formáte JSON",
        "laskakit_title": "Pre LaskaKit / zivyobraz.eu:",
        "laskakit_step1": "V zivyobraz.eu vyberte ako zdroj obsahu: URL s obrázkom",
        "laskakit_step2": "Vložte URL",
        "laskakit_step3": "Nastavte interval obnovovania na 1-6 hodín",
        "close_btn": "Zavrieť",
        "loading_text": "Načítava sa...",
        "error_text": "Chyba",
    },
    "es": {
        "lang_desc": "Código de idioma para el texto del calendario",
        "year_desc": "Año de temporada para una carrera concreta",
        "round_desc": "Número de ronda (1-24) para una carrera concreta",
        "tz_desc": "Zona horaria para los horarios (formato IANA)",
        "display_desc": "Modo de salida para pantallas 1bit, B/W/R, B/W/R/Y o Spectra 6",
        "weather_desc": "Activa o desactiva la capa del tiempo",
        "weather_type_desc": "Qué variante de datos meteorológicos renderizar",
        "calendar_desc": (
            "Genera un calendario de F1 como imagen BMP (800×480) "
            "para pantallas E-Ink 1bit, 4-bit B/W/R, 4-bit B/W/R/Y y Spectra 6."
        ),
        "eg": "p. ej.",
        "dimensions_label": "Dimensiones",
        "color_depth_label": "Profundidad de color",
        "races_desc": "Lista de todas las carreras de una temporada",
        "race_desc": "Información detallada de una carrera, incluido el horario",
        "stats_desc": "Estadísticas de peticiones (última hora y 24 h)",
        "health_desc": "Comprobación del estado del servicio",
        "json_api_desc": "Documentación de la API en formato JSON",
        "laskakit_title": "Para LaskaKit / zivyobraz.eu:",
        "laskakit_step1": "En zivyobraz.eu selecciona como fuente de contenido: URL con imagen",
        "laskakit_step2": "Pega la URL",
        "laskakit_step3": "Configura el intervalo de actualización entre 1 y 6 horas",
        "close_btn": "Cerrar",
        "loading_text": "Cargando...",
        "error_text": "Error",
    },
    "it": {
        "lang_desc": "Codice lingua per il testo del calendario",
        "year_desc": "Anno di stagione per una gara specifica",
        "round_desc": "Numero del round (1-24) per una gara specifica",
        "tz_desc": "Fuso orario per gli orari del programma (formato IANA)",
        "display_desc": "Modalità di output per display 1bit, B/W/R, B/W/R/Y o Spectra 6",
        "weather_desc": "Abilita o disabilita l'overlay meteo",
        "weather_type_desc": "Quale variante dei dati meteo renderizzare",
        "calendar_desc": (
            "Genera un calendario F1 come immagine BMP (800×480) "
            "per display E-Ink 1bit, 4-bit B/W/R, 4-bit B/W/R/Y e Spectra 6."
        ),
        "eg": "ad es.",
        "dimensions_label": "Dimensioni",
        "color_depth_label": "Profondità colore",
        "races_desc": "Elenco di tutte le gare di una stagione",
        "race_desc": "Informazioni dettagliate sulla gara, incluso il programma",
        "stats_desc": "Statistiche delle richieste (ultima ora e 24h)",
        "health_desc": "Controllo stato del servizio",
        "json_api_desc": "Documentazione API in formato JSON",
        "laskakit_title": "Per LaskaKit / zivyobraz.eu:",
        "laskakit_step1": "In zivyobraz.eu seleziona come sorgente contenuto: URL con immagine",
        "laskakit_step2": "Incolla l'URL",
        "laskakit_step3": "Imposta l'intervallo di aggiornamento su 1-6 ore",
        "close_btn": "Chiudi",
        "loading_text": "Caricamento...",
        "error_text": "Errore",
    },
    "fr": {
        "lang_desc": "Code langue pour le texte du calendrier",
        "year_desc": "Année de saison pour une course spécifique",
        "round_desc": "Numéro de manche (1-24) pour une course spécifique",
        "tz_desc": "Fuseau horaire pour les horaires (format IANA)",
        "display_desc": "Mode de sortie pour écrans 1bit, B/W/R, B/W/R/Y ou Spectra 6",
        "weather_desc": "Activer ou désactiver l'overlay météo",
        "weather_type_desc": "Quelle variante de données météo afficher",
        "calendar_desc": (
            "Génère un calendrier F1 en image BMP (800×480) "
            "pour écrans E-Ink 1bit, 4-bit B/W/R, 4-bit B/W/R/Y et Spectra 6."
        ),
        "eg": "ex.",
        "dimensions_label": "Dimensions",
        "color_depth_label": "Profondeur de couleur",
        "races_desc": "Liste de toutes les courses d'une saison",
        "race_desc": "Informations détaillées sur une course, y compris l'horaire",
        "stats_desc": "Statistiques des requêtes (dernière heure et 24 h)",
        "health_desc": "Vérification de l'état du service",
        "json_api_desc": "Documentation API au format JSON",
        "laskakit_title": "Pour LaskaKit / zivyobraz.eu :",
        "laskakit_step1": "Dans zivyobraz.eu, choisissez comme source de contenu : URL avec image",
        "laskakit_step2": "Collez l'URL",
        "laskakit_step3": "Réglez l'intervalle d'actualisation sur 1 à 6 heures",
        "close_btn": "Fermer",
        "loading_text": "Chargement...",
        "error_text": "Erreur",
    },
    "pt-BR": {
        "lang_desc": "Código do idioma para o texto do calendário",
        "year_desc": "Ano da temporada para uma corrida específica",
        "round_desc": "Número da rodada (1-24) para uma corrida específica",
        "tz_desc": "Fuso horário para os horários do cronograma (formato IANA)",
        "display_desc": "Modo de saída para telas 1bit, B/W/R, B/W/R/Y ou Spectra 6",
        "weather_desc": "Ativa ou desativa a sobreposição de clima",
        "weather_type_desc": "Qual variante de dados meteorológicos renderizar",
        "calendar_desc": (
            "Gera um calendário de F1 como imagem BMP (800×480) "
            "para telas E-Ink 1bit, 4-bit B/W/R, 4-bit B/W/R/Y e Spectra 6."
        ),
        "eg": "ex.",
        "dimensions_label": "Dimensões",
        "color_depth_label": "Profundidade de cor",
        "races_desc": "Lista de todas as corridas de uma temporada",
        "race_desc": "Informações detalhadas da corrida, incluindo cronograma",
        "stats_desc": "Estatísticas de requisições (última hora e 24 h)",
        "health_desc": "Verificação de saúde do serviço",
        "json_api_desc": "Documentação da API em formato JSON",
        "laskakit_title": "Para LaskaKit / zivyobraz.eu:",
        "laskakit_step1": "No zivyobraz.eu selecione como fonte de conteúdo: URL com imagem",
        "laskakit_step2": "Cole a URL",
        "laskakit_step3": "Defina o intervalo de atualização para 1-6 horas",
        "close_btn": "Fechar",
        "loading_text": "Carregando...",
        "error_text": "Erro",
    },
    "nl": {
        "lang_desc": "Taalcode voor kalendertekst",
        "year_desc": "Seizoensjaar voor een specifieke race",
        "round_desc": "Rondenummer (1-24) voor een specifieke race",
        "tz_desc": "Tijdzone voor tijden in het schema (IANA-formaat)",
        "display_desc": "Uitvoermodus voor 1bit-, B/W/R-, B/W/R/Y- of Spectra 6-displays",
        "weather_desc": "Weeroverlay in- of uitschakelen",
        "weather_type_desc": "Welke variant van weergegevens gerenderd moet worden",
        "calendar_desc": (
            "Genereert een F1-kalender als BMP-afbeelding (800×480) "
            "voor 1bit-, 4-bit B/W/R-, 4-bit B/W/R/Y- en Spectra 6 E-Ink-displays."
        ),
        "eg": "bijv.",
        "dimensions_label": "Afmetingen",
        "color_depth_label": "Kleurdiepte",
        "races_desc": "Lijst van alle races voor een seizoen",
        "race_desc": "Gedetailleerde race-informatie inclusief schema",
        "stats_desc": "Aanvraagstatistieken (laatste uur en 24 u)",
        "health_desc": "Service health-check",
        "json_api_desc": "API-documentatie in JSON-formaat",
        "laskakit_title": "Voor LaskaKit / zivyobraz.eu:",
        "laskakit_step1": "Selecteer in zivyobraz.eu als contentbron: URL met afbeelding",
        "laskakit_step2": "Plak de URL",
        "laskakit_step3": "Stel het vernieuwingsinterval in op 1-6 uur",
        "close_btn": "Sluiten",
        "loading_text": "Laden...",
        "error_text": "Fout",
    },
    "pl": {
        "lang_desc": "Kod języka dla tekstu kalendarza",
        "year_desc": "Rok sezonu dla konkretnego wyścigu",
        "round_desc": "Numer rundy (1-24) dla konkretnego wyścigu",
        "tz_desc": "Strefa czasowa dla godzin harmonogramu (format IANA)",
        "display_desc": "Tryb wyjścia dla ekranów 1bit, B/W/R, B/W/R/Y lub Spectra 6",
        "weather_desc": "Włącz lub wyłącz nakładkę pogody",
        "weather_type_desc": "Który wariant danych pogodowych wyrenderować",
        "calendar_desc": (
            "Generuje kalendarz F1 jako obraz BMP (800×480) "
            "dla wyświetlaczy E-Ink 1bit, 4-bit B/W/R, 4-bit B/W/R/Y i Spectra 6."
        ),
        "eg": "np.",
        "dimensions_label": "Wymiary",
        "color_depth_label": "Głębia kolorów",
        "races_desc": "Lista wszystkich wyścigów danego sezonu",
        "race_desc": "Szczegółowe informacje o wyścigu wraz z harmonogramem",
        "stats_desc": "Statystyki żądań (ostatnia godzina i 24 h)",
        "health_desc": "Kontrola stanu usługi",
        "json_api_desc": "Dokumentacja API w formacie JSON",
        "laskakit_title": "Dla LaskaKit / zivyobraz.eu:",
        "laskakit_step1": "W zivyobraz.eu wybierz jako źródło treści: URL z obrazem",
        "laskakit_step2": "Wklej URL",
        "laskakit_step3": "Ustaw interwał odświeżania na 1-6 godzin",
        "close_btn": "Zamknij",
        "loading_text": "Ładowanie...",
        "error_text": "Błąd",
    },
    "tr": {
        "lang_desc": "Takvim metni için dil kodu",
        "year_desc": "Belirli yarış için sezon yılı",
        "round_desc": "Belirli yarış için tur numarası (1-24)",
        "tz_desc": "Takvim saatleri için saat dilimi (IANA biçimi)",
        "display_desc": "1bit, B/W/R, B/W/R/Y veya Spectra 6 ekranlar için çıktı modu",
        "weather_desc": "Hava durumu katmanını aç veya kapat",
        "weather_type_desc": "Hangi hava durumu verisi varyantının çizileceği",
        "calendar_desc": (
            "F1 takvimini BMP görseli (800×480) olarak üretir "
            "ve 1bit, 4-bit B/W/R, 4-bit B/W/R/Y ve Spectra 6 E-Ink ekranları destekler."
        ),
        "eg": "örn.",
        "dimensions_label": "Boyutlar",
        "color_depth_label": "Renk derinliği",
        "races_desc": "Belirli bir sezon için tüm yarışların listesi",
        "race_desc": "Takvim dahil ayrıntılı yarış bilgisi",
        "stats_desc": "İstek istatistikleri (son saat ve 24 saat)",
        "health_desc": "Servis sağlık kontrolü",
        "json_api_desc": "JSON formatında API dokümantasyonu",
        "laskakit_title": "LaskaKit / zivyobraz.eu için:",
        "laskakit_step1": "zivyobraz.eu içinde içerik kaynağı olarak resim URL'si seçin",
        "laskakit_step2": "URL'yi yapıştırın",
        "laskakit_step3": "Yenileme aralığını 1-6 saat olarak ayarlayın",
        "close_btn": "Kapat",
        "loading_text": "Yükleniyor...",
        "error_text": "Hata",
    },
    "ja": {
        "lang_desc": "カレンダーテキスト用の言語コード",
        "year_desc": "特定レース用のシーズン年",
        "round_desc": "特定レース用のラウンド番号 (1-24)",
        "tz_desc": "スケジュール時刻用のタイムゾーン (IANA 形式)",
        "display_desc": "1bit、B/W/R、B/W/R/Y、Spectra 6 ディスプレイ用の出力モード",
        "weather_desc": "天気オーバーレイの有効化または無効化",
        "weather_type_desc": "描画する天気データの種類",
        "calendar_desc": (
            "F1 カレンダーを BMP 画像 (800×480) として生成し、"
            "1bit、4-bit B/W/R、4-bit B/W/R/Y、Spectra 6 の E-Ink ディスプレイに対応します。"
        ),
        "eg": "例",
        "dimensions_label": "サイズ",
        "color_depth_label": "色深度",
        "races_desc": "指定シーズンの全レース一覧",
        "race_desc": "スケジュールを含む詳細なレース情報",
        "stats_desc": "リクエスト統計 (直近1時間と24時間)",
        "health_desc": "サービス稼働状況チェック",
        "json_api_desc": "JSON 形式の API ドキュメント",
        "laskakit_title": "LaskaKit / zivyobraz.eu 向け:",
        "laskakit_step1": "zivyobraz.eu でコンテンツソースとして画像 URL を選択",
        "laskakit_step2": "URL を貼り付け",
        "laskakit_step3": "更新間隔を 1〜6 時間に設定",
        "close_btn": "閉じる",
        "loading_text": "読み込み中...",
        "error_text": "エラー",
    },
    "zh-CN": {
        "lang_desc": "日历文本的语言代码",
        "year_desc": "指定比赛的赛季年份",
        "round_desc": "指定比赛的轮次编号 (1-24)",
        "tz_desc": "赛程时间使用的时区 (IANA 格式)",
        "display_desc": "适用于 1bit、B/W/R、B/W/R/Y 或 Spectra 6 显示器的输出模式",
        "weather_desc": "启用或禁用天气叠加层",
        "weather_type_desc": "要渲染的天气数据类型",
        "calendar_desc": (
            "将 F1 赛历生成为 BMP 图片 (800×480)，"
            "适用于 1bit、4-bit B/W/R、4-bit B/W/R/Y 和 Spectra 6 E-Ink 显示器。"
        ),
        "eg": "例如",
        "dimensions_label": "尺寸",
        "color_depth_label": "色深",
        "races_desc": "指定赛季的全部比赛列表",
        "race_desc": "包含赛程的详细比赛信息",
        "stats_desc": "请求统计（最近 1 小时和 24 小时）",
        "health_desc": "服务健康检查",
        "json_api_desc": "JSON 格式的 API 文档",
        "laskakit_title": "适用于 LaskaKit / zivyobraz.eu：",
        "laskakit_step1": "在 zivyobraz.eu 中选择内容来源：图片 URL",
        "laskakit_step2": "粘贴 URL",
        "laskakit_step3": "将刷新间隔设置为 1-6 小时",
        "close_btn": "关闭",
        "loading_text": "加载中...",
        "error_text": "错误",
    },
}


def _build_english_code_examples() -> dict[str, str]:
    curl_comment1 = "# Download next race calendar"
    curl_comment2 = "# With Czech language and timezone"
    curl_comment3 = "# Specific race (year and round)"
    curl_comment4 = "# B/W/R/Y output for four-color E-Ink"
    python_docstring = "Download F1 calendar as BMP image."
    python_print = "Calendar saved as calendar.bmp"
    python_usage = "# Usage"
    js_comment1 = "// Fetch and display calendar"
    js_comment2 = "// Display in img element"
    js_comment3 = "// Download as file"

    return {
        "code_curl": (
            f"{curl_comment1}\n"
            'curl -o calendar.bmp "https://f1-eink.example.com/calendar.bmp"\n\n'
            f"{curl_comment2}\n"
            'curl -o calendar.bmp "https://f1-eink.example.com/calendar.bmp?lang=cs&tz=Europe/Prague"\n\n'
            f"{curl_comment3}\n"
            'curl -o calendar.bmp "https://f1-eink.example.com/calendar.bmp?year=2025&round=5"\n\n'
            f"{curl_comment4}\n"
            'curl -o calendar.bmp "https://f1-eink.example.com/calendar.bmp?display=bwry"'
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
    }


def _build_czech_code_examples() -> dict[str, str]:
    curl_comment1 = "# Stáhnout kalendář dalšího závodu"
    curl_comment2 = "# S českým jazykem a časovým pásmem"
    curl_comment3 = "# Konkrétní závod (rok a kolo)"
    curl_comment4 = "# B/W/R/Y varianta pro černo-bílo-červeno-žlutý e-paper"
    python_docstring = "Stáhne F1 kalendář jako BMP obrázek."
    python_print = "Kalendář uložen jako calendar.bmp"
    python_usage = "# Použití"
    js_comment1 = "// Načíst a zobrazit kalendář"
    js_comment2 = "// Zobrazit v img elementu"
    js_comment3 = "// Stáhnout jako soubor"

    return {
        "code_curl": (
            f"{curl_comment1}\n"
            'curl -o calendar.bmp "https://f1-eink.example.com/calendar.bmp"\n\n'
            f"{curl_comment2}\n"
            'curl -o calendar.bmp "https://f1-eink.example.com/calendar.bmp?lang=cs&tz=Europe/Prague"\n\n'
            f"{curl_comment3}\n"
            'curl -o calendar.bmp "https://f1-eink.example.com/calendar.bmp?year=2025&round=5"\n\n'
            f"{curl_comment4}\n"
            'curl -o calendar.bmp "https://f1-eink.example.com/calendar.bmp?display=bwry"'
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
    }


def build_api_docs_context(ui_lang: str) -> dict[str, Any]:
    """Build localized strings and code snippets for api_docs.html."""
    texts = API_DOCS_TEXTS.get(ui_lang, API_DOCS_TEXTS["en"])
    code_examples = (
        _build_czech_code_examples() if ui_lang == "cs" else _build_english_code_examples()
    )
    return {**code_examples, **texts}
