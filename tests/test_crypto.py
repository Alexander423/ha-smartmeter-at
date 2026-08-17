from __future__ import annotations

import pytest

from conftest import TEST_KEY, TEST_KEY_HEX, WRONG_KEY
from ha_smartmeter.dlms.apdu import build_ciphered_apdu, parse_ciphered_apdu
from ha_smartmeter.dlms.crypto import decrypt, encrypt, parse_key
from ha_smartmeter.errors import ConfigError, DecryptionError, ParseError

SYSTEM_TITLE = b"\x53\x41\x47\x01\x02\x03\x04\x05"
PLAINTEXT = b"\x0f\x00\x00\x00\x01\x00\x02\x01\x11\x2a"


class TestParseKey:
    def test_accepts_the_documented_form(self):
        assert parse_key(TEST_KEY_HEX) == TEST_KEY

    def test_accepts_lower_case_and_separators(self):
        assert parse_key(TEST_KEY_HEX.lower()) == TEST_KEY
        assert parse_key(" ".join(TEST_KEY_HEX[i : i + 2] for i in range(0, 32, 2))) == TEST_KEY

    @pytest.mark.parametrize(
        "text, hint_fragment",
        [
            ("", "Enter the key"),
            ("zzzz", "0-9 and A-F"),
            ("00112233445566778899AABBCCDDEE", "32 characters"),
            (TEST_KEY_HEX + "00", "32 characters"),
        ],
    )
    def test_rejects_bad_keys_with_a_usable_hint(self, text, hint_fragment):
        with pytest.raises(ConfigError) as excinfo:
            parse_key(text)
        assert hint_fragment in excinfo.value.hint


class TestApduRoundTrip:
    def build(self, security_control=0x21, frame_counter=42, auth_key=None):
        ciphertext, tag = encrypt(
            SYSTEM_TITLE, frame_counter, security_control, PLAINTEXT, TEST_KEY, auth_key
        )
        return build_ciphered_apdu(SYSTEM_TITLE, security_control, frame_counter, ciphertext, tag)

    def test_encryption_only_has_no_tag(self):
        apdu = parse_ciphered_apdu(self.build(), expected_security_control=0x21)
        assert apdu.system_title == SYSTEM_TITLE
        assert apdu.frame_counter == 42
        assert apdu.tag is None
        assert apdu.iv == SYSTEM_TITLE + (42).to_bytes(4, "big")
        assert apdu.is_encrypted and not apdu.is_authenticated
        assert decrypt(apdu, TEST_KEY) == (PLAINTEXT, False)

    def test_authenticated_message_verifies_its_tag(self):
        apdu = parse_ciphered_apdu(self.build(security_control=0x31))
        assert apdu.tag is not None and len(apdu.tag) == 12
        assert decrypt(apdu, TEST_KEY) == (PLAINTEXT, True)

    def test_authenticated_message_with_a_separate_auth_key(self):
        auth_key = bytes(range(16))
        apdu = parse_ciphered_apdu(self.build(security_control=0x31, auth_key=auth_key))
        assert decrypt(apdu, TEST_KEY, auth_key) == (PLAINTEXT, True)

    def test_a_tampered_tag_is_reported_not_ignored(self):
        auth_key = bytes(range(16))
        raw = bytearray(self.build(security_control=0x31, auth_key=auth_key))
        raw[-1] ^= 0xFF
        apdu = parse_ciphered_apdu(bytes(raw))
        with pytest.raises(DecryptionError) as excinfo:
            decrypt(apdu, TEST_KEY, auth_key)
        assert "authentication key" in excinfo.value.hint

    def test_wrong_key_produces_garbage_rather_than_an_exception(self):
        # Without a tag there is nothing to check here, so the wrong key is
        # caught one layer up by the telegram parser. What must not happen is a
        # crash or a silent success.
        apdu = parse_ciphered_apdu(self.build())
        plaintext, authenticated = decrypt(apdu, WRONG_KEY)
        assert plaintext != PLAINTEXT
        assert authenticated is False

    def test_two_byte_length_field_for_a_long_apdu(self):
        long_plaintext = b"\x0f" + b"\x5a" * 400
        ciphertext, _ = encrypt(SYSTEM_TITLE, 1, 0x21, long_plaintext, TEST_KEY)
        raw = build_ciphered_apdu(SYSTEM_TITLE, 0x21, 1, ciphertext)
        assert raw[10] == 0x82  # long form length
        apdu = parse_ciphered_apdu(raw)
        assert decrypt(apdu, TEST_KEY)[0] == long_plaintext

    def test_frame_counter_before_the_security_control_byte(self):
        ciphertext, _ = encrypt(SYSTEM_TITLE, 7, 0x21, PLAINTEXT, TEST_KEY)
        body = (7).to_bytes(4, "big") + b"\x21" + ciphertext
        raw = b"\xdb\x08" + SYSTEM_TITLE + bytes([len(body)]) + body
        apdu = parse_ciphered_apdu(raw, expected_security_control=0x21, header_order="auto")
        assert apdu.frame_counter == 7
        assert apdu.security_control == 0x21
        assert decrypt(apdu, TEST_KEY)[0] == PLAINTEXT


class TestApduRejection:
    @pytest.mark.parametrize(
        "raw, message",
        [
            (b"\xcc\x08" + b"\x00" * 20, "general-glo-ciphering"),
            (b"\x00" * 4, "too short"),
            (b"\xdb\x04" + b"\x00" * 20, "system title is 4 bytes"),
            (b"\xdb\x08" + SYSTEM_TITLE + b"\x05\x21\x00\x00\x00\x00", "no ciphertext"),
        ],
    )
    def test_malformed_apdus_are_rejected(self, raw, message):
        with pytest.raises(ParseError, match=message):
            parse_ciphered_apdu(raw)

    def test_declared_length_longer_than_the_message_is_rejected(self):
        raw = b"\xdb\x08" + SYSTEM_TITLE + b"\x50" + b"\x21" + b"\x00" * 8
        with pytest.raises(ParseError, match="declares"):
            parse_ciphered_apdu(raw)
