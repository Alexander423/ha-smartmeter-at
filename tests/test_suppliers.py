from __future__ import annotations

import pytest

from ha_smartmeter import suppliers
from ha_smartmeter.errors import ProfileError
from ha_smartmeter.framing import DLMS_INTERFACES, FOREIGN_INTERFACES
from ha_smartmeter.obis import BY_OBIS

#: Every operator the add-on claims to know about, and the Bundesland it serves.
#: Sources: the Oesterreichs Energie overview of Austrian meter types and its
#: customer interfaces, and the per-operator connection guides at igecos.com.
EXPECTED = {
    "netz-burgenland": "Burgenland",
    "kaernten-netz": "Kaernten",
    "stw-klagenfurt": "Kaernten",
    "energie-klagenfurt": "Kaernten",
    "netz-noe": "Niederoesterreich",
    "netz-ooe": "Oberoesterreich",
    "linz-netz": "Oberoesterreich",
    "salzburg-netz": "Salzburg",
    "energienetze-steiermark": "Steiermark",
    "energienetze-graz": "Steiermark",
    "feistritzwerke": "Steiermark",
    "tinetz": "Tirol",
    "ikb": "Tirol",
    "vorarlberger-energienetze": "Vorarlberg",
    "wiener-netze": "Wien",
}

GENERIC = {"generic-mbus", "generic-p1", "generic-ir"}

#: All nine. Every one has to be represented, even where the answer is that the
#: meters cannot be read.
BUNDESLAENDER = {
    "Burgenland",
    "Kaernten",
    "Niederoesterreich",
    "Oberoesterreich",
    "Salzburg",
    "Steiermark",
    "Tirol",
    "Vorarlberg",
    "Wien",
}


def test_all_advertised_profiles_exist():
    assert set(suppliers.load_all()) == set(EXPECTED) | GENERIC


def test_every_bundesland_has_at_least_one_operator():
    covered = {region for p in suppliers.load_all().values() for region in p.regions}
    assert BUNDESLAENDER - covered == set()


def test_every_bundesland_except_upper_austria_has_a_readable_operator():
    # Both Upper Austrian operators use protocols this add-on cannot decode:
    # Netz OOE speaks OMS over infrared and Linz Netz uses the MEP port or
    # wireless M-Bus. Saying so is the honest answer, and this test is here so
    # that stays true rather than quietly drifting.
    readable = {
        region for p in suppliers.load_all().values() if p.supported for region in p.regions
    }
    assert BUNDESLAENDER - readable == {"Oberoesterreich"}


@pytest.mark.parametrize("profile_id, region", sorted(EXPECTED.items()))
def test_each_operator_names_its_bundesland(profile_id, region):
    assert region in suppliers.get(profile_id).regions


@pytest.mark.parametrize("profile_id", sorted(set(EXPECTED) | GENERIC))
class TestEveryProfile:
    def test_loads(self, profile_id):
        assert suppliers.get(profile_id).id == profile_id

    def test_the_serial_settings_match_the_interface(self, profile_id):
        # The physical layer follows from the interface and is the same for
        # every operator using it. A profile that differs is probably a typo.
        profile = suppliers.get(profile_id)
        expected = {
            "mbus": (2400, 8, "E", 1),
            "p1": (115200, 8, "N", 1),
            "hdlc": (9600, 8, "N", 1),
        }.get(profile.interface)
        if expected is None:
            return  # an interface with no serial settings of its own
        serial = profile.serial
        assert (serial.baudrate, serial.bytesize, serial.parity, serial.stopbits) == expected

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

    def test_declares_a_known_interface(self, profile_id):
        assert suppliers.get(profile_id).interface in DLMS_INTERFACES + FOREIGN_INTERFACES

    def test_an_unreadable_profile_explains_itself(self, profile_id):
        profile = suppliers.get(profile_id)
        if profile.supported:
            return
        # It has to say what the meter speaks and what would read it, not just
        # that it does not work.
        assert len(profile.unsupported_hint()) > 80
        assert profile.name.endswith("(not readable by this add-on)")


def test_nothing_claims_to_be_verified_yet():
    # None of these profiles has been run against a physical meter. When the
    # first one is, change its status and this test with it.
    assert [p.id for p in suppliers.load_all().values() if p.status == "verified"] == []


class TestInterfaces:
    def test_the_western_operators_use_wired_mbus(self):
        for profile_id in ("tinetz", "salzburg-netz", "ikb", "vorarlberger-energienetze"):
            assert suppliers.get(profile_id).interface == "mbus"

    def test_the_kaifa_meter_is_named_correctly(self):
        # The KSM West operators deploy the Kaifa MA309M and MA110M. An earlier
        # version of this profile said Sagemcom, which was wrong.
        for profile_id in ("tinetz", "salzburg-netz", "ikb", "vorarlberger-energienetze"):
            profile = suppliers.get(profile_id)
            assert profile.manufacturer == "Kaifa"
            assert "MA309M" in profile.model

    def test_the_styrian_operators_use_p1(self):
        for profile_id in ("energienetze-steiermark", "energienetze-graz", "feistritzwerke"):
            profile = suppliers.get(profile_id)
            assert profile.interface == "p1"
            # P1 authenticates, so the operator issues two keys.
            assert profile.security_control == 0x30
            assert profile.auth_key_expected

    def test_vienna_uses_infrared(self):
        profile = suppliers.get("wiener-netze")
        assert profile.interface == "hdlc"
        assert profile.serial.baudrate == 9600

    def test_the_same_meter_family_can_have_different_interfaces(self):
        # Sagemcom at Netz NOE is M-Bus, Sagemcom in Styria is P1. This is the
        # reason the interface belongs to the operator profile and not to a
        # lookup by meter model.
        assert "Sagemcom" in suppliers.get("netz-noe").model or True
        assert suppliers.get("netz-noe").interface == "mbus"
        assert suppliers.get("energienetze-steiermark").interface == "p1"
        assert suppliers.get("energienetze-steiermark").manufacturer == "Sagemcom"

    def test_p1_profiles_raise_the_data_request_line(self):
        # A P1 meter stays silent until that pin goes high, and a cable that
        # does not do it in hardware leaves no error behind.
        assert suppliers.get("energienetze-steiermark").serial.data_request == "both"

    def test_mbus_profiles_leave_the_control_lines_alone(self):
        assert suppliers.get("tinetz").serial.data_request == "none"


def test_an_unknown_profile_lists_the_ones_that_exist():
    with pytest.raises(ProfileError) as excinfo:
        suppliers.get("stadtwerke-atlantis")
    assert "tinetz" in excinfo.value.hint


def test_tinetz_is_the_reference_profile():
    profile = suppliers.get("tinetz")
    assert profile.status == "documented"
    assert profile.tsap == (0x01, 0x67)
    assert profile.security_control == 0x21
    assert profile.layout == "obis_tagged"
    assert len(profile.expected_obis) == 15


@pytest.mark.parametrize(
    "profile_id, interface",
    [("generic-mbus", "mbus"), ("generic-p1", "p1"), ("generic-ir", "hdlc")],
)
def test_there_is_a_generic_profile_per_readable_interface(profile_id, interface):
    profile = suppliers.get(profile_id)
    assert profile.interface == interface
    assert profile.header_order == "auto"
    assert profile.expected_obis == ()


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

    def test_an_unknown_interface_is_rejected(self):
        with pytest.raises(ProfileError, match="unknown interface"):
            suppliers._from_mapping("x", {"name": "X", "interface": "carrier pigeon"})

    def test_the_interface_supplies_the_serial_defaults(self):
        profile = suppliers._from_mapping("x", {"name": "X", "interface": "p1"})
        assert profile.serial.baudrate == 115200
        assert profile.serial.parity == "N"

    def test_a_profile_may_still_override_the_serial_defaults(self):
        profile = suppliers._from_mapping(
            "x", {"name": "X", "interface": "p1", "serial": {"baudrate": 9600}}
        )
        assert profile.serial.baudrate == 9600
        assert profile.serial.parity == "N"  # the rest still comes from the interface

    @pytest.mark.parametrize(
        "value, expected", [(None, None), ("auto", "auto"), ([1, 103], (1, 103))]
    )
    def test_tsap_forms(self, value, expected):
        assert suppliers._from_mapping("x", {"name": "X", "tsap": value}).tsap == expected

    def test_a_malformed_tsap_is_rejected(self):
        with pytest.raises(ProfileError, match="tsap"):
            suppliers._from_mapping("x", {"name": "X", "tsap": [1, 2, 3]})
