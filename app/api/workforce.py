"""Workforce API (/api/v1/workforce): users, roles, teams, reporting lines.

Kept separate from /api/v1/admin, which stays the Super Admin technical surface
(password/2FA reset, unscoped audit search). This is the business-hierarchy surface
Manager, Team Leader, and Team Captain use day to day (plan section 6).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_csrf
from app.authz import service as authz
from app.authz.capabilities import APPOINT_TEAM_CAPTAIN, MANAGE_ROLES
from app.db import get_session
from app.flags.service import FeatureDisabledError
from app.models.authz import ReportingAssignment, RoleAssignment
from app.models.identity import Team, TeamMembership, User
from app.workforce import service as workforce_service
from app.workforce.schemas import (
    DisableUserRequest,
    ReportingAssignmentOut,
    ReportingLineRequest,
    RoleAssignmentOut,
    RoleAssignRequest,
    RoleEndRequest,
    TeamCreateRequest,
    TeamMembershipOut,
    TeamMembershipRequest,
    TeamOut,
    UserCreateOut,
    UserCreateRequest,
    UserOut,
)
from app.workforce.service import (
    ROLE_APPOINTMENT_CAPABILITY,
    DuplicateIdentity,
    SelfSupervision,
    UnknownRole,
)

router = APIRouter(prefix="/api/v1/workforce", tags=["workforce"])


def _require_any_appointment_capability(
    db: Session = Depends(get_session), user: User = Depends(get_current_user)
) -> User:
    if not any(
        authz.has_assigned_capability(db, user.id, capability)
        for capability in ROLE_APPOINTMENT_CAPABILITY.values()
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized")
    return user


def _load_user_or_404(db: Session, user_id: uuid.UUID) -> User:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return target


def _require_can_manage(db: Session, actor: User, target: User) -> None:
    if not workforce_service.can_manage_user(db, actor.id, target.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized")


def _user_out(u: User) -> UserOut:
    return UserOut(
        id=str(u.id), email=u.email, display_name=u.display_name, workforce_id=u.workforce_id,
        active=u.active, workforce_status=u.workforce_status, created_at=u.created_at,
    )


def _role_out(ra: RoleAssignment) -> RoleAssignmentOut:
    return RoleAssignmentOut(
        id=str(ra.id), user_id=str(ra.user_id), role_code=ra.role_code,
        scope_type=ra.scope_type, scope_id=str(ra.scope_id) if ra.scope_id else None,
        status=ra.status, effective_from=ra.effective_from, effective_to=ra.effective_to,
    )


def _team_out(t: Team) -> TeamOut:
    return TeamOut(
        id=str(t.id), name=t.name, external_code=t.external_code,
        parent_team_id=str(t.parent_team_id) if t.parent_team_id else None,
        default_timezone=t.default_timezone, status=t.status,
    )


def _membership_out(m: TeamMembership) -> TeamMembershipOut:
    return TeamMembershipOut(
        id=str(m.id), team_id=str(m.team_id), user_id=str(m.user_id),
        membership_status=m.membership_status, effective_from=m.effective_from,
        effective_to=m.effective_to,
    )


def _reporting_out(r: ReportingAssignment) -> ReportingAssignmentOut:
    return ReportingAssignmentOut(
        id=str(r.id), subordinate_user_id=str(r.subordinate_user_id),
        supervisor_user_id=str(r.supervisor_user_id), context_type=r.context_type,
        context_id=str(r.context_id) if r.context_id else None,
        assignment_type=r.assignment_type, status=r.status,
        effective_from=r.effective_from, effective_to=r.effective_to,
    )


@router.post("/users", response_model=UserCreateOut, dependencies=[Depends(require_csrf)])
def create_user(
    payload: UserCreateRequest,
    db: Session = Depends(get_session),
    actor: User = Depends(_require_any_appointment_capability),
) -> UserCreateOut:
    try:
        user, token = workforce_service.create_user(
            db, email=payload.email, display_name=payload.display_name,
            workforce_id=payload.workforce_id, created_by=actor.id,
        )
    except DuplicateIdentity as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    db.commit()
    return UserCreateOut(
        id=str(user.id), email=user.email, display_name=user.display_name,
        workforce_id=user.workforce_id, activation_token=token,
    )


@router.get("/users", response_model=list[UserOut])
def list_users(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
    actor: User = Depends(_require_any_appointment_capability),
) -> list[UserOut]:
    users = workforce_service.list_visible_users(db, actor.id, limit=limit)
    return [_user_out(u) for u in users]


@router.post(
    "/users/{user_id}/disable", response_model=UserOut, dependencies=[Depends(require_csrf)]
)
def disable_user(
    user_id: uuid.UUID,
    payload: DisableUserRequest,
    db: Session = Depends(get_session),
    actor: User = Depends(get_current_user),
) -> UserOut:
    target = _load_user_or_404(db, user_id)
    _require_can_manage(db, actor, target)
    try:
        workforce_service.disable_user(
            db, target, actor_id=actor.id, reason_code=payload.reason_code
        )
    except authz.SelfApprovalError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    db.commit()
    return _user_out(target)


@router.post(
    "/users/{user_id}/reactivate", response_model=UserOut, dependencies=[Depends(require_csrf)]
)
def reactivate_user(
    user_id: uuid.UUID,
    payload: DisableUserRequest,
    db: Session = Depends(get_session),
    actor: User = Depends(get_current_user),
) -> UserOut:
    target = _load_user_or_404(db, user_id)
    _require_can_manage(db, actor, target)
    workforce_service.reactivate_user(
        db, target, actor_id=actor.id, reason_code=payload.reason_code
    )
    db.commit()
    return _user_out(target)


@router.get("/users/{user_id}/roles", response_model=list[RoleAssignmentOut])
def list_user_roles(
    user_id: uuid.UUID,
    db: Session = Depends(get_session),
    actor: User = Depends(_require_any_appointment_capability),
) -> list[RoleAssignmentOut]:
    target = _load_user_or_404(db, user_id)
    rows = db.scalars(
        select(RoleAssignment)
        .where(RoleAssignment.user_id == target.id)
        .order_by(RoleAssignment.created_at.desc())
    )
    return [_role_out(ra) for ra in rows]


@router.post(
    "/users/{user_id}/roles",
    response_model=RoleAssignmentOut,
    dependencies=[Depends(require_csrf)],
)
def assign_role(
    user_id: uuid.UUID,
    payload: RoleAssignRequest,
    db: Session = Depends(get_session),
    actor: User = Depends(get_current_user),
) -> RoleAssignmentOut:
    target = _load_user_or_404(db, user_id)
    capability = ROLE_APPOINTMENT_CAPABILITY.get(payload.role_code)
    if capability is None or not authz.has_scope_capability(
        db, actor.id, capability, scope_type=payload.scope_type, scope_id=payload.scope_id
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized")
    if payload.scope_type == "team":
        team = db.get(Team, payload.scope_id)
        if team is None or team.status != "active":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid scope team")
    try:
        assignment = workforce_service.assign_role(
            db, target_user_id=target.id, role_code=payload.role_code,
            scope_type=payload.scope_type, scope_id=payload.scope_id,
            appointed_by=actor.id, reason_code=payload.reason_code,
        )
    except UnknownRole as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except authz.SelfApprovalError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    except FeatureDisabledError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    db.commit()
    return _role_out(assignment)


@router.post(
    "/roles/{role_assignment_id}/end",
    response_model=RoleAssignmentOut,
    dependencies=[Depends(require_csrf)],
)
def end_role_assignment(
    role_assignment_id: uuid.UUID,
    payload: RoleEndRequest,
    db: Session = Depends(get_session),
    actor: User = Depends(get_current_user),
) -> RoleAssignmentOut:
    assignment = db.get(RoleAssignment, role_assignment_id)
    if assignment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "role assignment not found")
    capability = ROLE_APPOINTMENT_CAPABILITY.get(assignment.role_code)
    if capability is None or not authz.has_scope_capability(
        db, actor.id, capability,
        scope_type=assignment.scope_type, scope_id=assignment.scope_id,
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized")
    try:
        workforce_service.end_role_assignment(
            db, assignment, ended_by=actor.id, reason_code=payload.reason_code
        )
    except authz.SelfApprovalError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
    db.commit()
    return _role_out(assignment)


@router.post("/teams", response_model=TeamOut, dependencies=[Depends(require_csrf)])
def create_team(
    payload: TeamCreateRequest,
    db: Session = Depends(get_session),
    actor: User = Depends(get_current_user),
) -> TeamOut:
    if not authz.has_assigned_capability(db, actor.id, MANAGE_ROLES):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized")
    team = workforce_service.create_team(
        db, name=payload.name, external_code=payload.external_code,
        parent_team_id=payload.parent_team_id, default_timezone=payload.default_timezone,
        created_by=actor.id,
    )
    db.commit()
    return _team_out(team)


@router.get("/teams", response_model=list[TeamOut])
def list_teams(
    db: Session = Depends(get_session),
    actor: User = Depends(_require_any_appointment_capability),
) -> list[TeamOut]:
    rows = db.scalars(select(Team).where(Team.status == "active").order_by(Team.name))
    return [_team_out(t) for t in rows]


def _load_team_or_404(db: Session, team_id: uuid.UUID) -> Team:
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "team not found")
    return team


def _require_team_capability(db: Session, actor: User, team: Team) -> None:
    if not authz.has_scope_capability(
        db, actor.id, APPOINT_TEAM_CAPTAIN, scope_type="team", scope_id=team.id
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized")


@router.post(
    "/teams/{team_id}/members",
    response_model=TeamMembershipOut,
    dependencies=[Depends(require_csrf)],
)
def add_team_member(
    team_id: uuid.UUID,
    payload: TeamMembershipRequest,
    db: Session = Depends(get_session),
    actor: User = Depends(get_current_user),
) -> TeamMembershipOut:
    team = _load_team_or_404(db, team_id)
    _require_team_capability(db, actor, team)
    _load_user_or_404(db, payload.user_id)
    membership = workforce_service.add_team_membership(
        db, team, user_id=payload.user_id, added_by=actor.id
    )
    db.commit()
    return _membership_out(membership)


@router.post(
    "/memberships/{membership_id}/end",
    response_model=TeamMembershipOut,
    dependencies=[Depends(require_csrf)],
)
def end_team_membership(
    membership_id: uuid.UUID,
    db: Session = Depends(get_session),
    actor: User = Depends(get_current_user),
) -> TeamMembershipOut:
    membership = db.get(TeamMembership, membership_id)
    if membership is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "membership not found")
    team = _load_team_or_404(db, membership.team_id)
    _require_team_capability(db, actor, team)
    workforce_service.end_team_membership(db, membership, ended_by=actor.id)
    db.commit()
    return _membership_out(membership)


@router.post(
    "/users/{user_id}/reporting-line",
    response_model=ReportingAssignmentOut,
    dependencies=[Depends(require_csrf)],
)
def set_reporting_line(
    user_id: uuid.UUID,
    payload: ReportingLineRequest,
    db: Session = Depends(get_session),
    actor: User = Depends(get_current_user),
) -> ReportingAssignmentOut:
    target = _load_user_or_404(db, user_id)
    _require_can_manage(db, actor, target)
    _load_user_or_404(db, payload.supervisor_user_id)
    try:
        line = workforce_service.set_reporting_line(
            db, subordinate_user_id=target.id, supervisor_user_id=payload.supervisor_user_id,
            context_type=payload.context_type, context_id=payload.context_id,
            assignment_type=payload.assignment_type, assigned_by=actor.id,
            reason_code=payload.reason_code,
        )
    except SelfSupervision as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    db.commit()
    return _reporting_out(line)
