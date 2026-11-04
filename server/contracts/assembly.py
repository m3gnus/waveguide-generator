"""Identity contract of the integrated Sources and baffle assembly.

One physical radiator has one stable ``radiator_id``; its moving patches are
``<radiator_id>/<segment_id>``. The horn's classic prescribed source keeps the
mesher's fixed identity. These names are shared by the design schema, the
compiled record and the solve setup, so they live here once.
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote

#: The design mesher's fixed physical source id for the horn throat.
HF_RADIATOR_ID = "horn-throat"
#: Physical-name namespace the design mesher writes for assembly tags.
MESHER_TAG_NAMESPACE = "wg-assembly-v1"
#: Feature the compiled record carries; a solve must negotiate it.
ASSEMBLY_RECORD_FEATURE = "wg-design-assembly-v1"
#: Mesher tag-map role to WG source role. The horn is the HF band; every
#: enclosure woofer patch is LF. No other mesher role is admitted.
MESHER_ROLE_TO_SOURCE_ROLE = {"primary": "HF", "secondary": "LF"}

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def is_radiator_uuid(value: object) -> bool:
    """A woofer's identity: a lower-case canonical UUID, never a label."""

    return isinstance(value, str) and _UUID.fullmatch(value) is not None


def patch_id(radiator_id: str, segment_id: str) -> str:
    if not ((is_radiator_uuid(radiator_id) or radiator_id == HF_RADIATOR_ID)
            and isinstance(segment_id, str) and segment_id and segment_id.strip() == segment_id
            and segment_id.isprintable()):
        raise ValueError("assembly patch ids name a radiator and a canonical contour segment")
    return f"{radiator_id}/{quote(segment_id, safe='')}"


def split_source_id(source_id: str) -> tuple[str, str | None]:
    """``(radiator_id, segment_id)`` of a compiled source id.

    The classic horn is unsegmented; sketches use the same escaped segment
    address on an HF or woofer radiator. Noncanonical spellings are refused.
    """

    if source_id == HF_RADIATOR_ID:
        return HF_RADIATOR_ID, None
    radiator, sep, segment = source_id.partition("/")
    if (not sep or not segment or not (is_radiator_uuid(radiator) or radiator == HF_RADIATOR_ID)
            or patch_id(radiator, unquote(segment)) != source_id):
        raise ValueError(f"unknown assembly source id {source_id!r}")
    return radiator, unquote(segment)


def source_role(mesher_role: str) -> str:
    try:
        return MESHER_ROLE_TO_SOURCE_ROLE[mesher_role]
    except KeyError:
        raise ValueError(f"unsupported assembly source role {mesher_role!r}") from None


__all__ = [
    "ASSEMBLY_RECORD_FEATURE",
    "HF_RADIATOR_ID",
    "MESHER_ROLE_TO_SOURCE_ROLE",
    "MESHER_TAG_NAMESPACE",
    "is_radiator_uuid",
    "patch_id",
    "source_role",
    "split_source_id",
]
