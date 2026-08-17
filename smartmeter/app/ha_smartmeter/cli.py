"""Entry point.

`run` is what the add-on does. The other two subcommands exist for the times
when something is wrong and somebody has a capture file: `decode` runs a capture
through the full stack on a laptop and prints what came out, and `profiles`
lists what is shipped. Neither needs Home Assistant, a broker or an adapter.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
import sys
from pathlib import Path

from . import __version__, suppliers
from .config import MqttSettings, Options
from .decoder import Decoder
from .dlms.crypto import parse_key
from .errors import SmartmeterError
from .logging_setup import configure
from .runner import Runner
from .status import Status
from .transport.replay_source import parse_hex_file
from .web.server import DEFAULT_PORT, StatusServer

_LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ha-smartmeter", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="read the meter and publish to MQTT (the default)")
    run.add_argument("--options", help="path to options.json, for running outside Supervisor")
    run.add_argument("--no-web", action="store_true", help="do not start the status page")
    run.add_argument("--web-port", type=int, default=DEFAULT_PORT)

    decode = sub.add_parser("decode", help="decode a capture file and print the readings")
    decode.add_argument("file", help="capture file, one frame per line as hex")
    decode.add_argument("--key", required=True, help="32 hex characters")
    decode.add_argument("--auth-key", default="", help="only if your operator issued a second key")
    decode.add_argument("--supplier", default="generic-ksm-west")

    sub.add_parser("profiles", help="list the grid operator profiles that ship with this add-on")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command or "run"
    try:
        if command == "profiles":
            return _profiles()
        if command == "decode":
            return _decode(args)
        return _run(args)
    except SmartmeterError as exc:
        # Configuration problems land here. One sentence of instruction first,
        # then the detail, because the log tab shows the last lines.
        configure("info")
        logging.getLogger("ha_smartmeter").error("%s", exc.hint)
        logging.getLogger("ha_smartmeter").error("Details: %s", exc)
        return 1
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        return 0


# ---------------------------------------------------------------------- run


def _run(args: argparse.Namespace) -> int:
    override = getattr(args, "options", None)
    options = Options.load(Path(override) if override else None)
    configure(options.log_level, secrets=options.secrets)
    status = Status()
    asyncio.run(_run_async(options, status, args))
    return 0


async def _run_async(options: Options, status: Status, args: argparse.Namespace) -> None:
    runner = Runner(options, MqttSettings.from_env(), status=status)
    _install_signal_handlers(runner)

    server = None
    if not getattr(args, "no_web", False):
        server = StatusServer(status, port=getattr(args, "web_port", DEFAULT_PORT))
        try:
            await server.start()
        except OSError as exc:
            # The page is a convenience. Losing it must not stop the meter being
            # read, which is the thing the user actually installed this for.
            _LOGGER.warning("Status page could not start (%s), carrying on without it", exc)
            server = None
    try:
        await runner.run()
    finally:
        if server is not None:
            await server.stop()


def _install_signal_handlers(runner: Runner) -> None:
    loop = asyncio.get_running_loop()
    for name in ("SIGTERM", "SIGINT"):
        sig = getattr(signal, name, None)
        if sig is None:  # pragma: no cover - Windows
            continue
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, runner.request_stop)


# ------------------------------------------------------------------- decode


def _decode(args: argparse.Namespace) -> int:
    configure("info", secrets=[args.key, args.auth_key])
    profile = suppliers.get(args.supplier)
    decoder = Decoder(
        profile=profile,
        key=parse_key(args.key),
        auth_key=parse_key(args.auth_key, what="authentication key") if args.auth_key else None,
    )
    with open(args.file, encoding="utf-8") as handle:
        frames = parse_hex_file(handle.read())

    telegrams = decoder.feed(b"".join(frames))
    print(f"{len(frames)} frames, {len(telegrams)} telegrams, profile {profile.name}")
    for index, telegram in enumerate(telegrams, start=1):
        print(f"\nTelegram {index}: frame counter {telegram.frame_counter}")
        print(f"  system title  {telegram.system_title.hex()}")
        print(f"  timestamp     {telegram.timestamp}")
        for key, reading in telegram.readings.items():
            print(f"  {key:<24} {reading.value} {reading.unit or ''}".rstrip())
    if not telegrams:
        print(f"\nNothing decoded. {decoder.stats.last_error_hint}")
        return 1
    return 0


# ------------------------------------------------------------------ profiles


def _profiles() -> int:
    for profile in sorted(suppliers.load_all().values(), key=lambda p: p.name):
        print(f"{profile.id:<28} {profile.status:<11} {profile.name}")
        if profile.notes:
            print(f"{'':<28} {profile.notes}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
