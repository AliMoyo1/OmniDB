"""Request and response models for the agent work API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


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


class CompleteRequest(BaseModel):
    lease_id: str
    disposition_id: str
    notes: str | None = None
    callback_at: datetime | None = None
    self_reported_duration_seconds: int | None = None
    idempotency_key: str


class CompleteOut(BaseModel):
    attempt_id: str
    work_item_state: str
    semantic_outcome: str
    callback_at: datetime | None


class SkipRequest(BaseModel):
    lease_id: str
    reason: str


class RenewRequest(BaseModel):
    lease_id: str


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
