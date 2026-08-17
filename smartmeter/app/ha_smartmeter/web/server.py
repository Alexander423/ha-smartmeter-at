"""The ingress status page.

One page and one JSON endpoint. It exists for the fifteen minutes after somebody
first plugs the adapter in, when the useful question is "are frames arriving at
all, and if so what is wrong with them". The add-on log answers that too, but
not at a glance and not while it is happening.

The page fetches "api/status" with no leading slash, because ingress serves the
add-on under a path prefix that changes every session. An absolute path would
escape it and hit Home Assistant itself.
"""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web

from ..status import Status

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 8099
INDEX = Path(__file__).parent / "index.html"

STATUS = web.AppKey("status", Status)


def build_app(status: Status) -> web.Application:
    app = web.Application()
    app[STATUS] = status
    app.add_routes(
        [
            web.get("/", _index),
            web.get("/api/status", _status),
        ]
    )
    return app


async def _index(request: web.Request) -> web.Response:
    return web.Response(
        body=INDEX.read_bytes(),
        content_type="text/html",
        charset="utf-8",
        headers={"Cache-Control": "no-store"},
    )


async def _status(request: web.Request) -> web.Response:
    return web.json_response(request.app[STATUS].snapshot(), headers={"Cache-Control": "no-store"})


class StatusServer:
    """Runs the page alongside the read loop."""

    def __init__(self, status: Status, port: int = DEFAULT_PORT) -> None:
        self._status = status
        self._port = port
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        self._runner = web.AppRunner(build_app(self._status), access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await site.start()
        _LOGGER.info("Status page available in the add-on's Web UI tab")

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
