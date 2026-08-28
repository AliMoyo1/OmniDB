"""Request and response models for the feature-flags API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class FlagOut(BaseModel):
    flag_key: str
    enabled: bool
    updated_by: str | None
    updated_at: datetime


class FlagSetRequest(BaseModel):
    enabled: bool
    reason_code: str | None = None
