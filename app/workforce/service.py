"""Workforce hierarchy: users, roles, teams, and reporting lines (plan 6, Phase 4A).

Default deny, no unbounded inheritance (plan 6.4): every role grant is checked
against the actor's own scoped capability for that specific target role, not a
generic "manage roles" shortcut. super_admin is never assignable here - it stays a
manual/ops-provisioned role, consistent with how the first Manager account in this
build was created outside the app.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.auth.service import issue_activation_token
from app.authz import service as authz
from app.authz.capabilities import (
    APPOINT_TEAM_CAPTAIN,
    APPOINT_TEAM_LEADER,
    CREATE_AGENT,
    CREATE_MANAGER,
    MANAGE_ROLES,
    ROLE_AGENT,
    ROLE_CAPABILITIES,
    ROLE_MANAGER,
    ROLE_TEAM_CAPTAIN,
    ROLE_TEAM_LEADER,
    ROLE_VIEWER,
)
from app.models.authz import ReportingAssignment, RoleAssignment
from app.models.base import utcnow
from app.models.identity import Organization, Team, TeamMembership, User
from app.work.service import reclaim_leases_for_user

# The capability that gates appointing a user INTO this role (plan 6.3). Deliberately
# excludes super_admin.
ROLE_APPOINTMENT_CAPABILITY: dict[str, str] = {
    ROLE_MANAGER: CREATE_MANAGER,
    ROLE_TEAM_LEADER: APPOINT_TEAM_LEADER,
    ROLE_TEAM_CAPTAIN: APPOINT_TEAM_CAPTAIN,
    ROLE_AGENT: CREATE_AGENT,
    ROLE_VIEWER: MANAGE_ROLES,
}

_DEFAULT_ORGANIZATION_NAME = "Default organization"


class WorkforceError(Exception):
    pass


class DuplicateIdentity(WorkforceError):
    pass


class UnknownRole(WorkforceError):
    pass


class SelfSupervision(WorkforceError):
    pass


def visible_team_ids(db: Session, actor_id: uuid.UUID) -> tuple[bool, set[uuid.UUID]]:
    """Scope a workforce (user) listing to what the actor's own appointment-capability
    grants cover, the same non-leaking principle campaign_scope_filter already applies
    to campaigns. Returns (sees_everyone, team_ids); an empty, non-everyone result
    means the actor has no appointment capability with a resolvable scope at all."""
    appointment_capabilities = set(ROLE_APPOINTMENT_CAPABILITY.values())
    sees_everyone = False
    team_ids: set[uuid.UUID] = set()
    for assignment in authz.effective_role_assignments(db, actor_id):
        if not ROLE_CAPABILITIES.get(assignment.role_code, set()) & appointment_capabilities:
            continue
        if assignment.scope_type == "installation":
            sees_everyone = True
        elif assignment.scope_type == "organization" and assignment.scope_id is None:
            sees_everyone = True
        elif assignment.scope_type == "team" and assignment.scope_id is not None:
            team_ids.add(assignment.scope_id)
    return sees_everyone, team_ids


def can_manage_user(db: Session, actor_id: uuid.UUID, target_id: uuid.UUID) -> bool:
    """Whether the actor could have appointed the target into at least one of their
    current active roles, at that role's own scope - the same authority used to
    disable/reactivate them or change their reporting line. A user with no role yet
    (freshly created, pre-grant) may be managed by anyone holding any appointment
    capability at all, matching create_user's own gate."""
    target_roles = authz.effective_role_assignments(db, target_id)
    if not target_roles:
        return any(
            authz.has_assigned_capability(db, actor_id, capability)
            for capability in ROLE_APPOINTMENT_CAPABILITY.values()
        )
    for assignment in target_roles:
        capability = ROLE_APPOINTMENT_CAPABILITY.get(assignment.role_code)
        if capability is None:
            continue
        if authz.has_scope_capability(
            db, actor_id, capability,
            scope_type=assignment.scope_type, scope_id=assignment.scope_id,
        ):
            return True
    return False


def create_user(
    db: Session,
    *,
    email: str,
    display_name: str,
    workforce_id: str | None,
    created_by: uuid.UUID,
) -> tuple[User, str]:
    """Create an identity with no password and no role (ADR-005C: workforce_id is the
    email's local part, immutable once set). Returns (user, activation_token) - the
    same one-time-activation pattern already used for a Super Admin password reset."""
    derived_id = workforce_id or email.split("@")[0]
    existing = db.scalar(
        select(User.id).where(or_(User.email == email, User.workforce_id == derived_id))
    )
    if existing is not None:
        raise DuplicateIdentity("a user with this email or workforce ID already exists")
    user = User(workforce_id=derived_id, email=email, display_name=display_name)
    db.add(user)
    db.flush()
    token = issue_activation_token(db, user.id, created_by=created_by)
    record_audit(
        db, action="workforce.user.create", result="success", actor_user_id=created_by,
        target_type="user", target_id=user.id,
    )
    return user, token


def disable_user(db: Session, target: User, *, actor_id: uuid.UUID, reason_code: str) -> User:
    """Plan 6.4: disabling a user immediately revokes active sessions and leases."""
    authz.assert_not_self(actor_id, target.id)
    target.active = False
    target.workforce_status = "inactive"
    target.disabled_at = utcnow()
    authz.invalidate_sessions_on_privilege_change(db, target.id)
    reclaimed = reclaim_leases_for_user(db, target.id)
    record_audit(
        db, action="workforce.user.disable", result="success", actor_user_id=actor_id,
        target_type="user", target_id=target.id, reason_code=reason_code,
        event_metadata={"leases_reclaimed": reclaimed},
    )
    return target


def reactivate_user(db: Session, target: User, *, actor_id: uuid.UUID, reason_code: str) -> User:
    target.active = True
    target.workforce_status = "active"
    target.disabled_at = None
    record_audit(
        db, action="workforce.user.reactivate", result="success", actor_user_id=actor_id,
        target_type="user", target_id=target.id, reason_code=reason_code,
    )
    return target


def assign_role(
    db: Session,
    *,
    target_user_id: uuid.UUID,
    role_code: str,
    scope_type: str,
    scope_id: uuid.UUID | None,
    appointed_by: uuid.UUID,
    reason_code: str | None = None,
) -> RoleAssignment:
    if role_code not in ROLE_APPOINTMENT_CAPABILITY:
        raise UnknownRole(f"{role_code} is not an appointable role")
    authz.assert_not_self(appointed_by, target_user_id)
    now = utcnow()
    # Re-granting the same (user, role, scope) supersedes the prior grant rather than
    # stacking a second overlapping one; the partial unique index backs this too.
    prior = db.scalars(
        select(RoleAssignment).where(
            RoleAssignment.user_id == target_user_id,
            RoleAssignment.role_code == role_code,
            RoleAssignment.scope_type == scope_type,
            RoleAssignment.scope_id == scope_id,
            RoleAssignment.status == "active",
            RoleAssignment.effective_to.is_(None),
        )
    )
    for existing in prior:
        existing.status = "ended"
        existing.effective_to = now
        existing.ended_at = now
    assignment = RoleAssignment(
        user_id=target_user_id,
        role_code=role_code,
        scope_type=scope_type,
        scope_id=scope_id,
        effective_from=now,
        appointed_by=appointed_by,
        reason_code=reason_code,
    )
    db.add(assignment)
    db.flush()
    authz.invalidate_sessions_on_privilege_change(db, target_user_id)
    record_audit(
        db, action="workforce.role.assign", result="success", actor_user_id=appointed_by,
        target_type="user", target_id=target_user_id, reason_code=reason_code,
        event_metadata={
            "role_code": role_code,
            "scope_type": scope_type,
            "scope_id": str(scope_id) if scope_id else None,
        },
    )
    return assignment


def end_role_assignment(
    db: Session, assignment: RoleAssignment, *, ended_by: uuid.UUID, reason_code: str
) -> RoleAssignment:
    authz.assert_not_self(ended_by, assignment.user_id)
    now = utcnow()
    assignment.status = "ended"
    assignment.effective_to = now
    assignment.ended_at = now
    authz.invalidate_sessions_on_privilege_change(db, assignment.user_id)
    record_audit(
        db, action="workforce.role.end", result="success", actor_user_id=ended_by,
        target_type="user", target_id=assignment.user_id, reason_code=reason_code,
        event_metadata={"role_code": assignment.role_code},
    )
    return assignment


def _get_or_create_default_organization(db: Session, *, created_by: uuid.UUID) -> Organization:
    """D-07: single organization, no multi-tenant. Nothing in this build has ever
    needed to materialize that row until team creation's NOT NULL foreign key - every
    other scope check already treats scope_id=None as "the" organization."""
    existing = db.scalar(select(Organization).limit(1))
    if existing is not None:
        return existing
    organization = Organization(name=_DEFAULT_ORGANIZATION_NAME, status="active")
    db.add(organization)
    db.flush()
    record_audit(
        db, action="workforce.organization.create", result="success", actor_user_id=created_by,
        target_type="organization", target_id=organization.id,
    )
    return organization


def create_team(
    db: Session,
    *,
    name: str,
    external_code: str,
    parent_team_id: uuid.UUID | None,
    default_timezone: str,
    created_by: uuid.UUID,
) -> Team:
    organization = _get_or_create_default_organization(db, created_by=created_by)
    team = Team(
        organization_id=organization.id,
        name=name,
        external_code=external_code,
        parent_team_id=parent_team_id,
        default_timezone=default_timezone,
    )
    db.add(team)
    db.flush()
    record_audit(
        db, action="workforce.team.create", result="success", actor_user_id=created_by,
        target_type="team", target_id=team.id,
    )
    return team


def add_team_membership(
    db: Session, team: Team, *, user_id: uuid.UUID, added_by: uuid.UUID
) -> TeamMembership:
    existing = db.scalar(
        select(TeamMembership).where(
            TeamMembership.team_id == team.id,
            TeamMembership.user_id == user_id,
            TeamMembership.membership_status == "active",
            TeamMembership.effective_to.is_(None),
        )
    )
    if existing is not None:
        return existing
    membership = TeamMembership(
        team_id=team.id, user_id=user_id, effective_from=utcnow(), created_by=added_by,
    )
    db.add(membership)
    db.flush()
    record_audit(
        db, action="workforce.team.add_member", result="success", actor_user_id=added_by,
        target_type="team", target_id=team.id, event_metadata={"user_id": str(user_id)},
    )
    return membership


def end_team_membership(
    db: Session,
    membership: TeamMembership,
    *,
    ended_by: uuid.UUID,
    reason_code: str | None = None,
) -> TeamMembership:
    now = utcnow()
    membership.membership_status = "ended"
    membership.effective_to = now
    membership.ended_at = now
    record_audit(
        db, action="workforce.team.end_member", result="success", actor_user_id=ended_by,
        target_type="team", target_id=membership.team_id, reason_code=reason_code,
        event_metadata={"user_id": str(membership.user_id)},
    )
    return membership


def set_reporting_line(
    db: Session,
    *,
    subordinate_user_id: uuid.UUID,
    supervisor_user_id: uuid.UUID,
    context_type: str = "organization",
    context_id: uuid.UUID | None = None,
    assignment_type: str = "primary",
    assigned_by: uuid.UUID,
    reason_code: str | None = None,
) -> ReportingAssignment:
    if subordinate_user_id == supervisor_user_id:
        raise SelfSupervision("a user may not supervise themselves")
    now = utcnow()
    if assignment_type == "primary":
        # Mirrors the one-active-primary-campaign-assignment rule (D-17): a primary
        # reporting line does not stack, it supersedes. Non-primary (acting, dotted-
        # line) assignments may coexist - the partial unique index only covers primary.
        prior = db.scalars(
            select(ReportingAssignment).where(
                ReportingAssignment.subordinate_user_id == subordinate_user_id,
                ReportingAssignment.context_type == context_type,
                ReportingAssignment.context_id == context_id,
                ReportingAssignment.assignment_type == "primary",
                ReportingAssignment.status == "active",
                ReportingAssignment.effective_to.is_(None),
            )
        )
        for existing in prior:
            existing.status = "ended"
            existing.effective_to = now
            existing.ended_at = now
    line = ReportingAssignment(
        subordinate_user_id=subordinate_user_id,
        supervisor_user_id=supervisor_user_id,
        context_type=context_type,
        context_id=context_id,
        assignment_type=assignment_type,
        effective_from=now,
        assigned_by=assigned_by,
        reason_code=reason_code,
    )
    db.add(line)
    db.flush()
    record_audit(
        db, action="workforce.reporting_line.set", result="success", actor_user_id=assigned_by,
        target_type="user", target_id=subordinate_user_id, reason_code=reason_code,
        event_metadata={"supervisor_user_id": str(supervisor_user_id)},
    )
    return line
