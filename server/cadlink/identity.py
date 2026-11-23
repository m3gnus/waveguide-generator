"""Typed file identity, stable design hashing, and open classification."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import secrets
import threading
import time
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.design.cad_link import _DESIGN_ID, _LINEAGE_ID, CadLink
from server.design.schema import DesignConfig
from server.contracts.wglink_protocol import ASCII_NAN_PERMITTED, canonical_json


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _encode_ulid(value: int) -> str:
    chars = ["0"] * 26
    for index in range(25, -1, -1):
        chars[index] = _CROCKFORD[value & 31]
        value >>= 5
    return "".join(chars)


def mint_id(prefix: Literal["wgd_", "wgl_", "wge_", "wgb_", "wgi_"]) -> str:
    """Mint a prefixed, time-sortable ULID without an external dependency."""

    timestamp_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    return prefix + _encode_ulid((timestamp_ms << 80) | secrets.randbits(80))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class SaveIdentity(BaseModel):
    """The optimistic-concurrency token sent back by an open editor."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    design_id: str = Field(alias="designId")
    lineage_id: str = Field(alias="lineageId")
    base_edit_version: int = Field(alias="baseEditVersion", ge=1)

    @field_validator("design_id")
    @classmethod
    def _valid_design_id(cls, value: str) -> str:
        if not _DESIGN_ID.fullmatch(value):
            raise ValueError("designId must be a wgd_ ULID")
        return value

    @field_validator("lineage_id")
    @classmethod
    def _valid_lineage_id(cls, value: str) -> str:
        if not _LINEAGE_ID.fullmatch(value):
            raise ValueError("lineageId must be a wgl_ ULID")
        return value


OpenClassification = Literal[
    "current", "stale_copy", "externally_edited", "foreign", "missing"
]


#: Recent design hashes by the canonical payload they were computed from. The
#: round trip below costs ~130 ms on an imported ATH design, and the CAD status
#: and send paths ask again for the same, unchanged design.
_DESIGN_HASHES: OrderedDict[str, str] = OrderedDict()
_DESIGN_HASHES_LIMIT = 64
_DESIGN_HASHES_LOCK = threading.Lock()


def design_hash(design: DesignConfig) -> str:
    """Hash the stable canonical typed payload, excluding link metadata.

    The writer materializes a few v1 compatibility defaults (notably
    ``Throat.Profile``), so normalize through one canonical text round trip
    before hashing. This makes the pre-save hash equal the hash recomputed from
    the saved file.

    The round trip is a function of the payload alone, so its result is kept
    by that payload: a repeat is the same hash, byte for byte, without it.
    """

    key = canonical_json(design.model_dump(mode="json"), ASCII_NAN_PERMITTED)
    with _DESIGN_HASHES_LOCK:
        cached = _DESIGN_HASHES.get(key)
        if cached is not None:
            _DESIGN_HASHES.move_to_end(key)
            return cached

    from server.design.textcfg import parse, serialize

    normalized = parse(serialize(design)).design
    canonical = canonical_json(normalized.model_dump(mode="json"), ASCII_NAN_PERMITTED).encode("utf-8")
    value = "sha256:" + hashlib.sha256(canonical).hexdigest()
    with _DESIGN_HASHES_LOCK:
        _DESIGN_HASHES[key] = value
        _DESIGN_HASHES.move_to_end(key)
        while len(_DESIGN_HASHES) > _DESIGN_HASHES_LIMIT:
            _DESIGN_HASHES.popitem(last=False)
    return value


def truncated_design_hash(full_hash: str) -> str:
    return "sha256:" + full_hash.removeprefix("sha256:")[:16]


def classify_open(
    identity: CadLink | None,
    computed_hash: str,
    head: Mapping[str, object] | None,
) -> OpenClassification:
    """Classify file bytes against the per-machine registry without mutation."""

    if identity is None:
        return "missing"
    if head is None:
        return "foreign"
    # The registry can tell us which version is the head, while the truncated
    # hash carried by the file tells us whether these bytes are still the ones
    # that version saved. Check self-integrity first: an edited old copy is not
    # merely stale, and presenting it that way hides the external edit.
    if truncated_design_hash(computed_hash) != identity.saved_design_hash:
        return "externally_edited"
    head_version = int(head["edit_version"])
    if identity.edit_version < head_version:
        return "stale_copy"
    if identity.edit_version == head_version and computed_hash == head["design_hash"]:
        return "current"
    return "externally_edited"


__all__ = [
    "CadLink",
    "OpenClassification",
    "SaveIdentity",
    "classify_open",
    "design_hash",
    "mint_id",
    "truncated_design_hash",
    "utc_now",
]
