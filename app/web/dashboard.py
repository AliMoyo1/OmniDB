"""Manager / Team Leader / Team Captain dashboard.

One parameterized view whose sections are gated by the viewer's own capabilities,
rather than three separate hardcoded pages: the shape is identical and only the
visible sections and data differ by role and scope. Plan 6.1's "separate
dashboards" is delivered by what each role actually sees, not by three duplicated
templates. Every action here calls the same service-layer functions the JSON API
already uses (app.campaigns.service, app.workforce.service) - this is a new
presentation layer over already-built, already-tested business logic, not a
reimplementation of it.
"""

from __future__ import annotations

import uuid
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.admin import list_visible_audit_events
from app.authz import service as authz
from app.authz.capabilities import (
    APPOINT_TEAM_CAPTAIN,
    ASSIGN_CAMPAIGN_AGENT,
    CREATE_CAMPAIGN,
    MANAGE_ROLES,
    VIEW_CAMPAIGN,
    VIEW_CAMPAIGN_REPORTS,
)
from app.campaigns import service as campaign_service
from app.campaigns.service import CampaignAssignmentError
from app.db import get_session
from app.models.campaign import Campaign
from app.models.identity import Team, User
from app.reporting import campaign_stats as campaign_stats_service
from app.web.dependencies import require_page_user, verify_form_csrf
from app.web.templates import nav_flags, page_context, templates
from app.workforce import service as workforce_service
from app.workforce.service import (
    ROLE_APPOINTMENT_CAPABILITY,
    DuplicateIdentity,
    UnknownRole,
)

router = APIRouter(tags=["web-dashboard"])

_DEFAULT_PROVENANCE_HINT = {
    "default_region": "ZW",
    "timezone": "Africa/Harare",
}


def _redirect(*, success: str | None = None, error: str | None = None) -> RedirectResponse:
    params: dict[str, str] = {}
    if success:
        params["flash_success"] = success
    if error:
        params["flash_error"] = error
    url = "/dashboard" + ("?" + urlencode(params) if params else "")
    return RedirectResponse(url, status_code=303)


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/dashboard", status_code=303)


@router.get("/dashboard")
def dashboard(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    flags = nav_flags(db, user)
    if flags["can_work_queue"] and not any(
        flags[key]
        for key in (
            "can_view_campaigns",
            "can_manage_workforce",
            "can_manage_teams",
            "can_view_audit",
        )
    ):
        return RedirectResponse("/agent/work", status_code=303)
    can_view_campaigns = flags["can_view_campaigns"]
    can_manage_workforce = flags["can_manage_workforce"]
    can_manage_teams = flags["can_manage_teams"]
    can_view_audit = flags["can_view_audit"]
    can_view_reports = authz.has_assigned_capability(db, user.id, VIEW_CAMPAIGN_REPORTS)
    can_create_campaign = authz.has_scope_capability(
        db, user.id, CREATE_CAMPAIGN, scope_type="organization", scope_id=None
    )

    campaigns = []
    if can_view_campaigns:
        rows = db.scalars(
            select(Campaign)
            .where(authz.campaign_scope_filter(db, user.id, VIEW_CAMPAIGN))
            .order_by(Campaign.created_at.desc())
            .limit(50)
        )
        for campaign in rows:
            stats = (
                campaign_stats_service.get_campaign_stats(db, campaign.id)
                if can_view_reports
                else None
            )
            campaigns.append({"campaign": campaign, "stats": stats})

    workforce_users = []
    user_roles: dict[uuid.UUID, list[str]] = {}
    if can_manage_workforce:
        workforce_users = workforce_service.list_visible_users(db, user.id, limit=100)
        user_roles = {u.id: sorted(authz.effective_roles(db, u.id)) for u in workforce_users}

    teams = []
    if can_manage_teams or can_manage_workforce:
        teams = list(
            db.scalars(
                select(Team).where(Team.status == "active").order_by(Team.name).limit(50)
            )
        )

    audit_events = list_visible_audit_events(db, user.id, limit=20) if can_view_audit else []

    # can_view_campaigns/can_manage_workforce/can_manage_teams/can_view_audit are
    # not passed explicitly here - page_context() computes the same nav_flags()
    # for every authenticated page, so the dock/sidebar stay correct even on pages
    # that don't otherwise need them (see app/web/templates.py's docstring).
    context = page_context(
        request, db, user,
        active_section="dashboard",
        campaigns=campaigns,
        can_create_campaign=can_create_campaign,
        default_provenance=_DEFAULT_PROVENANCE_HINT,
        workforce_users=workforce_users,
        user_roles=user_roles,
        appointable_roles=sorted(ROLE_APPOINTMENT_CAPABILITY.keys()),
        teams=teams,
        audit_events=audit_events,
        flash_error=request.query_params.get("flash_error"),
        flash_success=request.query_params.get("flash_success"),
    )
    return templates.TemplateResponse(request, "dashboard.html", context)


@router.post("/dashboard/campaigns", dependencies=[Depends(verify_form_csrf)])
def create_campaign_action(
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    name: str = Form(...),
    purpose: str = Form(...),
    data_source: str = Form(...),
    data_obtained_at: str = Form(...),
    lawful_basis_or_consent_reference: str = Form(...),
):
    if not authz.has_scope_capability(
        db, user.id, CREATE_CAMPAIGN, scope_type="organization", scope_id=None
    ):
        return _redirect(error="Not authorized to create a campaign.")
    try:
        parsed_date = date.fromisoformat(data_obtained_at)
    except ValueError:
        return _redirect(error="Data-obtained date must be a valid date.")
    campaign_service.create_campaign(
        db, created_by=user.id, name=name, description=None,
        owning_scope_type="organization", owning_scope_id=None,
        default_region=_DEFAULT_PROVENANCE_HINT["default_region"],
        timezone=_DEFAULT_PROVENANCE_HINT["timezone"],
        purpose=purpose, data_source=data_source, data_obtained_at=parsed_date,
        lawful_basis_or_consent_reference=lawful_basis_or_consent_reference,
    )
    db.commit()
    return _redirect(success=f'Campaign "{name}" created.')


@router.post("/dashboard/users", dependencies=[Depends(verify_form_csrf)])
def create_user_action(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    email: str = Form(...),
    display_name: str = Form(...),
):
    if not any(
        authz.has_assigned_capability(db, user.id, capability)
        for capability in ROLE_APPOINTMENT_CAPABILITY.values()
    ):
        return _redirect(error="Not authorized to create a user.")
    try:
        new_user, token = workforce_service.create_user(
            db, email=email, display_name=display_name, workforce_id=None, created_by=user.id,
        )
    except DuplicateIdentity as exc:
        db.rollback()
        return _redirect(error=str(exc))
    db.commit()
    # Rendered directly rather than redirected: a one-time secret does not belong in
    # a URL query string (browser history, proxy logs) even for a moment.
    context = page_context(request, db, user, new_user=new_user, activation_token=token)
    return templates.TemplateResponse(request, "user_created.html", context)


@router.post("/dashboard/users/{user_id}/roles", dependencies=[Depends(verify_form_csrf)])
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
        return _redirect(error="Not authorized to grant that role at that scope.")
    try:
        workforce_service.assign_role(
            db, target_user_id=user_id, role_code=role_code, scope_type=scope_type,
            scope_id=parsed_scope_id, appointed_by=user.id,
        )
    except (UnknownRole, authz.SelfApprovalError) as exc:
        db.rollback()
        return _redirect(error=str(exc))
    db.commit()
    return _redirect(success="Role assigned.")


@router.post("/dashboard/teams", dependencies=[Depends(verify_form_csrf)])
def create_team_action(
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    name: str = Form(...),
    external_code: str = Form(...),
):
    if not authz.has_assigned_capability(db, user.id, MANAGE_ROLES):
        return _redirect(error="Not authorized to create a team.")
    workforce_service.create_team(
        db, name=name, external_code=external_code, parent_team_id=None,
        default_timezone=_DEFAULT_PROVENANCE_HINT["timezone"], created_by=user.id,
    )
    db.commit()
    return _redirect(success=f'Team "{name}" created.')


@router.post("/dashboard/teams/{team_id}/members", dependencies=[Depends(verify_form_csrf)])
def add_team_member_action(
    team_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    member_user_id: uuid.UUID = Form(...),
):
    if not authz.has_scope_capability(
        db, user.id, APPOINT_TEAM_CAPTAIN, scope_type="team", scope_id=team_id
    ):
        return _redirect(error="Not authorized to manage this team's membership.")
    team = db.get(Team, team_id)
    if team is None:
        return _redirect(error="Team not found.")
    workforce_service.add_team_membership(db, team, user_id=member_user_id, added_by=user.id)
    db.commit()
    return _redirect(success="Team member added.")


@router.post(
    "/dashboard/campaigns/{campaign_id}/assignments", dependencies=[Depends(verify_form_csrf)]
)
def assign_agent_action(
    campaign_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
    agent_id: uuid.UUID = Form(...),
):
    campaign = db.get(Campaign, campaign_id)
    if campaign is None or not authz.has_campaign_capability(
        db, user.id, ASSIGN_CAMPAIGN_AGENT, campaign_id
    ):
        return _redirect(error="Not authorized to assign an agent to this campaign.")
    try:
        campaign_service.assign_agent_to_campaign(
            db, campaign, agent_id=agent_id, team_id=None, actor_id=user.id,
        )
    except CampaignAssignmentError as exc:
        db.rollback()
        return _redirect(error=str(exc))
    db.commit()
    return _redirect(success="Agent assigned to campaign.")
