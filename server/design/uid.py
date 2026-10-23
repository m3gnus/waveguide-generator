"""Permanent document identity, outside DesignConfig and CadLink edit identity."""

from typing import Annotated

from pydantic import StringConstraints, TypeAdapter

DesignUid = Annotated[str, StringConstraints(pattern=r"^wgu_[0-7][0-9A-HJKMNP-TV-Z]{25}$")]
_adapter = TypeAdapter(DesignUid)


def validate_design_uid(value: object) -> str | None:
    return None if value is None else _adapter.validate_python(value)
