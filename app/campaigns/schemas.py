"""Request and response models for the campaign API."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator


class CampaignCreateRequest(BaseModel):
    external_code: str
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

    @field_validator("external_code")
    @classmethod
    def canonical_external_code(cls, value: str) -> str:
        # Same shape as every other "code" field this build resolves by exact
        # match (external_workforce_id, team_code): trimmed, non-empty, bounded
        # to the column's own length, and never whitespace-internal - a code
        # with leading/trailing or embedded whitespace would create a campaign
        # the CSV importer's own exact-match lookup could never resolve.
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("external_code must not be empty")
        if len(cleaned) > 50:
            raise ValueError("external_code must be at most 50 characters")
        if any(character.isspace() for character in cleaned):
            raise ValueError("external_code must not contain whitespace")
        return cleaned

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
    external_code: str
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


class CampaignStatsOut(BaseModel):
    total_contacts: int
    assigned_agents: int
    total_attempts: int
    connected: int
    conversions: int
    dnc_requests: int
