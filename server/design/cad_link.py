"""The typed save-time identity a design file carries in its ``CadLink`` block.

The design text reader parses it; CAD Link (``server/cadlink/identity.py``)
mints, hashes and classifies it.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schema import ConfigBlock


_DESIGN_ID = re.compile(r"^wgd_[0-9A-HJKMNP-TV-Z]{26}$")
_LINEAGE_ID = re.compile(r"^wgl_[0-9A-HJKMNP-TV-Z]{26}$")
_SAVED_HASH = re.compile(r"^sha256:[0-9a-f]{16}$")


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


__all__ = ["CadLink"]
