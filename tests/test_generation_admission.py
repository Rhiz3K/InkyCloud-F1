"""Request-level coverage for image selection across scheduler refreshes."""

import asyncio

import pytest

from app.models import TeamEntry, TeamsData
from app.routes import images
from app.services.teams_service import TeamsService
from app.state import clear_bmp_cache
from app.utils.etag import strong_etag
from app.utils.render_limit import render_slot
from tests.test_routes_images import _request


@pytest.mark.asyncio
@pytest.mark.parametrize("queued", [False, True])
async def test_conditional_teams_waiter_uses_current_generation(monkeypatch, queued):
    selected = asyncio.Event()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []
    year = [2025]

    def select():
        selected.set()
        return year[0]

    async def roster(self, season):
        calls.append(season)
        if len(calls) == 1:
            started.set()
            await release.wait()
        return TeamsData(
            season=season, teams=[TeamEntry(constructor_name="Team")], standings_complete=False
        )

    async def draw(fn):
        return str(fn.args[2].season).encode()

    async def request(etag=None):
        return await images.get_teams_bmp(
            _request({"If-None-Match": etag} if etag else {}),
            lang="en",
            year=None,
            tz=None,
            display="1bit",
        )

    monkeypatch.setattr(images, "get_default_teams_year", select)
    monkeypatch.setattr(images, "_get_pregenerated_teams_path", lambda **_: None)
    monkeypatch.setattr(images, "enforce_rate_limit", lambda *a, **kw: None)
    monkeypatch.setattr(images, "_record_teams_api_call", lambda **kw: None)
    monkeypatch.setattr(images, "run_render", draw)
    monkeypatch.setattr(TeamsService, "get_teams_and_drivers", roster)
    monkeypatch.setattr(images.config, "RENDER_CONCURRENCY", 1)
    if queued:
        async with render_slot():
            first = asyncio.create_task(request())
            await selected.wait()
            clear_bmp_cache()
            year[0] = 2026
        release.set()
        assert (await first).body == b"2026"
        assert calls == [2026]
    else:
        first = asyncio.create_task(request())
        await started.wait()
        clear_bmp_cache()

        # Same season and source key. The next fetch sees a corrected roster.
        async def fresh(self, season):
            calls.append(season)
            return TeamsData(season=2026, teams=[TeamEntry(constructor_name="Team")])

        monkeypatch.setattr(TeamsService, "get_teams_and_drivers", fresh)
        second = asyncio.create_task(request(strong_etag(b"2025")))
        await asyncio.sleep(0)
        release.set()
        await first
        response = await second
        assert response.status_code == 200
        assert response.body == b"2026"
        assert calls == [2025, 2025]
