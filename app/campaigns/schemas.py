"""Request and response models for the campaign API."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, model_validator


class CampaignCreateRequest(BaseModel):
    name: str
    description: str | None = None
    owning_scope_type: Literal["organization", "team"] = "organization"
    owning_scope_id: uuid.UUID | None = None
    default_region: str = "ZW"
    timezone: str = "Africa/Harare"
    purpose: str | None = None
    data_source: str | None = None
    data_obtained_at: date | None = None
    lawful_basis_or_consent_reference: str | None = None

    @model_validator(mode="after")
    def require_team_scope_id(self) -> CampaignCreateRequest:
        if self.owning_scope_type == "team" and self.owning_scope_id is None:
            raise ValueError("owning_scope_id is required for a team-owned campaign")
        return self


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
    next_action: Literal["complete", "review", "requeue"] | None = None
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


class TeamAssignmentRequest(BaseModel):
    team_id: uuid.UUID
    staffing_capacity: int | None = None


class TeamAssignmentOut(BaseModel):
    id: str
    campaign_id: str
    team_id: str
    status: str
    staffing_capacity: int | None
    effective_from: datetime
    effective_to: datetime | None


class UserAssignmentRequest(BaseModel):
    agent_id: uuid.UUID
    team_id: uuid.UUID | None = None
    assignment_type: Literal["primary", "secondary", "callback_only"] = "primary"
    priority: int | None = None
    allocation_percentage: int | None = None
    shift_reference: str | None = None


class UserAssignmentOut(BaseModel):
    id: str
    campaign_id: str
    user_id: str
    team_id: str | None
    campaign_role: str
    assignment_type: str
    status: str
    effective_from: datetime
    effective_to: datetime | None


class AgentTransferRequest(BaseModel):
    agent_id: uuid.UUID
    to_campaign_id: uuid.UUID
    team_id: uuid.UUID | None = None
    reason_code: str | None = None
