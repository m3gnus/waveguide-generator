"""Typed file identity, stable design hashing, and open classification."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import secrets
import time
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.design.schema import ConfigBlock, DesignConfig


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DESIGN_ID = re.compile(r"^wgd_[0-9A-HJKMNP-TV-Z]{26}$")
_LINEAGE_ID = re.compile(r"^wgl_[0-9A-HJKMNP-TV-Z]{26}$")
_SAVED_HASH = re.compile(r"^sha256:[0-9a-f]{16}$")


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


class CadLink(BaseModel):
    """The typed, save-time identity carried by a ``CadLink`` text block."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    design_id: str
    lineage_id: str
    edit_version: int = Field(ge=1)
    saved_at: str
    saved_design_hash: str
    schema_version: Literal[1] = 1

    @field_validator("design_id")
    @classmethod
    def _valid_design_id(cls, value: str) -> str:
        if not _DESIGN_ID.fullmatch(value):
            raise ValueError("DesignId must be a wgd_ ULID")
        return value

    @field_validator("lineage_id")
    @classmethod
    def _valid_lineage_id(cls, value: str) -> str:
        if not _LINEAGE_ID.fullmatch(value):
            raise ValueError("LineageId must be a wgl_ ULID")
        return value

    @field_validator("saved_at")
    @classmethod
    def _valid_saved_at(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("SavedAt must be an RFC-3339 timestamp") from exc
        if parsed.tzinfo is None:
            raise ValueError("SavedAt must include a timezone")
        return value

    @field_validator("saved_design_hash")
    @classmethod
    def _valid_saved_hash(cls, value: str) -> str:
        if not _SAVED_HASH.fullmatch(value):
            raise ValueError("SavedDesignHash must be sha256: plus 16 lowercase hex digits")
        return value

    @classmethod
    def from_block(cls, block: ConfigBlock) -> CadLink:
        required = {
            "DesignId",
            "LineageId",
            "EditVersion",
            "SavedAt",
            "SavedDesignHash",
            "Schema",
        }
        if (
            set(block.items) != required
            or len(block.entries) != len(required)
            or block.lines
            or block.comments
        ):
            raise ValueError("CadLink block must contain exactly the six schema-1 fields")
        return cls(
            design_id=block.items["DesignId"],
            lineage_id=block.items["LineageId"],
            edit_version=int(block.items["EditVersion"]),
            saved_at=block.items["SavedAt"],
            saved_design_hash=block.items["SavedDesignHash"],
            schema_version=int(block.items["Schema"]),
        )

    def block_lines(self) -> list[str]:
        return [
            "CadLink = {",
            f"DesignId = {self.design_id}",
            f"LineageId = {self.lineage_id}",
            f"EditVersion = {self.edit_version}",
            f"SavedAt = {self.saved_at}",
            f"SavedDesignHash = {self.saved_design_hash}",
            f"Schema = {self.schema_version}",
            "}",
        ]

    def api_dict(self) -> dict[str, object]:
        return {
            "designId": self.design_id,
            "lineageId": self.lineage_id,
            "editVersion": self.edit_version,
            "savedAt": self.saved_at,
            "savedDesignHash": self.saved_design_hash,
            "schema": self.schema_version,
        }


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


def design_hash(design: DesignConfig) -> str:
    """Hash the stable canonical typed payload, excluding link metadata.

    The writer materializes a few v1 compatibility defaults (notably
    ``Throat.Profile``), so normalize through one canonical text round trip
    before hashing. This makes the pre-save hash equal the hash recomputed from
    the saved file.
    """

    from server.design.textcfg import parse, serialize

    normalized = parse(serialize(design)).design
    canonical = json.dumps(
        normalized.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


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
