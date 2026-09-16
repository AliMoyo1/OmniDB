"""Workforce hierarchy: users, roles, teams, and reporting lines (plan 6, Phase 4A).

Default deny, no unbounded inheritance (plan 6.4): every role grant is checked
against the actor's own scoped capability for that specific target role, not a
generic "manage roles" shortcut. super_admin is never assignable here - it stays a
manual/ops-provisioned role, consistent with how the first Manager account in this
build was created outside the app.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, case, func, or_, select
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
    RESET_USER_AUTH,
    ROLE_AGENT,
    ROLE_CAPABILITIES,
    ROLE_MANAGER,
    ROLE_TEAM_CAPTAIN,
    ROLE_TEAM_LEADER,
    ROLE_VIEWER,
)
from app.flags import service as flags
from app.models.activation import ActivationToken
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
    means the actor has no appointment capability with a resolvable scope at all.

    Coverage must mirror the authorization core (_scope_assignment_covers_target): an
    installation or null-organization grant sees everyone; a grant scoped to a specific
    organization covers every team in that organization (resolved below); a team grant
    covers exactly that team. An org-scoped grant that resolved to nothing here would
    hide teams the actor is genuinely authorized over - fail-closed, but wrong."""
    appointment_capabilities = set(ROLE_APPOINTMENT_CAPABILITY.values())
    sees_everyone = False
    team_ids: set[uuid.UUID] = set()
    organization_ids: set[uuid.UUID] = set()
    for assignment in authz.effective_role_assignments(db, actor_id):
        if not ROLE_CAPABILITIES.get(assignment.role_code, set()) & appointment_capabilities:
            continue
        if assignment.scope_type == "installation":
            sees_everyone = True
        elif assignment.scope_type == "organization" and assignment.scope_id is None:
            sees_everyone = True
        elif assignment.scope_type == "organization" and assignment.scope_id is not None:
            organization_ids.add(assignment.scope_id)
        elif assignment.scope_type == "team" and assignment.scope_id is not None:
            team_ids.add(assignment.scope_id)
    if not sees_everyone and organization_ids:
        team_ids |= set(
            db.scalars(
                select(Team.id).where(
                    Team.organization_id.in_(organization_ids), Team.status == "active"
                )
            )
        )
    return sees_everyone, team_ids


def list_visible_users(db: Session, actor_id: uuid.UUID, *, limit: int = 50) -> list[User]:
    """Shared by the JSON endpoint and the dashboard page - one scoping
    implementation, not two that could quietly drift apart."""
    sees_everyone, team_ids = visible_team_ids(db, actor_id)
    if sees_everyone:
        return list(db.scalars(select(User).order_by(User.created_at.desc()).limit(limit)))
    if not team_ids:
        return []
    return list(
        db.scalars(
            select(User)
            .join(TeamMembership, TeamMembership.user_id == User.id)
            .where(
                TeamMembership.team_id.in_(team_ids),
                TeamMembership.membership_status == "active",
            )
            .distinct()
            .order_by(User.created_at.desc())
            .limit(limit)
        )
    )


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
    issue_activation: bool = True,
) -> tuple[User, str | None]:
    """Create an identity with no password and no role (ADR-005C: workforce_id is the
    email's local part, immutable once set). Returns (user, activation_token) - the
    same one-time-activation pattern already used for a Super Admin password reset.

    issue_activation=True is the default so every existing single-user caller
    keeps issuing a token exactly as before. Bulk-import commit is the only
    caller expected to pass issue_activation=False, and only once the
    deferred-activation flag is enabled (plan 6.3/6.5) - a deferred identity is
    marked "Activation not issued" until an administrator issues one on demand
    (app/auth/service.py's issue_or_replace_activation_code)."""
    derived_id = workforce_id or email.split("@")[0]
    existing = db.scalar(
        select(User.id).where(or_(User.email == email, User.workforce_id == derived_id))
    )
    if existing is not None:
        raise DuplicateIdentity("a user with this email or workforce ID already exists")
    user = User(workforce_id=derived_id, email=email, display_name=display_name)
    db.add(user)
    db.flush()
    record_audit(
        db, action="workforce.user.create", result="success", actor_user_id=created_by,
        target_type="user", target_id=user.id,
    )
    token: str | None = None
    if issue_activation:
        token = issue_activation_token(db, user.id, created_by=created_by)
        # Separate from the identity-creation event above (plan 6.3): a later
        # reader should be able to tell "an identity was created" apart from
        # "a code was issued" without inferring it from token presence alone.
        record_audit(
            db, action="workforce.user.activation_issued", result="success",
            actor_user_id=created_by, target_type="user", target_id=user.id,
        )
    return user, token


_UPDATABLE_USER_FIELDS = {"display_name", "start_date", "end_date"}


def update_user(
    db: Session, target: User, *, changes: dict, actor_id: uuid.UUID, reason_code: str | None = None
) -> User:
    """Apply a bounded set of non-identity field changes (bulk-import commit path).

    Deliberately excludes workforce_id, email, and workforce_status: the first two are
    the immutable identity (ADR-005C), and workforce_status is only ever changed
    together with `active` and session/lease revocation by disable_user/reactivate_user,
    never as a bare field write.
    """
    unknown = set(changes) - _UPDATABLE_USER_FIELDS
    if unknown:
        raise ValueError(f"update_user cannot change: {', '.join(sorted(unknown))}")
    for field, value in changes.items():
        setattr(target, field, value)
    record_audit(
        db, action="workforce.user.update", result="success", actor_user_id=actor_id,
        target_type="user", target_id=target.id, reason_code=reason_code,
        event_metadata={"fields": sorted(changes)},
    )
    return target


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
    if role_code == ROLE_VIEWER:
        # A rollout gate on new grants, not a live kill switch: existing Viewer
        # assignments keep working even while this is off, matching the
        # master plan's flags as "ready to onboard this capability," not an
        # incident switch (that's shared_pool_enabled's job for leasing).
        flags.require_enabled(db, "viewer_enabled")
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
    # This user may have delegated authority they held through the role just ended.
    # Those delegations stop granting the moment the backing role is gone (authz
    # revalidates the delegator's current role authority at read time), but refresh
    # the delegates' sessions too so their privilege state is re-derived at once.
    authz.invalidate_delegate_sessions_for_delegator(db, assignment.user_id)
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


def end_reporting_line(
    db: Session,
    line: ReportingAssignment,
    *,
    ended_by: uuid.UUID,
    reason_code: str | None = None,
) -> ReportingAssignment:
    now = utcnow()
    line.status = "ended"
    line.effective_to = now
    line.ended_at = now
    record_audit(
        db, action="workforce.reporting_line.end", result="success", actor_user_id=ended_by,
        target_type="user", target_id=line.subordinate_user_id, reason_code=reason_code,
        event_metadata={"supervisor_user_id": str(line.supervisor_user_id)},
    )
    return line


# --- Administrative user directory (admin user management plan, phase A) -----------
#
# One shared scoped/filtered/paginated query and one shared activation-state
# expression, reused by both the row-level page and the summary aggregate so the
# two can never quietly drift apart (the same principle as list_visible_users
# above and list_visible_audit_events in app/api/admin.py).

STATE_DISABLED = "disabled"
STATE_ACTIVATION_NOT_ISSUED = "activation_not_issued"
STATE_ACTIVATION_CODE_ACTIVE = "activation_code_active"
STATE_ACTIVATION_CODE_EXPIRED = "activation_code_expired"
STATE_MFA_SETUP_REQUIRED = "mfa_setup_required"
STATE_READY = "ready"

ACTIVATION_STATE_LABELS: dict[str, str] = {
    STATE_DISABLED: "Disabled",
    STATE_ACTIVATION_NOT_ISSUED: "Activation not issued",
    STATE_ACTIVATION_CODE_ACTIVE: "Activation code active",
    STATE_ACTIVATION_CODE_EXPIRED: "Activation code expired",
    STATE_MFA_SETUP_REQUIRED: "MFA setup required",
    STATE_READY: "Ready",
}

_AWAITING_ACTIVATION_STATES = (
    STATE_ACTIVATION_NOT_ISSUED,
    STATE_ACTIVATION_CODE_ACTIVE,
    STATE_ACTIVATION_CODE_EXPIRED,
)

DIRECTORY_PAGE_SIZE_DEFAULT = 50
DIRECTORY_PAGE_SIZE_MAX = 100
DIRECTORY_SORTS = ("name", "recent")


@dataclass
class DirectoryRow:
    user: User
    roles: list[str]
    teams: list[str]
    activation_state: str


@dataclass
class DirectoryPage:
    rows: list[DirectoryRow]
    total_count: int
    sees_everyone: bool


@dataclass
class DirectorySummary:
    total: int
    active: int
    inactive: int
    awaiting_activation: int
    mfa_setup_required: int
    never_logged_in: int


def can_view_admin_directory(db: Session, actor_id: uuid.UUID) -> bool:
    """Plan 5.1: the /admin/users directory may be opened by anyone holding
    credential-administration authority (RESET_USER_AUTH) or an applicable
    workforce appointment capability - shared by the nav-visibility flag and the
    route itself so they can't drift apart."""
    if authz.has_assigned_capability(db, actor_id, RESET_USER_AUTH):
        return True
    return any(
        authz.has_assigned_capability(db, actor_id, capability)
        for capability in ROLE_APPOINTMENT_CAPABILITY.values()
    )


def _directory_scope(db: Session, actor_id: uuid.UUID) -> tuple[bool, set[uuid.UUID]]:
    """Plan 6.1: Super Administrator visibility is RESET_USER_AUTH; every other
    role reuses the same team-scoped visibility already used for the workforce
    list (visible_team_ids). A filter narrows this; it must never widen it."""
    sees_everyone, team_ids = visible_team_ids(db, actor_id)
    if not sees_everyone and authz.has_assigned_capability(db, actor_id, RESET_USER_AUTH):
        sees_everyone = True
    return sees_everyone, team_ids


def _activation_state_expression(now: datetime):
    """A single CASE expression, reused by both the row-level query and the
    summary aggregate below. Priority order matches plan 5.2 exactly: disabled
    overrides everything, then password/MFA state, then the pre-password
    activation-token state. Issuing a new token invalidates every prior unused
    one (issue_activation_token), so at most one unused token can exist per user
    - this correlated subquery only needs its latest row."""
    unused_token_expiry = (
        select(ActivationToken.expires_at)
        .where(
            ActivationToken.user_id == User.id,
            ActivationToken.used_at.is_(None),
            ActivationToken.purpose == "password_activation",
        )
        .order_by(ActivationToken.expires_at.desc())
        .limit(1)
        .correlate(User)
        .scalar_subquery()
    )
    return case(
        (User.active.is_(False), STATE_DISABLED),
        (and_(User.password_hash.is_not(None), User.totp_enrolled.is_(True)), STATE_READY),
        (User.password_hash.is_not(None), STATE_MFA_SETUP_REQUIRED),
        (unused_token_expiry.is_(None), STATE_ACTIVATION_NOT_ISSUED),
        (unused_token_expiry > now, STATE_ACTIVATION_CODE_ACTIVE),
        else_=STATE_ACTIVATION_CODE_EXPIRED,
    )


def _effective_role_window(now: datetime) -> tuple:
    return (
        RoleAssignment.status == "active",
        RoleAssignment.effective_from <= now,
        or_(RoleAssignment.effective_to.is_(None), RoleAssignment.effective_to > now),
    )


def _roles_for_users(
    db: Session, user_ids: list[uuid.UUID], now: datetime
) -> dict[uuid.UUID, list[str]]:
    """One bounded query for the whole page, never one per row (plan 5.2/12)."""
    rows = db.execute(
        select(
            RoleAssignment.user_id,
            RoleAssignment.role_code,
            RoleAssignment.scope_type,
            RoleAssignment.scope_id,
        ).where(RoleAssignment.user_id.in_(user_ids), *_effective_role_window(now))
    ).all()
    team_scope_ids = {row.scope_id for row in rows if row.scope_type == "team" and row.scope_id}
    team_names: dict[uuid.UUID, str] = {
        team_row.id: team_row.name
        for team_row in db.execute(select(Team.id, Team.name).where(Team.id.in_(team_scope_ids)))
    }
    result: dict[uuid.UUID, list[str]] = {}
    for row in rows:
        if row.scope_type == "team" and row.scope_id:
            label = f"{row.role_code} ({team_names.get(row.scope_id, 'unknown team')})"
        elif row.scope_type == "organization" and row.scope_id:
            label = f"{row.role_code} (org-scoped)"
        else:
            label = row.role_code
        result.setdefault(row.user_id, []).append(label)
    return result


def _teams_for_users(db: Session, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    """One bounded query for the whole page, never one per row (plan 5.2/12)."""
    rows = db.execute(
        select(TeamMembership.user_id, Team.name)
        .join(Team, TeamMembership.team_id == Team.id)
        .where(
            TeamMembership.user_id.in_(user_ids),
            TeamMembership.membership_status == "active",
        )
        .order_by(Team.name)
    ).all()
    result: dict[uuid.UUID, list[str]] = {}
    for user_id, team_name in rows:
        result.setdefault(user_id, []).append(team_name)
    return result


def list_user_directory(
    db: Session,
    actor_id: uuid.UUID,
    *,
    search: str | None = None,
    status: str | None = None,
    activation_state: str | None = None,
    role: str | None = None,
    team_id: uuid.UUID | None = None,
    page: int = 1,
    page_size: int = DIRECTORY_PAGE_SIZE_DEFAULT,
    sort: str = "name",
) -> DirectoryPage:
    """The shared scoped, filtered, paginated directory query (plan 6.1). Begins
    with the actor's own authorized scope and only narrows from there - a filter
    must never be able to widen it back out. Sorting is always (key, id) so a
    person is never duplicated or skipped while paging (plan 5.1)."""
    sees_everyone, team_ids = _directory_scope(db, actor_id)
    if not sees_everyone and not team_ids:
        return DirectoryPage(rows=[], total_count=0, sees_everyone=False)

    page = max(page, 1)
    page_size = min(max(page_size, 1), DIRECTORY_PAGE_SIZE_MAX)
    now = utcnow()
    state_expr = _activation_state_expression(now)

    stmt = select(User)
    if not sees_everyone:
        stmt = (
            stmt.join(TeamMembership, TeamMembership.user_id == User.id)
            .where(
                TeamMembership.team_id.in_(team_ids),
                TeamMembership.membership_status == "active",
            )
            .distinct()
        )
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                User.display_name.ilike(pattern),
                User.email.ilike(pattern),
                User.workforce_id.ilike(pattern),
            )
        )
    if status in ("active", "inactive"):
        stmt = stmt.where(User.active.is_(status == "active"))
    if role and role in ROLE_CAPABILITIES:
        stmt = stmt.where(
            User.id.in_(
                select(RoleAssignment.user_id).where(
                    RoleAssignment.role_code == role, *_effective_role_window(now)
                )
            )
        )
    if team_id is not None:
        stmt = stmt.where(
            User.id.in_(
                select(TeamMembership.user_id).where(
                    TeamMembership.team_id == team_id,
                    TeamMembership.membership_status == "active",
                )
            )
        )
    if activation_state in ACTIVATION_STATE_LABELS:
        stmt = stmt.where(state_expr == activation_state)

    total_count = (
        db.scalar(select(func.count()).select_from(stmt.with_only_columns(User.id).subquery()))
        or 0
    )

    order_cols = (
        (User.created_at.desc(), User.id.desc())
        if sort == "recent"
        else (User.display_name.asc(), User.id.asc())
    )
    rows_stmt = (
        stmt.add_columns(state_expr.label("activation_state"))
        .order_by(*order_cols)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    page_rows = db.execute(rows_stmt).all()
    if not page_rows:
        return DirectoryPage(rows=[], total_count=total_count, sees_everyone=sees_everyone)

    page_ids = [row.User.id for row in page_rows]
    roles_by_user = _roles_for_users(db, page_ids, now)
    teams_by_user = _teams_for_users(db, page_ids)
    rows = [
        DirectoryRow(
            user=row.User,
            roles=roles_by_user.get(row.User.id, []),
            teams=teams_by_user.get(row.User.id, []),
            activation_state=row.activation_state,
        )
        for row in page_rows
    ]
    return DirectoryPage(rows=rows, total_count=total_count, sees_everyone=sees_everyone)


def directory_summary(db: Session, actor_id: uuid.UUID) -> DirectorySummary:
    """Plan 5.1's summary cards: one aggregate query over the actor's full visible
    scope, independent of whatever the table's filters currently narrow to."""
    sees_everyone, team_ids = _directory_scope(db, actor_id)
    if not sees_everyone and not team_ids:
        return DirectorySummary(0, 0, 0, 0, 0, 0)

    now = utcnow()
    state_expr = _activation_state_expression(now)
    base = select(
        User.id.label("id"),
        User.active.label("active"),
        User.last_login_at.label("last_login_at"),
        state_expr.label("state"),
    )
    if not sees_everyone:
        base = base.join(TeamMembership, TeamMembership.user_id == User.id).where(
            TeamMembership.team_id.in_(team_ids),
            TeamMembership.membership_status == "active",
        )
    sub = base.distinct().subquery()
    aggregate = select(
        func.count().label("total"),
        func.sum(case((sub.c.active.is_(True), 1), else_=0)).label("active"),
        func.sum(case((sub.c.active.is_(False), 1), else_=0)).label("inactive"),
        func.sum(case((sub.c.state.in_(_AWAITING_ACTIVATION_STATES), 1), else_=0)).label(
            "awaiting_activation"
        ),
        func.sum(case((sub.c.state == STATE_MFA_SETUP_REQUIRED, 1), else_=0)).label(
            "mfa_setup_required"
        ),
        func.sum(case((sub.c.last_login_at.is_(None), 1), else_=0)).label("never_logged_in"),
    ).select_from(sub)
    row = db.execute(aggregate).one()
    return DirectorySummary(
        total=row.total or 0,
        active=row.active or 0,
        inactive=row.inactive or 0,
        awaiting_activation=row.awaiting_activation or 0,
        mfa_setup_required=row.mfa_setup_required or 0,
        never_logged_in=row.never_logged_in or 0,
    )


def user_in_admin_scope(db: Session, actor_id: uuid.UUID, target_id: uuid.UUID) -> bool:
    """Whether target_id falls within actor_id's directory scope (plan 6.1) - the
    same check the detail page uses to decide whether to show a target at all,
    so a route can return the same response for "inaccessible" and "nonexistent"
    (plan 5.3/9.5) without a second, potentially drifting scope implementation."""
    sees_everyone, team_ids = _directory_scope(db, actor_id)
    if sees_everyone:
        return True
    if not team_ids:
        return False
    return (
        db.scalar(
            select(TeamMembership.id).where(
                TeamMembership.user_id == target_id,
                TeamMembership.team_id.in_(team_ids),
                TeamMembership.membership_status == "active",
            )
        )
        is not None
    )


def activation_state_for_user(db: Session, user_id: uuid.UUID) -> str:
    """Single-user variant of the same expression list_user_directory and
    directory_summary use, for the detail page - one CASE, three call sites,
    never three implementations to keep in sync."""
    now = utcnow()
    state = db.scalar(
        select(_activation_state_expression(now)).where(User.id == user_id)
    )
    return state or STATE_ACTIVATION_NOT_ISSUED


def roles_and_teams_for_user(
    db: Session, user_id: uuid.UUID
) -> tuple[list[str], list[str]]:
    """Single-user convenience wrapper over the same bounded, page-shaped
    lookups list_user_directory uses - still one query each, just for a page of
    one instead of fifty."""
    now = utcnow()
    roles = _roles_for_users(db, [user_id], now).get(user_id, [])
    teams = _teams_for_users(db, [user_id]).get(user_id, [])
    return roles, teams
