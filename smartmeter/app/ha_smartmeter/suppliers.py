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

    def describe(self) -> str:
        return f"{self.baudrate} baud {self.bytesize}{self.parity}{self.stopbits}"


@dataclass(frozen=True, slots=True)
class SupplierProfile:
    id: str
    name: str
    serial: SerialSettings = field(default_factory=SerialSettings)

    #: (STSAP, DTSAP) pair in front of the DLMS data, None for none, "auto" to
    #: work it out from the first telegram.
    tsap: tuple[int, int] | str | None = "auto"
    reassembly_timeout: float = 15.0

    #: Security control byte the meter is documented to send.
    security_control: int = 0x21
    #: "sc_fc", "fc_sc" or "auto". Some operators put the frame counter first.
    header_order: str = "sc_fc"

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
    def label(self) -> str:
        return self.name if self.status == "verified" else f"{self.name} ({self.status})"


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
        "serial",
        "tsap",
        "reassembly_timeout",
        "security_control",
        "header_order",
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

    layout = data.get("layout", "obis_tagged")
    if layout not in ("obis_tagged", "positional"):
        raise ProfileError(f"profile {profile_id} has an unknown layout {layout!r}")
    header_order = data.get("header_order", "sc_fc")
    if header_order not in ("sc_fc", "fc_sc", "auto"):
        raise ProfileError(f"profile {profile_id} has an unknown header_order {header_order!r}")

    return SupplierProfile(
        id=profile_id,
        name=data.get("name", profile_id),
        serial=SerialSettings(
            baudrate=int(serial_data.get("baudrate", 2400)),
            bytesize=int(serial_data.get("bytesize", 8)),
            parity=str(serial_data.get("parity", "E")).upper(),
            stopbits=int(serial_data.get("stopbits", 1)),
        ),
        tsap=_coerce_tsap(data.get("tsap", "auto")),
        reassembly_timeout=float(data.get("reassembly_timeout", 15.0)),
        security_control=int(data.get("security_control", 0x21)),
        header_order=header_order,
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
