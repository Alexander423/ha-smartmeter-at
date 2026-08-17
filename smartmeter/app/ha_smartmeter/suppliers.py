"""Grid operator profiles.

Everything that differs between operators lives in `smartmeter/suppliers/*.yaml`.
Adding an operator is a pull request that touches one profile and one test
fixture, and nothing else. See CONTRIBUTING.md.

Every profile states how far it can be trusted, in `status`: "verified" means
somebody ran it against a physical meter, "documented" means it was transcribed
from the operator's published technical description, "assumed" means it was
inferred from a related operator. Anything short of "verified" is said so in the
add-on log, in the docs and on the ingress page, because a silently wrong
profile produces confidently wrong numbers, which is worse than an error.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .errors import ProfileError
from .framing import INTERFACES

_LOGGER = logging.getLogger(__name__)

GENERIC_ID = "generic-ksm-west"

#: Where the profiles are inside the container image, then the checkout layout.
_SEARCH_PATHS = (
    Path("/opt/smartmeter/suppliers"),
    Path(__file__).resolve().parents[2] / "suppliers",
)


@dataclass(frozen=True, slots=True)
class SerialSettings:
    baudrate: int = 2400
    bytesize: int = 8
    parity: str = "E"
    stopbits: int = 1
    #: DSMR P1 only sends while its Data Request line is held high. Most cables
    #: wire that to +5V, but some expect the host to raise DTR or RTS.
    data_request: str = "none"

    def describe(self) -> str:
        return f"{self.baudrate} baud {self.bytesize}{self.parity}{self.stopbits}"


#: Serial settings each interface uses, unless a profile overrides them.
INTERFACE_DEFAULTS: dict[str, SerialSettings] = {
    "mbus": SerialSettings(2400, 8, "E", 1),
    "p1": SerialSettings(115200, 8, "N", 1, data_request="both"),
    "hdlc": SerialSettings(9600, 8, "N", 1),
}

#: Why an interface cannot be read by this add-on, in the user's terms.
UNSUPPORTED_INTERFACES: dict[str, str] = {
    "oms-ir": (
        "This meter speaks OMS over M-Bus rather than DLMS, which is a different "
        "protocol that this add-on does not decode. It also needs an optical read "
        "head rather than an M-Bus adapter. See the README for the projects that do "
        "read it."
    ),
    "mep": (
        "This meter uses the MEP expansion port with ANSI C12.19, which is a "
        "different protocol and a different connector from anything this add-on "
        "reads. There is no adapter that would make it work."
    ),
    "wmbus": (
        "This meter sends over wireless M-Bus. Reading it needs an 868 MHz radio "
        "receiver rather than a cable, and this add-on only reads wired interfaces."
    ),
}


@dataclass(frozen=True, slots=True)
class SupplierProfile:
    id: str
    name: str
    #: mbus, p1, hdlc, oms-ir, mep or wmbus. Decides the framing and the serial
    #: settings, and whether the add-on can read this operator at all.
    interface: str = "mbus"
    serial: SerialSettings = field(default_factory=SerialSettings)
    #: Bundesländer this operator supplies. Several operators serve more than one.
    regions: tuple[str, ...] = ()

    #: (STSAP, DTSAP) pair in front of the DLMS data, None for none, "auto" to
    #: work it out from the first telegram. M-Bus only.
    tsap: tuple[int, int] | str | None = "auto"
    reassembly_timeout: float = 15.0

    #: Security control byte the meter is documented to send.
    security_control: int = 0x21
    #: "sc_fc", "fc_sc" or "auto". Some operators put the frame counter first.
    header_order: str = "sc_fc"
    #: True when the operator issues a second key for authentication, so the
    #: user can be told to go and get it instead of quietly losing the check.
    auth_key_expected: bool = False

    #: "obis_tagged" or "positional".
    layout: str = "obis_tagged"
    #: Only for the positional layout.
    obis_order: tuple[str, ...] = ()
    #: Per-value multipliers, when the meter sends raw integers in another unit.
    scales: dict[str, float] = field(default_factory=dict)

    #: OBIS codes the operator documents. Used to warn about missing values and
    #: to generate the supported-values table in the docs.
    expected_obis: tuple[str, ...] = ()

    manufacturer: str = "Unknown"
    model: str = "Smart meter (M-Bus customer interface)"

    #: How much this profile is worth trusting.
    #:   verified   someone ran it against a real meter and the numbers were right
    #:   documented transcribed from the operator's published technical description
    #:   assumed    inferred from a related operator, nobody has read a document
    status: str = "assumed"
    #: Free text shown in the log at start-up and on the ingress page.
    notes: str = ""
    #: Where the customer gets their key. Quoted verbatim in error messages.
    key_source: str = ""

    @property
    def unverified(self) -> bool:
        return self.status != "verified"

    @property
    def supported(self) -> bool:
        """False when the operator's interface carries something other than DLMS."""
        return self.interface not in UNSUPPORTED_INTERFACES

    def unsupported_hint(self) -> str:
        return UNSUPPORTED_INTERFACES.get(self.interface, "")

    @property
    def label(self) -> str:
        return self.name if self.status == "verified" else f"{self.name} ({self.status})"

    @property
    def region_label(self) -> str:
        return ", ".join(self.regions) if self.regions else "Austria"


def _coerce_tsap(value: Any) -> tuple[int, int] | str | None:
    if value is None:
        return None
    if isinstance(value, str):
        if value != "auto":
            raise ProfileError(f"tsap must be 'auto', null or a two element list, got {value!r}")
        return "auto"
    if isinstance(value, list | tuple) and len(value) == 2:
        return (int(value[0]), int(value[1]))
    raise ProfileError(f"tsap must be 'auto', null or a two element list, got {value!r}")


def _from_mapping(profile_id: str, data: dict[str, Any]) -> SupplierProfile:
    serial_data = data.get("serial") or {}
    unknown = set(data) - {
        "name",
        "interface",
        "regions",
        "serial",
        "tsap",
        "reassembly_timeout",
        "security_control",
        "header_order",
        "auth_key_expected",
        "layout",
        "obis_order",
        "scales",
        "expected_obis",
        "manufacturer",
        "model",
        "status",
        "notes",
        "key_source",
    }
    if unknown:
        raise ProfileError(f"profile {profile_id} has unknown keys: {', '.join(sorted(unknown))}")

    status = data.get("status", "assumed")
    if status not in ("verified", "documented", "assumed"):
        raise ProfileError(f"profile {profile_id} has an unknown status {status!r}")

    interface = data.get("interface", "mbus")
    if interface not in INTERFACES:
        raise ProfileError(
            f"profile {profile_id} has an unknown interface {interface!r}",
            hint="Valid interfaces are: " + ", ".join(INTERFACES) + ".",
        )
    # The serial settings follow from the interface, so a profile only states
    # them when its operator deviates.
    defaults = INTERFACE_DEFAULTS.get(interface, SerialSettings())

    layout = data.get("layout", "obis_tagged")
    if layout not in ("obis_tagged", "positional"):
        raise ProfileError(f"profile {profile_id} has an unknown layout {layout!r}")
    header_order = data.get("header_order", "sc_fc")
    if header_order not in ("sc_fc", "fc_sc", "auto"):
        raise ProfileError(f"profile {profile_id} has an unknown header_order {header_order!r}")

    return SupplierProfile(
        id=profile_id,
        name=data.get("name", profile_id),
        interface=interface,
        regions=tuple(data.get("regions") or ()),
        serial=SerialSettings(
            baudrate=int(serial_data.get("baudrate", defaults.baudrate)),
            bytesize=int(serial_data.get("bytesize", defaults.bytesize)),
            parity=str(serial_data.get("parity", defaults.parity)).upper(),
            stopbits=int(serial_data.get("stopbits", defaults.stopbits)),
            data_request=str(serial_data.get("data_request", defaults.data_request)).lower(),
        ),
        tsap=_coerce_tsap(data.get("tsap", "auto")),
        reassembly_timeout=float(data.get("reassembly_timeout", 15.0)),
        security_control=int(data.get("security_control", 0x21)),
        header_order=header_order,
        auth_key_expected=bool(data.get("auth_key_expected", False)),
        layout=layout,
        obis_order=tuple(data.get("obis_order") or ()),
        scales={str(k): float(v) for k, v in (data.get("scales") or {}).items()},
        expected_obis=tuple(data.get("expected_obis") or ()),
        manufacturer=data.get("manufacturer", "Unknown"),
        model=data.get("model", "Smart meter (M-Bus customer interface)"),
        status=status,
        notes=data.get("notes", "").strip(),
        key_source=data.get("key_source", "").strip(),
    )


def suppliers_dir() -> Path:
    override = os.environ.get("SMARTMETER_SUPPLIERS_DIR")
    candidates = (Path(override), *_SEARCH_PATHS) if override else _SEARCH_PATHS
    for path in candidates:
        if path.is_dir():
            return path
    raise ProfileError(
        f"no supplier profile directory found, looked in {', '.join(str(p) for p in candidates)}",
        hint="This is a packaging fault in the add-on, please open an issue.",
    )


@lru_cache(maxsize=1)
def load_all() -> dict[str, SupplierProfile]:
    directory = suppliers_dir()
    profiles: dict[str, SupplierProfile] = {}
    for path in sorted(directory.glob("*.yaml")):
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        if not isinstance(data, dict):
            raise ProfileError(f"{path.name} does not contain a mapping")
        profiles[path.stem] = _from_mapping(path.stem, data)
    if not profiles:
        raise ProfileError(f"no supplier profiles in {directory}")
    return profiles


def get(profile_id: str) -> SupplierProfile:
    profiles = load_all()
    profile = profiles.get(profile_id)
    if profile is None:
        raise ProfileError(
            f"unknown supplier profile {profile_id!r}",
            hint=(
                "Choose one of: "
                + ", ".join(sorted(profiles))
                + ". If your operator is not listed, choose the generic profile."
            ),
        )
    return profile
