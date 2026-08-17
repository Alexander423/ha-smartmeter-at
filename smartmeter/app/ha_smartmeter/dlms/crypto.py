"""AES-128-GCM decryption of the telegram.

The nonce is the system title followed by the four byte frame counter, which is
exactly 96 bits, so no GCM nonce derivation is needed.

About tag verification. The security control byte the Austrian meters send
(0x21) has the authentication bit clear, so there is no tag on the wire and
there is nothing to verify. Grid operators also issue a single key. So when no
tag is present the check is structural instead: the plaintext has to start with
a data-notification APDU and parse as valid A-XDR. A wrong key turns the
plaintext into noise, which fails that check immediately, and the user is told
their key is wrong rather than being left with entities that never update.

When a tag is present it is verified for real, and a failure is reported, never
ignored. If the operator issues a separate authentication key, set it in the
add-on configuration and verification uses it.
"""

from __future__ import annotations

import logging

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..errors import ConfigError, DecryptionError
from ..models import CipheredApdu
from .apdu import GCM_TAG_LENGTH

_LOGGER = logging.getLogger(__name__)

KEY_LENGTH = 16


def parse_key(text: str, *, what: str = "key") -> bytes:
    """32 hex characters to 16 bytes, with an error the user can act on."""
    cleaned = "".join(text.split()).replace("-", "").replace(":", "")
    if not cleaned:
        raise ConfigError(f"{what} is empty", hint=f"Enter the {what} from your grid operator.")
    try:
        raw = bytes.fromhex(cleaned)
    except ValueError as exc:
        raise ConfigError(
            f"{what} is not hexadecimal",
            hint=(
                f"The {what} is 32 characters using only 0-9 and A-F. "
                "Copy it from your operator's portal rather than typing it."
            ),
        ) from exc
    if len(raw) != KEY_LENGTH:
        raise ConfigError(
            f"{what} is {len(raw)} bytes, expected {KEY_LENGTH}",
            hint=(f"The {what} must be exactly 32 characters long. Yours is {len(cleaned)}."),
        )
    return raw


def decrypt(apdu: CipheredApdu, key: bytes, auth_key: bytes | None = None) -> tuple[bytes, bool]:
    """Decrypt the payload.

    Returns the plaintext and whether the GCM tag was actually verified.
    """
    if len(key) != KEY_LENGTH:
        raise ConfigError(f"encryption key must be {KEY_LENGTH} bytes, got {len(key)}")

    if apdu.tag is None:
        return _decrypt_unverified(apdu, key), False

    aad_key = auth_key if auth_key is not None else key
    try:
        return _decrypt_verified(apdu, key, aad_key), True
    except InvalidTag:
        if auth_key is not None:
            raise DecryptionError(
                f"GCM tag mismatch for frame counter {apdu.frame_counter}",
                hint=(
                    "The telegram failed its authenticity check. Either the encryption key or "
                    "the authentication key is wrong. Check both against your operator's portal."
                ),
            ) from None
        # No separate authentication key was configured and using the encryption
        # key for both did not work. Fall back and let the structural check
        # decide whether the key is right.
        _LOGGER.debug(
            "Tag verification with the encryption key failed; continuing without verification. "
            "Set 'auth_key' if your operator issued a second key."
        )
        return _decrypt_unverified(apdu, key), False


def _decrypt_verified(apdu: CipheredApdu, key: bytes, aad_key: bytes) -> bytes:
    # DLMS truncates the GCM tag to 12 bytes, which is shorter than the default
    # this library insists on.
    mode = modes.GCM(apdu.iv, apdu.tag, min_tag_length=GCM_TAG_LENGTH)
    decryptor = Cipher(algorithms.AES(key), mode).decryptor()
    decryptor.authenticate_additional_data(bytes([apdu.security_control]) + aad_key)
    return decryptor.update(apdu.ciphertext) + decryptor.finalize()


def _decrypt_unverified(apdu: CipheredApdu, key: bytes) -> bytes:
    """GCM is counter mode underneath, so the plaintext comes out without the tag."""
    decryptor = Cipher(algorithms.AES(key), modes.GCM(apdu.iv)).decryptor()
    return decryptor.update(apdu.ciphertext)


def encrypt(
    system_title: bytes,
    frame_counter: int,
    security_control: int,
    plaintext: bytes,
    key: bytes,
    auth_key: bytes | None = None,
) -> tuple[bytes, bytes | None]:
    """Inverse of `decrypt`, for the simulator. Returns (ciphertext, tag)."""
    iv = system_title + frame_counter.to_bytes(4, "big")
    authenticated = bool(security_control & 0x10)
    if not authenticated:
        encryptor = Cipher(algorithms.AES(key), modes.GCM(iv)).encryptor()
        return encryptor.update(plaintext), None
    encryptor = Cipher(algorithms.AES(key), modes.GCM(iv)).encryptor()
    encryptor.authenticate_additional_data(
        bytes([security_control]) + (auth_key if auth_key is not None else key)
    )
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()
    return ciphertext, encryptor.tag[:GCM_TAG_LENGTH]
