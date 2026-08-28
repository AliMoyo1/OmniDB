"""Server-rendered workforce operations: users, roles, teams, reporting lines.

Thin browser layer over the existing workforce service and its JSON API
(app/api/workforce.py) - every action here calls the same, already-tested service
functions, not a reimplementation. Rounds out the same list+detail "control room"
pattern app/web/campaigns.py established: a list page for users and teams, and a
detail page per user/team for the lifecycle actions (disable/reactivate a user, end
a role assignment, end a team membership, set a reporting line) the original
dashboard-embedded sections only ever exposed the create half of.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.authz import service as authz
from app.authz.capabilities import APPOINT_TEAM_CAPTAIN, MANAGE_ROLES
from app.db import get_session
from app.models.authz import ReportingAssignment, RoleAssignment
from app.models.identity import Team, TeamMembership, User
from app.web.dependencies import require_page_user, verify_form_csrf
from app.web.templates import page_context, templates
from app.workforce import service as workforce_service
from app.workforce.service import (
    ROLE_APPOINTMENT_CAPABILITY,
    DuplicateIdentity,
    SelfSupervision,
    UnknownRole,
)

router = APIRouter(prefix="/workforce", tags=["web-workforce"])

_DEFAULT_TIMEZONE = "Africa/Harare"


def _redirect(path: str, *, success: str | None = None, error: str | None = None):
    params = {
        key: value for key, value in (("flash_success", success), ("flash_error", error)) if value
    }
    return RedirectResponse(path + ("?" + urlencode(params) if params else ""), status_code=303)


def _index_redirect(*, error: str):
    return _redirect("/workforce", error=error)


def _user_redirect(user_id: uuid.UUID, *, success: str | None = None, error: str | None = None):
    return _redirect(f"/workforce/users/{user_id}", success=success, error=error)


def _team_redirect(team_id: uuid.UUID, *, success: str | None = None, error: str | None = None):
    return _redirect(f"/workforce/teams/{team_id}", success=success, error=error)


def _any_appointment_capability(db: Session, user: User) -> bool:
    return any(
        authz.has_assigned_capability(db, user.id, capability)
        for capability in ROLE_APPOINTMENT_CAPABILITY.values()
    )


@router.get("")
def workforce_list(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    can_manage_workforce = _any_appointment_capability(db, user)
    can_manage_teams = authz.has_assigned_capability(db, user.id, MANAGE_ROLES)
    if not can_manage_workforce and not can_manage_teams:
        return RedirectResponse("/dashboard?flash_error=Not+authorized+for+workforce.", 303)

    workforce_users = (
        workforce_service.list_visible_users(db, user.id, limit=100) if can_manage_workforce else []
    )
    user_roles = {u.id: sorted(authz.effective_roles(db, u.id)) for u in workforce_users}

    teams = list(
        db.scalars(select(Team).where(Team.status == "active").order_by(Team.name).limit(100))
    )

    context = page_context(
        request, db, user,
        active_section="workforce",
        workforce_users=workforce_users,
        user_roles=user_roles,
        teams=teams,
        can_manage_workforce=can_manage_workforce,
        can_manage_teams=can_manage_teams,
        appointable_roles=sorted(ROLE_APPOINTMENT_CAPABILITY.keys()),
        default_timezone=_DEFAULT_TIMEZONE,
        flash_error=request.query_params.get("flash_error"),
        flash_success=request.query_params.get("flash_success"),
    )
    return templates.TemplateResponse(request, "workforce_list.html", context)


@router.post("/users", dependencies=[Depends(verify_form_csrf)])
def create_user_action(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    email: str = Form(...),
    display_name: str = Form(...),
):
    if not _any_appointment_capability(db, user):
        return _index_redirect(error="Not authorized to create a user.")
    try:
        new_user, token = workforce_service.create_user(
            db, email=email.strip(), display_name=display_name.strip(),
            workforce_id=None, created_by=user.id,
        )
    except DuplicateIdentity as exc:
        db.rollback()
        return _index_redirect(error=str(exc))
    db.commit()
    # Rendered directly rather than redirected: a one-time secret does not belong in
    # a URL query string (browser history, proxy logs) even for a moment.
    context = page_context(request, db, user, new_user=new_user, activation_token=token)
    return templates.TemplateResponse(request, "user_created.html", context)


@router.post("/teams", dependencies=[Depends(verify_form_csrf)])
def create_team_action(
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    name: str = Form(...),
    external_code: str = Form(...),
):
    if not authz.has_assigned_capability(db, user.id, MANAGE_ROLES):
        return _index_redirect(error="Not authorized to create a team.")
    team = workforce_service.create_team(
        db, name=name.strip(), external_code=external_code.strip(), parent_team_id=None,
        default_timezone=_DEFAULT_TIMEZONE, created_by=user.id,
    )
    db.commit()
    return _team_redirect(team.id, success=f'Team "{team.name}" created.')


@router.get("/users/{user_id}")
def user_detail(
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    target = db.get(User, user_id)
    if target is None or not workforce_service.can_manage_user(db, user.id, target.id):
        return _index_redirect(error="User not found or not authorized.")

    roles = list(
        db.scalars(
            select(RoleAssignment)
            .where(RoleAssignment.user_id == target.id, RoleAssignment.status == "active")
            .order_by(RoleAssignment.created_at.desc())
        )
    )
    memberships = list(
        db.execute(
            select(TeamMembership, Team)
            .join(Team, TeamMembership.team_id == Team.id)
            .where(
                TeamMembership.user_id == target.id,
                TeamMembership.membership_status == "active",
            )
            .order_by(Team.name)
        )
    )
    # .first() (a Row, subscriptable as (assignment, user)), not .scalar() (which
    # would silently collapse to just the first selected entity - the assignment -
    # and break the template's reporting_line[1] lookup for the supervisor).
    reporting_line = db.execute(
        select(ReportingAssignment, User)
        .join(User, ReportingAssignment.supervisor_user_id == User.id)
        .where(
            ReportingAssignment.subordinate_user_id == target.id,
            ReportingAssignment.status == "active",
            ReportingAssignment.assignment_type == "primary",
        )
    ).first()
    scope_teams = list(db.scalars(select(Team).where(Team.status == "active").order_by(Team.name)))
    candidate_supervisors = [
        candidate for candidate in workforce_service.list_visible_users(db, user.id, limit=100)
        if candidate.id != target.id
    ]

    context = page_context(
        request, db, user,
        active_section="workforce",
        target=target,
        roles=roles,
        memberships=memberships,
        reporting_line=reporting_line,
        appointable_roles=sorted(ROLE_APPOINTMENT_CAPABILITY.keys()),
        scope_teams=scope_teams,
        candidate_supervisors=candidate_supervisors,
        flash_error=request.query_params.get("flash_error"),
        flash_success=request.query_params.get("flash_success"),
    )
    return templates.TemplateResponse(request, "workforce_user_detail.html", context)


@router.post("/users/{user_id}/disable", dependencies=[Depends(verify_form_csrf)])
def disable_user_action(
    user_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    reason_code: str = Form(...),
):
    target = db.get(User, user_id)
    if target is None or not workforce_service.can_manage_user(db, user.id, target.id):
        return _index_redirect(error="User not found or not authorized.")
    try:
        workforce_service.disable_user(
            db, target, actor_id=user.id, reason_code=reason_code.strip()
        )
    except authz.SelfApprovalError as exc:
        db.rollback()
        return _user_redirect(user_id, error=str(exc))
    db.commit()
    return _user_redirect(user_id, success=f"{target.display_name} disabled.")


@router.post("/users/{user_id}/reactivate", dependencies=[Depends(verify_form_csrf)])
def reactivate_user_action(
    user_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    reason_code: str = Form(...),
):
    target = db.get(User, user_id)
    if target is None or not workforce_service.can_manage_user(db, user.id, target.id):
        return _index_redirect(error="User not found or not authorized.")
    workforce_service.reactivate_user(db, target, actor_id=user.id, reason_code=reason_code.strip())
    db.commit()
    return _user_redirect(user_id, success=f"{target.display_name} reactivated.")


@router.post("/users/{user_id}/roles", dependencies=[Depends(verify_form_csrf)])
def assign_role_action(
    user_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    role_code: str = Form(...),
    scope_type: str = Form(...),
    scope_id: str = Form(""),
):
    capability = ROLE_APPOINTMENT_CAPABILITY.get(role_code)
    parsed_scope_id = uuid.UUID(scope_id) if scope_id.strip() else None
    if capability is None or not authz.has_scope_capability(
        db, user.id, capability, scope_type=scope_type, scope_id=parsed_scope_id
    ):
        return _user_redirect(user_id, error="Not authorized to grant that role at that scope.")
    try:
        workforce_service.assign_role(
            db, target_user_id=user_id, role_code=role_code, scope_type=scope_type,
            scope_id=parsed_scope_id, appointed_by=user.id,
        )
    except (UnknownRole, authz.SelfApprovalError) as exc:
        db.rollback()
        return _user_redirect(user_id, error=str(exc))
    db.commit()
    return _user_redirect(user_id, success="Role assigned.")


@router.post("/roles/{role_assignment_id}/end", dependencies=[Depends(verify_form_csrf)])
def end_role_action(
    role_assignment_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    reason_code: str = Form(...),
):
    assignment = db.get(RoleAssignment, role_assignment_id)
    if assignment is None:
        return _index_redirect(error="Role assignment not found.")
    capability = ROLE_APPOINTMENT_CAPABILITY.get(assignment.role_code)
    if capability is None or not authz.has_scope_capability(
        db, user.id, capability,
        scope_type=assignment.scope_type, scope_id=assignment.scope_id,
    ):
        return _user_redirect(assignment.user_id, error="Not authorized to end that role.")
    try:
        workforce_service.end_role_assignment(
            db, assignment, ended_by=user.id, reason_code=reason_code.strip()
        )
    except authz.SelfApprovalError as exc:
        db.rollback()
        return _user_redirect(assignment.user_id, error=str(exc))
    db.commit()
    return _user_redirect(assignment.user_id, success="Role ended.")


@router.post("/users/{user_id}/reporting-line", dependencies=[Depends(verify_form_csrf)])
def set_reporting_line_action(
    user_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    supervisor_user_id: uuid.UUID = Form(...),
):
    target = db.get(User, user_id)
    if target is None or not workforce_service.can_manage_user(db, user.id, target.id):
        return _index_redirect(error="User not found or not authorized.")
    try:
        workforce_service.set_reporting_line(
            db, subordinate_user_id=user_id, supervisor_user_id=supervisor_user_id,
            assigned_by=user.id,
        )
    except SelfSupervision as exc:
        db.rollback()
        return _user_redirect(user_id, error=str(exc))
    db.commit()
    return _user_redirect(user_id, success="Reporting line updated.")


@router.get("/teams/{team_id}")
def team_detail(
    team_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    team = db.get(Team, team_id)
    if team is None:
        return _index_redirect(error="Team not found.")
    can_manage_team = authz.has_scope_capability(
        db, user.id, APPOINT_TEAM_CAPTAIN, scope_type="team", scope_id=team.id
    )

    members = list(
        db.execute(
            select(TeamMembership, User)
            .join(User, TeamMembership.user_id == User.id)
            .where(TeamMembership.team_id == team.id, TeamMembership.membership_status == "active")
            .order_by(User.display_name)
        )
    )
    candidate_members = []
    if can_manage_team:
        member_ids = {membership.user_id for membership, _ in members}
        candidate_members = [
            candidate for candidate in workforce_service.list_visible_users(db, user.id, limit=100)
            if candidate.id not in member_ids
        ]

    context = page_context(
        request, db, user,
        active_section="workforce",
        team=team,
        members=members,
        can_manage_team=can_manage_team,
        candidate_members=candidate_members,
        flash_error=request.query_params.get("flash_error"),
        flash_success=request.query_params.get("flash_success"),
    )
    return templates.TemplateResponse(request, "workforce_team_detail.html", context)


@router.post("/teams/{team_id}/members", dependencies=[Depends(verify_form_csrf)])
def add_team_member_action(
    team_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    member_user_id: uuid.UUID = Form(...),
):
    team = db.get(Team, team_id)
    if team is None or not authz.has_scope_capability(
        db, user.id, APPOINT_TEAM_CAPTAIN, scope_type="team", scope_id=team_id
    ):
        return _index_redirect(error="Team not found or not authorized.")
    workforce_service.add_team_membership(db, team, user_id=member_user_id, added_by=user.id)
    db.commit()
    return _team_redirect(team_id, success="Team member added.")


@router.post("/memberships/{membership_id}/end", dependencies=[Depends(verify_form_csrf)])
def end_team_membership_action(
    membership_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    membership = db.get(TeamMembership, membership_id)
    if membership is None:
        return _index_redirect(error="Team membership not found.")
    if not authz.has_scope_capability(
        db, user.id, APPOINT_TEAM_CAPTAIN, scope_type="team", scope_id=membership.team_id
    ):
        return _team_redirect(membership.team_id, error="Not authorized to manage this team.")
    workforce_service.end_team_membership(db, membership, ended_by=user.id)
    db.commit()
    return _team_redirect(membership.team_id, success="Team member removed.")
