"""Error types.

Every error carries a `hint`: one sentence telling the user what to do next.
Internal detail belongs in `args[0]`, which goes to the log at DEBUG. The hint
goes to the log at ERROR and to the ingress status page. If you add an error
here and cannot write a useful hint, the error is probably not worth raising
separately.
"""

from __future__ import annotations


class SmartmeterError(Exception):
    """Base class. `hint` is shown to the user, the message is for the log."""

    hint = "Check the add-on log for details."

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        if hint is not None:
            self.hint = hint


class ConfigError(SmartmeterError):
    hint = "Fix the add-on configuration and restart the add-on."


class SerialUnavailableError(SmartmeterError):
    hint = (
        "The serial device is not there. Unplug and replug the M-Bus adapter, "
        "then pick the device again in the add-on configuration."
    )


class FrameError(SmartmeterError):
    """A single M-Bus frame was malformed. Recoverable, we resynchronise."""

    hint = (
        "A telegram arrived damaged. A few of these per hour are normal on long cables; "
        "a constant stream means the baud rate or parity setting does not match your meter."
    )


class ReassemblyError(FrameError):
    hint = (
        "A multi-part telegram arrived incomplete. If this repeats, the connection to the "
        "meter is dropping bytes."
    )


class DecryptionError(SmartmeterError):
    hint = (
        "The telegram could not be decrypted. The key is almost certainly wrong. "
        "Check for typos, and make sure you used the key your grid operator issued for "
        "this meter number."
    )


class KeyMismatchError(DecryptionError):
    hint = (
        "Decryption produced data that is not a valid meter telegram, which means the key "
        "does not belong to this meter. Copy the 32-character key from your operator's "
        "portal again, without spaces."
    )


class ParseError(SmartmeterError):
    hint = (
        "The telegram decrypted but its contents were not understood. Enable 'capture_raw' "
        "in the add-on configuration and open an issue with the capture file so the "
        "profile for your operator can be fixed."
    )


class ProfileError(ConfigError):
    hint = "Pick a different grid operator in the add-on configuration."
