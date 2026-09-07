"""Server-rendered delegation console (/delegations).

Thin browser layer over app/authz/delegations.py, the same relationship the other
web routers have to their services. The page is personal, like the inbox: it shows
the delegations you have granted and the ones you currently hold, with no hard
capability gate. The grant form only appears when you actually hold a capability
that can be delegated.

Granting is a privilege change, so - exactly as the JSON API does with
require_recent_reauthentication - it requires a recent identity check (step-up).
Revoking a delegation is a de-escalation and needs only CSRF, matching the API's
DELETE.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import is_recently_reauthenticated
from app.authz import service as authz
from app.authz.capabilities import VIEW_CAMPAIGN
from app.authz.delegations import (
    NON_DELEGABLE,
    DelegationError,
    DelegationNotFound,
    NotAuthorizedToRevoke,
    create_delegation,
    list_delegations,
    revoke_delegation,
)
from app.db import get_session
from app.models.authz import Delegation
from app.models.base import utcnow
from app.models.campaign import Campaign
from app.models.identity import Team, User
from app.models.session import Session as SessionModel
from app.web.dependencies import (
    require_page_session,
    require_page_user,
    verify_form_csrf,
)
from app.web.templates import page_context, templates
from app.workforce.service import list_visible_users, visible_team_ids

router = APIRouter(prefix="/delegations", tags=["web-delegations"])


def _redirect(*, success: str | None = None, error: str | None = None) -> RedirectResponse:
    params = {
        key: value for key, value in (("flash_success", success), ("flash_error", error)) if value
    }
    return RedirectResponse(
        "/delegations" + ("?" + urlencode(params) if params else ""), status_code=303
    )


def _status(delegation: Delegation, now: datetime) -> str:
    """A delegation's lifecycle state at `now` - the same request-time window and
    revocation logic app/authz/service.py::active_delegations enforces, surfaced
    here for display."""
    if delegation.revoked_at is not None:
        return "revoked"
    if delegation.effective_from > now:
        return "scheduled"
    if delegation.effective_to is not None and delegation.effective_to <= now:
        return "expired"
    return "active"


def _parse_dt(value: str) -> datetime | None:
    """Parse a datetime-local form value (naive wall time) as UTC-aware, so it
    compares cleanly against the timezone-aware window checks. Blank -> None."""
    value = value.strip()
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _parse_uuid(value: str) -> uuid.UUID | None:
    value = value.strip()
    return uuid.UUID(value) if value else None


def _user_name_map(db: Session, delegations: list[Delegation]) -> dict[uuid.UUID, User]:
    ids = {d.delegator_user_id for d in delegations} | {d.delegate_user_id for d in delegations}
    if not ids:
        return {}
    return {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids)))}


def _scope_name_map(db: Session, delegations: list[Delegation]) -> dict[uuid.UUID, str]:
    """Map team/campaign scope ids to a human name so a row reads "team Alpha"
    rather than a bare UUID."""
    team_ids = {
        d.scope_id for d in delegations if d.scope_type == "team" and d.scope_id is not None
    }
    campaign_ids = {
        d.scope_id for d in delegations if d.scope_type == "campaign" and d.scope_id is not None
    }
    names: dict[uuid.UUID, str] = {}
    if team_ids:
        for team in db.scalars(select(Team).where(Team.id.in_(team_ids))):
            names[team.id] = team.name
    if campaign_ids:
        for campaign in db.scalars(select(Campaign).where(Campaign.id.in_(campaign_ids))):
            names[campaign.id] = campaign.name
    return names


@router.get("")
def delegations_page(
    request: Request,
    db: Session = Depends(get_session),
    session: SessionModel = Depends(require_page_session),
    user: User = Depends(require_page_user),
):
    granted = list_delegations(db, delegator_id=user.id)
    held = list_delegations(db, delegate_id=user.id)
    all_rows = granted + held
    now = utcnow()

    delegable = sorted(authz.capabilities_for(authz.effective_roles(db, user.id)) - NON_DELEGABLE)
    candidates = [u for u in list_visible_users(db, user.id) if u.id != user.id]
    # Scope the team picker to what the caller's own role authority covers - the
    # same non-leaking scoping list_visible_users applies - so a narrowly-scoped
    # user cannot enumerate every team (and org) in the system from this page.
    # create_delegation still enforces the precise per-capability role check, so
    # this can only ever be narrower than what a delegation may actually target.
    sees_all_teams, scoped_team_ids = visible_team_ids(db, user.id)
    if sees_all_teams:
        teams = list(db.scalars(select(Team).where(Team.status == "active").order_by(Team.name)))
    elif scoped_team_ids:
        teams = list(
            db.scalars(
                select(Team)
                .where(Team.status == "active", Team.id.in_(scoped_team_ids))
                .order_by(Team.name)
            )
        )
    else:
        teams = []
    campaigns = list(
        db.scalars(
            select(Campaign)
            .where(authz.campaign_scope_filter(db, user.id, VIEW_CAMPAIGN))
            .order_by(Campaign.name)
        )
    )

    context = page_context(
        request, db, user,
        active_section="delegations",
        granted=granted,
        held=held,
        statuses={d.id: _status(d, now) for d in all_rows},
        user_names=_user_name_map(db, all_rows),
        scope_names=_scope_name_map(db, all_rows),
        delegable_capabilities=delegable,
        candidates=candidates,
        teams=teams,
        campaigns=campaigns,
        recently_reauthenticated=is_recently_reauthenticated(session),
        flash_error=request.query_params.get("flash_error"),
        flash_success=request.query_params.get("flash_success"),
    )
    return templates.TemplateResponse(request, "delegations.html", context)


@router.post("", dependencies=[Depends(verify_form_csrf)])
def create_delegation_action(
    db: Session = Depends(get_session),
    session: SessionModel = Depends(require_page_session),
    user: User = Depends(require_page_user),
    delegate_id: str = Form(...),
    capabilities: list[str] = Form(default=[]),
    scope_type: str = Form(...),
    team_id: str = Form(""),
    campaign_id: str = Form(""),
    effective_from: str = Form(""),
    effective_to: str = Form(""),
    reason_code: str = Form(""),
):
    # Step-up: granting authority to someone else is a privilege change (plan 11.3
    # step 4). The JSON API enforces this with require_recent_reauthentication; the
    # browser equivalent is a recent identity check on this session.
    if not is_recently_reauthenticated(session):
        return _redirect(
            error="For security, confirm your identity at Account security within the "
            "last few minutes, then grant the delegation."
        )

    try:
        delegate_uuid = _parse_uuid(delegate_id)
        scope_id = (
            _parse_uuid(team_id)
            if scope_type == "team"
            else _parse_uuid(campaign_id)
            if scope_type == "campaign"
            else None
        )
        window_from = _parse_dt(effective_from) or utcnow()
        window_to = _parse_dt(effective_to)
    except ValueError:
        return _redirect(error="Choose a valid delegate and dates.")
    if delegate_uuid is None:
        return _redirect(error="Choose who to delegate to.")

    try:
        create_delegation(
            db,
            delegator_id=user.id,
            delegate_id=delegate_uuid,
            capability_set=capabilities,
            scope_type=scope_type,
            scope_id=scope_id,
            effective_from=window_from,
            effective_to=window_to,
            reason_code=reason_code.strip() or None,
        )
    except DelegationError as exc:
        db.rollback()
        return _redirect(error=str(exc))
    db.commit()
    return _redirect(success="Delegation granted. The delegate's sessions were refreshed.")


@router.post("/{delegation_id}/revoke", dependencies=[Depends(verify_form_csrf)])
def revoke_delegation_action(
    delegation_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    try:
        revoke_delegation(db, delegation_id, actor_id=user.id)
    except DelegationNotFound:
        return _redirect(error="Delegation not found.")
    except NotAuthorizedToRevoke:
        return _redirect(error="Only the person who granted a delegation can revoke it.")
    db.commit()
    return _redirect(success="Delegation revoked. The delegate's sessions were refreshed.")
