"""Logging, with the key redacted.

The add-on log is a text box in the Home Assistant UI that users copy and paste
into issues, so two things matter: it has to read like sentences, and the AES
key must never appear in it. Redaction is done in the formatter rather than in a
filter, so it covers the message, its arguments and any exception text, and
there is no path that writes a log line without going through it.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable

REDACTED = "***REDACTED***"

#: bashio uses these names, so the add-on option does too.
_LEVELS = {
    "trace": logging.DEBUG,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "notice": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "fatal": logging.CRITICAL,
}


class RedactingFormatter(logging.Formatter):
    """Formats a record, then removes anything secret from the result."""

    def __init__(self, fmt: str, datefmt: str | None = None) -> None:
        super().__init__(fmt, datefmt)
        self._secrets: list[str] = []

    def set_secrets(self, secrets: Iterable[str]) -> None:
        """Register strings to redact. Both cases are covered."""
        collected: list[str] = []
        for secret in secrets:
            if len(secret) < 8:
                # Too short to be a key, and redacting it would mangle ordinary
                # words in the log.
                continue
            collected += [secret, secret.lower(), secret.upper()]
        self._secrets = sorted(set(collected), key=len, reverse=True)

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text


_FORMATTER = RedactingFormatter("[%(asctime)s] %(levelname)s: %(message)s", "%H:%M:%S")
_DEBUG_FORMATTER = RedactingFormatter(
    "[%(asctime)s] %(levelname)s %(name)s: %(message)s", "%H:%M:%S"
)


def configure(level: str = "info", secrets: Iterable[str] = ()) -> None:
    """Set up the root logger. Safe to call more than once."""
    resolved = _LEVELS.get(level.lower(), logging.INFO)
    formatter = _DEBUG_FORMATTER if resolved <= logging.DEBUG else _FORMATTER
    formatter.set_secrets(secrets)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(resolved)

    # These are chatty at DEBUG and none of it helps anybody read a meter.
    logging.getLogger("asyncio").setLevel(max(resolved, logging.INFO))
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)


def redact(text: str, secrets: Iterable[str]) -> str:
    """Used by capture mode, which writes files rather than log lines."""
    for secret in secrets:
        if len(secret) < 8:
            continue
        for variant in (secret, secret.lower(), secret.upper()):
            text = text.replace(variant, REDACTED)
    return text
