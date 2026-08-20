"""Request and response models for the campaign API."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class CampaignCreateRequest(BaseModel):
    name: str
    description: str | None = None
    owning_scope_type: str = "organization"
    owning_scope_id: str | None = None
    default_region: str = "ZW"
    timezone: str = "Africa/Harare"
    purpose: str | None = None
    data_source: str | None = None
    data_obtained_at: date | None = None
    lawful_basis_or_consent_reference: str | None = None


class CampaignUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    default_region: str | None = None
    timezone: str | None = None
    purpose: str | None = None
    data_source: str | None = None
    data_obtained_at: date | None = None
    lawful_basis_or_consent_reference: str | None = None


class CampaignOut(BaseModel):
    id: str
    name: str
    description: str | None
    status: str
    default_region: str
    timezone: str
    purpose: str | None
    data_source: str | None
    data_obtained_at: date | None
    lawful_basis_or_consent_reference: str | None
    created_at: datetime
    launched_at: datetime | None
    archived_at: datetime | None


class DispositionCreateRequest(BaseModel):
    label: str
    stable_semantic_code: str
    next_action: str | None = None
    requires_notes: bool = False
    requires_callback_time: bool = False
    counts_as_connected: bool = False
    counts_as_conversion: bool = False
    causes_dnc: bool = False


class DispositionOut(BaseModel):
    id: str
    label: str
    stable_semantic_code: str
    causes_dnc: bool
    active: bool
