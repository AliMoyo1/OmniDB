"""Request and response models for the workforce API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

RoleCode = Literal["manager", "team_leader", "team_captain", "agent", "viewer"]
ScopeType = Literal["installation", "organization", "team", "campaign"]


class UserCreateRequest(BaseModel):
    email: str
    display_name: str
    workforce_id: str | None = None


class UserCreateOut(BaseModel):
    id: str
    email: str
    display_name: str
    workforce_id: str
    activation_token: str


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    workforce_id: str
    active: bool
    workforce_status: str
    created_at: datetime


class DisableUserRequest(BaseModel):
    reason_code: str


class RoleAssignRequest(BaseModel):
    role_code: RoleCode
    scope_type: ScopeType
    scope_id: uuid.UUID | None = None
    reason_code: str | None = None


class RoleAssignmentOut(BaseModel):
    id: str
    user_id: str
    role_code: str
    scope_type: str
    scope_id: str | None
    status: str
    effective_from: datetime
    effective_to: datetime | None


class RoleEndRequest(BaseModel):
    reason_code: str


class TeamCreateRequest(BaseModel):
    name: str
    external_code: str
    parent_team_id: uuid.UUID | None = None
    default_timezone: str = "Africa/Harare"


class TeamOut(BaseModel):
    id: str
    name: str
    external_code: str
    parent_team_id: str | None
    default_timezone: str
    status: str


class TeamMembershipRequest(BaseModel):
    user_id: uuid.UUID


class TeamMembershipOut(BaseModel):
    id: str
    team_id: str
    user_id: str
    membership_status: str
    effective_from: datetime
    effective_to: datetime | None


class ReportingLineRequest(BaseModel):
    supervisor_user_id: uuid.UUID
    context_type: Literal["organization", "team", "campaign"] = "organization"
    context_id: uuid.UUID | None = None
    assignment_type: Literal["primary", "acting", "dotted_line"] = "primary"
    reason_code: str | None = None


class ReportingAssignmentOut(BaseModel):
    id: str
    subordinate_user_id: str
    supervisor_user_id: str
    context_type: str
    context_id: str | None
    assignment_type: str
    status: str
    effective_from: datetime
    effective_to: datetime | None
