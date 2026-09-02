"""ORM models. Import Base for Alembic's target metadata."""

from __future__ import annotations

from app.models.activation import ActivationToken
from app.models.audit import AuditEvent
from app.models.authz import Delegation, ReportingAssignment, RoleAssignment
from app.models.base import Base
from app.models.campaign import (
    Campaign,
    CampaignDispositionDefinition,
    CampaignTeamAssignment,
    CampaignUserAssignment,
)
from app.models.contact import CampaignContact, Contact, SuppressionEntry
from app.models.flags import FeatureFlag
from app.models.identity import Organization, Team, TeamMembership, User
from app.models.imports import ImportDecision, ImportJob, ImportRow
from app.models.session import Session
from app.models.work import Batch, CallAttempt, WorkItem
from app.models.workforce_imports import (
    WorkforceImportDecision,
    WorkforceImportJob,
    WorkforceImportRow,
)

__all__ = [
    "Base",
    "ActivationToken",
    "Organization",
    "Team",
    "User",
    "TeamMembership",
    "RoleAssignment",
    "ReportingAssignment",
    "Delegation",
    "Session",
    "AuditEvent",
    "Campaign",
    "CampaignTeamAssignment",
    "CampaignUserAssignment",
    "CampaignDispositionDefinition",
    "Contact",
    "CampaignContact",
    "SuppressionEntry",
    "Batch",
    "WorkItem",
    "CallAttempt",
    "ImportJob",
    "ImportRow",
    "ImportDecision",
    "FeatureFlag",
    "WorkforceImportJob",
    "WorkforceImportRow",
    "WorkforceImportDecision",
]
