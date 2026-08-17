from __future__ import annotations

import pytest

from ha_smartmeter import suppliers
from ha_smartmeter.errors import ProfileError
from ha_smartmeter.obis import BY_OBIS

#: Every operator the add-on claims to support must have a profile.
EXPECTED = {
    "tinetz",
    "salzburg-netz",
    "ikb",
    "vorarlberger-energienetze",
    "evn-netz-noe",
    "kaernten-netz",
    "generic-ksm-west",
}


def test_all_advertised_profiles_exist():
    assert set(suppliers.load_all()) == EXPECTED


@pytest.mark.parametrize("profile_id", sorted(EXPECTED))
class TestEveryProfile:
    def test_loads(self, profile_id):
        assert suppliers.get(profile_id).id == profile_id

    def test_uses_the_austrian_physical_layer(self, profile_id):
        # 2400 8E1 is fixed by the specification and is the same for every
        # operator. A profile that changes it is almost certainly a mistake.
        serial = suppliers.get(profile_id).serial
        assert (serial.baudrate, serial.bytesize, serial.parity, serial.stopbits) == (
            2400,
            8,
            "E",
            1,
        )

    def test_expected_obis_codes_are_ones_the_add_on_publishes(self, profile_id):
        for code in suppliers.get(profile_id).expected_obis:
            assert code in BY_OBIS, f"{profile_id} expects {code}, which has no registry entry"

    def test_a_positional_profile_states_its_order(self, profile_id):
        profile = suppliers.get(profile_id)
        if profile.layout == "positional":
            assert profile.obis_order, "a positional profile without an order mislabels everything"

    def test_tells_the_user_where_to_get_the_key(self, profile_id):
        assert suppliers.get(profile_id).key_source

    def test_declares_an_honest_status(self, profile_id):
        assert suppliers.get(profile_id).status in ("verified", "documented", "assumed")


def test_nothing_claims_to_be_verified_yet():
    # None of these profiles has been run against a physical meter. When the
    # first one is, change its status and this test with it.
    assert [p.id for p in suppliers.load_all().values() if p.status == "verified"] == []


def test_tinetz_is_the_reference_profile():
    profile = suppliers.get("tinetz")
    assert profile.status == "documented"
    assert profile.tsap == (0x01, 0x67)
    assert profile.security_control == 0x21
    assert profile.layout == "obis_tagged"
    assert len(profile.expected_obis) == 15


def test_the_generic_profile_detects_rather_than_assumes():
    profile = suppliers.get("generic-ksm-west")
    assert profile.tsap == "auto"
    assert profile.header_order == "auto"
    assert profile.expected_obis == ()


def test_an_unknown_profile_lists_the_ones_that_exist():
    with pytest.raises(ProfileError) as excinfo:
        suppliers.get("wiener-netze")
    assert "tinetz" in excinfo.value.hint


class TestProfileValidation:
    def test_unknown_keys_are_rejected(self):
        with pytest.raises(ProfileError, match="unknown keys"):
            suppliers._from_mapping("x", {"name": "X", "baudrate": 9600})

    def test_an_unknown_layout_is_rejected(self):
        with pytest.raises(ProfileError, match="unknown layout"):
            suppliers._from_mapping("x", {"name": "X", "layout": "guess"})

    def test_an_unknown_status_is_rejected(self):
        with pytest.raises(ProfileError, match="unknown status"):
            suppliers._from_mapping("x", {"name": "X", "status": "probably fine"})

    @pytest.mark.parametrize(
        "value, expected", [(None, None), ("auto", "auto"), ([1, 103], (1, 103))]
    )
    def test_tsap_forms(self, value, expected):
        assert suppliers._from_mapping("x", {"name": "X", "tsap": value}).tsap == expected

    def test_a_malformed_tsap_is_rejected(self):
        with pytest.raises(ProfileError, match="tsap"):
            suppliers._from_mapping("x", {"name": "X", "tsap": [1, 2, 3]})
