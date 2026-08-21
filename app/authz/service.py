"""Authorization service: effective-dated roles, capability checks, self-approval guard.

Default deny everywhere. Object scope is resolved on the server; nothing is trusted from
the client.
"""

from __future__ import annotations

import uuid

from sqlalchemy import and_, exists, false, or_, select, true
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.auth import sessions as sess
from app.authz.capabilities import ROLE_CAPABILITIES
from app.models.authz import RoleAssignment
from app.models.base import utcnow
from app.models.campaign import Campaign, CampaignTeamAssignment
from app.models.identity import Team


class SelfApprovalError(Exception):
    """A user attempted to approve or authorize their own action."""


def effective_role_assignments(db: Session, user_id: uuid.UUID) -> list[RoleAssignment]:
    now = utcnow()
    stmt = select(RoleAssignment).where(
        RoleAssignment.user_id == user_id,
        RoleAssignment.status == "active",
        RoleAssignment.effective_from <= now,
        or_(RoleAssignment.effective_to.is_(None), RoleAssignment.effective_to > now),
    )
    return list(db.scalars(stmt))


def effective_roles(db: Session, user_id: uuid.UUID) -> set[str]:
    return {ra.role_code for ra in effective_role_assignments(db, user_id)}


def capabilities_for(roles: set[str]) -> set[str]:
    caps: set[str] = set()
    for role in roles:
        caps |= ROLE_CAPABILITIES.get(role, set())
    return caps


def _scope_covers(
    ra: RoleAssignment, scope_type: str | None, scope_id: uuid.UUID | None
) -> bool:
    if ra.scope_type == "installation":
        return True
    if scope_type is None:
        # An unscoped check is installation/organization-wide. Narrow assignments
        # must never be promoted merely because the caller omitted an object scope.
        return ra.scope_type == "organization"
    if ra.scope_type == "organization":
        # A null organization ID is the single-organization installation scope used
        # by the first pilot. Specific organization IDs do not cover an object whose
        # ancestry has not been resolved by the caller.
        return ra.scope_id is None or (
            scope_type == "organization" and ra.scope_id == scope_id
        )
    return ra.scope_type == scope_type and ra.scope_id == scope_id


def has_capability(
    db: Session,
    user_id: uuid.UUID,
    capability: str,
    *,
    scope_type: str | None = None,
    scope_id: uuid.UUID | None = None,
) -> bool:
    for ra in effective_role_assignments(db, user_id):
        granted = ROLE_CAPABILITIES.get(ra.role_code, set())
        if capability in granted and _scope_covers(ra, scope_type, scope_id):
            return True
    return False


def has_assigned_capability(db: Session, user_id: uuid.UUID, capability: str) -> bool:
    """Return whether any effective role grants a capability, independent of scope."""
    return any(
        capability in ROLE_CAPABILITIES.get(assignment.role_code, set())
        for assignment in effective_role_assignments(db, user_id)
    )


def _scope_assignment_covers_target(
    db: Session,
    assignment: RoleAssignment,
    scope_type: str,
    scope_id: uuid.UUID | None,
) -> bool:
    if assignment.scope_type == "installation":
        return True
    if assignment.scope_type == "organization":
        if assignment.scope_id is None:
            return True
        if scope_type == "organization":
            return assignment.scope_id == scope_id
        if scope_type == "team" and scope_id is not None:
            organization_id = db.scalar(
                select(Team.organization_id).where(Team.id == scope_id, Team.status == "active")
            )
            return organization_id == assignment.scope_id
        return False
    return assignment.scope_type == scope_type and assignment.scope_id == scope_id


def has_scope_capability(
    db: Session,
    user_id: uuid.UUID,
    capability: str,
    *,
    scope_type: str,
    scope_id: uuid.UUID | None,
) -> bool:
    """Authorize creation or management of a server-validated owning scope."""
    for assignment in effective_role_assignments(db, user_id):
        if capability not in ROLE_CAPABILITIES.get(assignment.role_code, set()):
            continue
        if _scope_assignment_covers_target(db, assignment, scope_type, scope_id):
            return True
    return False


def campaign_scope_filter(
    db: Session, user_id: uuid.UUID, capability: str
) -> ColumnElement[bool]:
    """Build the database filter for campaigns visible through effective role scopes."""
    assignments = [
        assignment
        for assignment in effective_role_assignments(db, user_id)
        if capability in ROLE_CAPABILITIES.get(assignment.role_code, set())
    ]
    if not assignments:
        return false()
    if any(
        assignment.scope_type == "installation"
        or (assignment.scope_type == "organization" and assignment.scope_id is None)
        for assignment in assignments
    ):
        return true()

    campaign_ids = {
        assignment.scope_id
        for assignment in assignments
        if assignment.scope_type == "campaign" and assignment.scope_id is not None
    }
    team_ids = {
        assignment.scope_id
        for assignment in assignments
        if assignment.scope_type == "team" and assignment.scope_id is not None
    }
    organization_ids = {
        assignment.scope_id
        for assignment in assignments
        if assignment.scope_type == "organization" and assignment.scope_id is not None
    }

    conditions: list[ColumnElement[bool]] = []
    if campaign_ids:
        conditions.append(Campaign.id.in_(campaign_ids))

    now = utcnow()
    active_team_assignment = and_(
        CampaignTeamAssignment.campaign_id == Campaign.id,
        CampaignTeamAssignment.status == "active",
        CampaignTeamAssignment.effective_from <= now,
        or_(
            CampaignTeamAssignment.effective_to.is_(None),
            CampaignTeamAssignment.effective_to > now,
        ),
    )
    if team_ids:
        conditions.extend(
            [
                and_(
                    Campaign.owning_scope_type == "team",
                    Campaign.owning_scope_id.in_(team_ids),
                ),
                exists().where(
                    active_team_assignment,
                    CampaignTeamAssignment.team_id.in_(team_ids),
                ),
            ]
        )

    if organization_ids:
        organization_team_ids = select(Team.id).where(
            Team.organization_id.in_(organization_ids), Team.status == "active"
        )
        conditions.extend(
            [
                and_(
                    Campaign.owning_scope_type == "organization",
                    Campaign.owning_scope_id.in_(organization_ids),
                ),
                and_(
                    Campaign.owning_scope_type == "team",
                    Campaign.owning_scope_id.in_(organization_team_ids),
                ),
                exists().where(
                    active_team_assignment,
                    CampaignTeamAssignment.team_id.in_(organization_team_ids),
                ),
            ]
        )

    return or_(*conditions) if conditions else false()


def has_campaign_capability(
    db: Session, user_id: uuid.UUID, capability: str, campaign_id: uuid.UUID
) -> bool:
    """Authorize a campaign only after resolving its owning and assigned team scopes."""
    stmt = select(Campaign.id).where(
        Campaign.id == campaign_id,
        campaign_scope_filter(db, user_id, capability),
    )
    return db.scalar(stmt) is not None


def assert_not_self(requester_id: uuid.UUID, subject_id: uuid.UUID) -> None:
    if requester_id == subject_id:
        raise SelfApprovalError("a user may not authorize this action on themselves")


def invalidate_sessions_on_privilege_change(db: Session, user_id: uuid.UUID) -> None:
    """Role or privilege changes revoke the affected user's active sessions (plan 6.4)."""
    sess.revoke_all_for_user(db, user_id)
