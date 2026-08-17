from __future__ import annotations

from aiohttp.test_utils import TestClient, TestServer

from conftest import TEST_KEY_HEX
from ha_smartmeter.status import Status
from ha_smartmeter.web.server import INDEX, build_app


async def client_for(status: Status) -> TestClient:
    client = TestClient(TestServer(build_app(status)))
    await client.start_server()
    return client


class TestStatusPage:
    async def test_the_page_is_served(self):
        client = await client_for(Status())
        try:
            response = await client.get("/")
            assert response.status == 200
            assert response.content_type == "text/html"
            assert "Smart meter" in await response.text()
        finally:
            await client.close()

    async def test_the_json_endpoint_reflects_the_status(self):
        status = Status(profile_name="TINETZ", profile_status="documented", state="reading")
        status.values = {"active_power_plus": 412}
        client = await client_for(status)
        try:
            body = await (await client.get("/api/status")).json()
        finally:
            await client.close()
        assert body["state"] == "reading"
        assert body["profile"]["status"] == "documented"
        assert body["values"]["active_power_plus"] == 412

    async def test_the_page_never_shows_the_key(self):
        status = Status()
        status.last_error = "irrelevant"
        client = await client_for(status)
        try:
            assert TEST_KEY_HEX not in await (await client.get("/api/status")).text()
        finally:
            await client.close()

    def test_the_page_uses_relative_urls_so_ingress_works(self):
        # An absolute "/api/status" would leave the ingress path prefix and hit
        # Home Assistant itself, which returns the frontend and not our JSON.
        html = INDEX.read_text(encoding="utf-8")
        assert 'fetch("api/status"' in html
        assert 'fetch("/api/status"' not in html

    def test_the_page_needs_nothing_from_the_internet(self):
        # Ingress pages load inside Home Assistant, often on a network with no
        # route out. A CDN reference would leave a blank page.
        html = INDEX.read_text(encoding="utf-8")
        assert "http://" not in html
        assert "https://" not in html
