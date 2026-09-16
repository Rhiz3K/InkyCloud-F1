# Data and artwork licences

The MIT software licence does not cover the datasets and artwork bundled with the
application. A generated calendar is a collection of separately licensed material;
source notices do not grant rights to unrelated third-party content or trademarks.

| Material | Source / authors | Licence and changes |
| --- | --- | --- |
| Calendars, race results, standings, live API/cache snapshots | [Jolpica-F1 / Ergast contributors](https://github.com/jolpica/jolpica-f1) | [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/). Selected fields, normalization, formatting, timezone conversion, retained cancelled races. [Terms](https://github.com/jolpica/jolpica-f1/blob/main/TERMS.md), reviewed 2026-09-09. |
| Supplementary circuit facts: length, laps, distance, first GP and lap record | Legacy project snapshot linked per circuit in `circuits_data.json`; upstream F1.com import. Current Madring values manually checked against the [2026 event page](https://www.formula1.com/en/racing/2026/spain) on 2026-09-11. | `LicenseRef-Legacy-Circuit-Facts-Unverified`: no open licence or written database redistribution permission is recorded. The original import revision was not recorded. Jolpica's licence applies to the historical results, not these supplementary fields. |
| 2025 team technical/entry table | [Wikipedia contributors, 2025 Formula One World Championship](https://en.wikipedia.org/wiki/2025_Formula_One_World_Championship); [author history](https://en.wikipedia.org/w/index.php?title=2025_Formula_One_World_Championship&action=history) | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Selected table cells, normalized JSON, manual corrections. Original import revision was not recorded; no retrospective revision is asserted. |
| 2026 team technical/entry table | [Wikipedia contributors, 2026 Formula One World Championship](https://en.wikipedia.org/wiki/2026_Formula_One_World_Championship); [author history](https://en.wikipedia.org/w/index.php?title=2026_Formula_One_World_Championship&action=history) | CC BY-SA 4.0, same extraction changes and original revision limitation as above. |
| Weather | [Open-Meteo](https://open-meteo.com/) | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Rounded temperatures, selected times/fields, converted precipitation display. Free API use is noncommercial and subject to [separate limits](https://open-meteo.com/en/terms). |
| Track outlines and adaptations | Jules Roy and individual Commons authors | Per-file author, original source, licence and adaptation in `/credits`, `/api/tracks/{id}` and `artwork/open-tracks`. ShareAlike adaptations retain the applicable licence. |
| Flags | [Flagpedia / Flagcdn](https://flagpedia.net/download/icons) | Offered as public domain; resized and converted to e-paper palettes/patterns. |
| Original project car, web header and favicon compositions | Project maintainer / contributors; exact files and historical Git source links in `app/assets/asset-register.json` | `LicenseRef-Project-AI-Artwork`: the car is reported as AI-generated. Model, prompt and generation terms are not recorded. This identifier records provenance; it does not assert exclusive copyright or grant third-party trademark rights. |
| Team logos (12 teams, colour and monochrome variants) | Respective teams / trademark owners; original graphic authors not recorded; archived project files linked individually in the asset register | `LicenseRef-Team-Logo-Unverified`: used for team identification. No written redistribution permission is recorded. Not covered by the MIT software licence or the track/data licences. |

Keep each component's source and licence with redistribution. Do not flatten the wiki
CC BY-SA component and Jolpica CC BY-NC-SA component into a single dataset claimed to
have only one of those licences. The software and its MIT licence remain separate.
No general permission for commercial redistribution of the Jolpica data is offered.

Season JSON files contain `_provenance`; their exact reviewed bytes are recorded in
`app/assets/asset-register.json`. The old F1 circuit artwork, driver graphics and source
import commands have been retired. The maintainer's photographs of physical displays
in the README are retained as historical examples. Team logos and original
project branding are retained as described above; a successful asset inventory check
verifies the recorded files and hashes, not legal permission to use every item.
Supplementary facts are available for the 25 circuits in the legacy snapshot; Sepang
remains an additional artwork circuit without these facts. Missing records are not
invented. Each circuit's `_provenance.scope` limits the Jolpica notice to `historical`;
`_provenance.supplementary` records the separate factual source and its unresolved rights.
The old F1 scraper remains retired. Sporting history continues to refresh from Jolpica.

Old public release assets, Git history and remote caches are separate migration work;
removing a file from this working tree does not remove copies already distributed.
