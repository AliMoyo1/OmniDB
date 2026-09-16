"""Request and response models for the agent work API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field


class LeaseOut(BaseModel):
    work_item_id: str
    lease_id: str
    lease_expires_at: datetime
    campaign_id: str
    campaign_name: str
    phone_e164: str
    contact_name: str | None
    approved_metadata: dict | None
    is_callback: bool
    lease_reason: Literal["normal", "scheduled_callback", "delayed_retry", "immediate_redial"]


class CompleteRequest(BaseModel):
    lease_id: uuid.UUID
    disposition_id: uuid.UUID
    notes: str | None = Field(default=None, max_length=3000)
    callback_at: AwareDatetime | None = None
    self_reported_duration_seconds: int | None = Field(default=None, ge=0, le=86_400)
    idempotency_key: str = Field(min_length=1, max_length=100)


class CompleteOut(BaseModel):
    attempt_id: str
    work_item_state: str
    semantic_outcome: str
    callback_at: datetime | None
    retry_at: datetime | None
    redial_lease_id: str | None
    redial_lease_expires_at: datetime | None
    next_step: Literal[
        "complete", "retry_scheduled", "callback_scheduled", "redial_ready", "review", "suppressed"
    ]


class SkipRequest(BaseModel):
    lease_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=500)


class RenewRequest(BaseModel):
    lease_id: uuid.UUID


class RenewOut(BaseModel):
    work_item_id: str
    lease_id: str
    lease_expires_at: datetime


class CallbackItemOut(BaseModel):
    work_item_id: str
    campaign_id: str
    campaign_name: str
    reference: str
    due_at: datetime | None


class AgentStatsOut(BaseModel):
    period: str
    total_attempts: int
    connected: int
    conversions: int
    dnc_requests: int
