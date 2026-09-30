"""Strict validation base shared by transport-independent contracts."""

from pydantic import BaseModel, ConfigDict


class JobModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
