"""Server-rendered administrative user directory (/admin/users).

Phase A (read-only): a scoped, filtered, paginated view over every user the
actor is authorized to see, plus a summary of their state. Thin layer over
workforce_service.list_user_directory/directory_summary - the same
one-scoped-query-not-two pattern app/web/audit.py already established for the
audit trail.

Phase B (this file's detail/action routes): a per-user detail page and three
credential-administration actions - issue/replace an activation code, reset
password, reset MFA - gated on RESET_USER_AUTH specifically, independent of
whatever workforce-appointment authority the actor may also hold (plan 4.4:
technical/credential authority and business/workforce authority stay separate
capabilities, even though the same Super Administrator commonly holds both
here). Disable/reactivate and role/team management stay on the existing
/workforce/users/{id} page, which already carries them for actors with
workforce-appointment authority; this page links there rather than duplicating
them.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import service as auth_service
from app.auth.dependencies import is_recently_reauthenticated
from app.authz import service as authz
from app.authz.capabilities import RESET_USER_AUTH
from app.config import get_settings
from app.db import get_session
from app.models.identity import Team, User
from app.models.session import Session as SessionModel
from app.web.dependencies import require_page_session, require_page_user, verify_form_csrf
from app.web.templates import page_context, templates
from app.workforce import service as workforce_service
from app.workforce.service import ROLE_APPOINTMENT_CAPABILITY

router = APIRouter(prefix="/admin/users", tags=["web-admin-users"])

_STATUS_VALUES = ("active", "inactive")


def _parse_page(raw: str | None) -> int:
    try:
        return max(int(raw or "1"), 1)
    except ValueError:
        return 1


def _parse_uuid(raw: str | None) -> uuid.UUID | None:
    if not raw:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


@router.get("")
def admin_users_list(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_page_user),
):
    if not workforce_service.can_view_admin_directory(db, user.id):
        return RedirectResponse(
            "/dashboard?flash_error=Not+authorized+for+user+administration.", 303
        )

    params = request.query_params
    search = params.get("q", "").strip() or None
    status = params.get("status", "").strip() or None
    activation_state = params.get("activation_state", "").strip() or None
    role = params.get("role", "").strip() or None
    team_id = _parse_uuid(params.get("team_id", "").strip())
    sort = params.get("sort", "name").strip()
    if sort not in workforce_service.DIRECTORY_SORTS:
        sort = "name"
    if status not in _STATUS_VALUES:
        status = None
    page = _parse_page(params.get("page"))

    directory = workforce_service.list_user_directory(
        db,
        user.id,
        search=search,
        status=status,
        activation_state=activation_state,
        role=role,
        team_id=team_id,
        page=page,
        sort=sort,
    )
    # A stale/out-of-range page (filters changed, or someone paged past the end)
    # renders as an empty table rather than raising - the query itself is safe,
    # this just clamps what the pager links to next.
    page_size = workforce_service.DIRECTORY_PAGE_SIZE_DEFAULT
    total_pages = max((directory.total_count + page_size - 1) // page_size, 1)
    page = min(page, total_pages)

    summary = workforce_service.directory_summary(db, user.id)
    teams = list(db.scalars(select(Team).where(Team.status == "active").order_by(Team.name)))

    filter_params = {
        "q": search or "",
        "status": status or "",
        "activation_state": activation_state or "",
        "role": role or "",
        "team_id": str(team_id) if team_id else "",
    }
    if sort != "name":
        filter_params["sort"] = sort
    filters_qs = urlencode({key: value for key, value in filter_params.items() if value})

    context = page_context(
        request,
        db,
        user,
        active_section="admin_users",
        directory=directory,
        summary=summary,
        teams=teams,
        appointable_roles=sorted(ROLE_APPOINTMENT_CAPABILITY.keys()),
        activation_states=workforce_service.ACTIVATION_STATE_LABELS,
        filter_q=search or "",
        filter_status=status or "",
        filter_activation_state=activation_state or "",
        filter_role=role or "",
        filter_team_id=str(team_id) if team_id else "",
        filters_qs=filters_qs,
        sort=sort,
        page=page,
        total_pages=total_pages,
        page_size=page_size,
    )
    return templates.TemplateResponse(request, "admin_users.html", context)


def _redirect_list(*, error: str) -> RedirectResponse:
    return RedirectResponse("/admin/users?" + urlencode({"flash_error": error}), 303)


def _redirect_detail(
    user_id: uuid.UUID, *, success: str | None = None, error: str | None = None
) -> RedirectResponse:
    params = {
        key: value for key, value in (("flash_success", success), ("flash_error", error)) if value
    }
    target = f"/admin/users/{user_id}" + ("?" + urlencode(params) if params else "")
    return RedirectResponse(target, 303)


def _can_reset_credentials(db: Session, actor_id: uuid.UUID) -> bool:
    """Matches app/api/admin.py's require_capability(RESET_USER_AUTH) exactly -
    the browser and JSON paths must agree on who can reset credentials (plan
    4.4/9.3/9.4: this is deliberately narrower than can_view_admin_directory,
    which also admits a plain workforce-appointment capability)."""
    return authz.has_capability(db, actor_id, RESET_USER_AUTH)


def _load_target_in_scope(db: Session, actor_id: uuid.UUID, user_id: uuid.UUID) -> User | None:
    """None for both "does not exist" and "exists but out of scope" (plan 6.1/9.5:
    a route must not disclose which case it is)."""
    target = db.get(User, user_id)
    if target is None or not workforce_service.user_in_admin_scope(db, actor_id, target.id):
        return None
    return target


@router.get("/{user_id}")
def admin_user_detail(
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
    session: SessionModel = Depends(require_page_session),
    user: User = Depends(require_page_user),
):
    if not workforce_service.can_view_admin_directory(db, user.id):
        return RedirectResponse(
            "/dashboard?flash_error=Not+authorized+for+user+administration.", 303
        )
    target = _load_target_in_scope(db, user.id, user_id)
    if target is None:
        return _redirect_list(error="User not found or not authorized.")

    roles, teams = workforce_service.roles_and_teams_for_user(db, target.id)
    activation_state = workforce_service.activation_state_for_user(db, target.id)
    is_self = user.id == target.id
    has_reset_capability = _can_reset_credentials(db, user.id)
    context = page_context(
        request,
        db,
        user,
        active_section="admin_users",
        target=target,
        activation_state=activation_state,
        activation_state_label=workforce_service.ACTIVATION_STATE_LABELS.get(activation_state),
        roles=roles,
        teams=teams,
        can_manage_workforce_profile=workforce_service.can_manage_user(db, user.id, target.id),
        has_reset_capability=has_reset_capability,
        can_reset_credentials=has_reset_capability and not is_self,
        is_self=is_self,
        recently_reauthenticated=is_recently_reauthenticated(session),
        flash_error=request.query_params.get("flash_error"),
        flash_success=request.query_params.get("flash_success"),
    )
    return templates.TemplateResponse(request, "admin_user_detail.html", context)


def _render_activation_code_result(
    request: Request, db: Session, user: User, target: User, token: str, expires_at
):
    """Rendered directly, never redirected (plan 5.4): a one-time secret does
    not belong in a URL, even briefly. Cache-Control: no-store is already
    applied to every non-static response by SecurityHeadersMiddleware."""
    display_timezone = ZoneInfo(get_settings().default_timezone)
    context = page_context(
        request,
        db,
        user,
        active_section="admin_users",
        target=target,
        activation_token=token,
        expires_at_local=expires_at.astimezone(display_timezone),
        timezone_name=get_settings().default_timezone,
    )
    return templates.TemplateResponse(request, "activation_code_issued.html", context)


@router.post("/{user_id}/activation-code", dependencies=[Depends(verify_form_csrf)])
def issue_activation_code_action(
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
    session: SessionModel = Depends(require_page_session),
    user: User = Depends(require_page_user),
):
    target = _load_target_in_scope(db, user.id, user_id)
    if target is None:
        return _redirect_list(error="User not found or not authorized.")
    if not _can_reset_credentials(db, user.id):
        return _redirect_detail(user_id, error="Not authorized to issue activation codes.")
    try:
        authz.assert_not_self(user.id, target.id)
    except authz.SelfApprovalError:
        return _redirect_detail(user_id, error="You cannot issue your own activation code.")
    if not is_recently_reauthenticated(session):
        return _redirect_detail(
            user_id,
            error="Confirm your identity at Account security, then try again.",
        )
    try:
        token, expires_at = auth_service.issue_or_replace_activation_code(
            db, target, actor_id=user.id
        )
    except auth_service.AccountNotEligible as exc:
        db.rollback()
        return _redirect_detail(user_id, error=str(exc).capitalize() + ".")
    db.commit()
    return _render_activation_code_result(request, db, user, target, token, expires_at)


@router.post("/{user_id}/reset-password", dependencies=[Depends(verify_form_csrf)])
def reset_password_action(
    user_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_session),
    session: SessionModel = Depends(require_page_session),
    user: User = Depends(require_page_user),
):
    target = _load_target_in_scope(db, user.id, user_id)
    if target is None:
        return _redirect_list(error="User not found or not authorized.")
    if not _can_reset_credentials(db, user.id):
        return _redirect_detail(user_id, error="Not authorized to reset passwords.")
    try:
        authz.assert_not_self(user.id, target.id)
    except authz.SelfApprovalError:
        return _redirect_detail(user_id, error="You cannot reset your own password here.")
    if not is_recently_reauthenticated(session):
        return _redirect_detail(
            user_id,
            error="Confirm your identity at Account security, then try again.",
        )
    try:
        token, expires_at = auth_service.reset_password(db, target, actor_id=user.id)
    except auth_service.AccountNotEligible as exc:
        db.rollback()
        return _redirect_detail(user_id, error=str(exc).capitalize() + ".")
    db.commit()
    return _render_activation_code_result(request, db, user, target, token, expires_at)


@router.post("/{user_id}/reset-mfa", dependencies=[Depends(verify_form_csrf)])
def reset_mfa_action(
    user_id: uuid.UUID,
    db: Session = Depends(get_session),
    session: SessionModel = Depends(require_page_session),
    user: User = Depends(require_page_user),
):
    target = _load_target_in_scope(db, user.id, user_id)
    if target is None:
        return _redirect_list(error="User not found or not authorized.")
    if not _can_reset_credentials(db, user.id):
        return _redirect_detail(user_id, error="Not authorized to reset MFA.")
    try:
        authz.assert_not_self(user.id, target.id)
    except authz.SelfApprovalError:
        return _redirect_detail(user_id, error="You cannot reset your own MFA here.")
    if not is_recently_reauthenticated(session):
        return _redirect_detail(
            user_id,
            error="Confirm your identity at Account security, then try again.",
        )
    try:
        auth_service.reset_mfa(db, target, actor_id=user.id)
    except auth_service.AccountNotEligible as exc:
        db.rollback()
        return _redirect_detail(user_id, error=str(exc).capitalize() + ".")
    db.commit()
    return _redirect_detail(
        user_id, success=f"MFA reset for {target.display_name}. They must re-enroll to sign in."
    )
