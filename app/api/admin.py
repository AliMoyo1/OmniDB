"""Administration API (/api/v1/admin): identity, resets, audit search."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.auth import sessions as sess
from app.auth.dependencies import (
    get_current_user,
    require_csrf,
    require_recent_reauthentication,
)
from app.auth.service import issue_activation_token
from app.authz import service as authz
from app.authz.capabilities import RESET_USER_AUTH, ROLE_CAPABILITIES, VIEW_AUDIT
from app.authz.dependencies import require_capability
from app.db import get_session
from app.models.audit import AuditEvent
from app.models.identity import TeamMembership, User

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

_require_reset_auth = Depends(require_capability(RESET_USER_AUTH))


@router.get("/whoami")
def whoami(
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    roles = authz.effective_roles(db, user.id)
    return {
        "user_id": str(user.id),
        "email": user.email,
        "roles": sorted(roles),
        "capabilities": sorted(authz.capabilities_for(roles)),
    }


def _assert_not_self(actor_id: uuid.UUID, target_id: uuid.UUID) -> None:
    try:
        authz.assert_not_self(actor_id, target_id)
    except authz.SelfApprovalError:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "self-service credential reset is not allowed here"
        ) from None


@router.post(
    "/users/{user_id}/reset-2fa",
    dependencies=[Depends(require_csrf), Depends(require_recent_reauthentication)],
)
def reset_2fa(
    user_id: uuid.UUID,
    db: Session = Depends(get_session),
    actor: User = _require_reset_auth,
) -> dict[str, str]:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    _assert_not_self(actor.id, target.id)
    target.totp_secret_ciphertext = None
    target.totp_enrolled = False
    sess.revoke_all_for_user(db, target.id)
    record_audit(
        db, action="admin.reset_2fa", result="success", actor_user_id=actor.id,
        target_type="user", target_id=target.id,
    )
    db.commit()
    return {"status": "ok"}


@router.post(
    "/users/{user_id}/reset-password",
    dependencies=[Depends(require_csrf), Depends(require_recent_reauthentication)],
)
def reset_password(
    user_id: uuid.UUID,
    db: Session = Depends(get_session),
    actor: User = _require_reset_auth,
) -> dict[str, str]:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    _assert_not_self(actor.id, target.id)
    target.password_hash = None  # cleared; the user must re-activate
    sess.revoke_all_for_user(db, target.id)
    token = issue_activation_token(db, target.id, created_by=actor.id)
    record_audit(
        db, action="admin.reset_password", result="success", actor_user_id=actor.id,
        target_type="user", target_id=target.id,
    )
    db.commit()
    # The activation token is handed to the user through an approved offline process (D-23).
    return {"status": "ok", "activation_token": token}


def _audit_visibility(db: Session, actor_id: uuid.UUID) -> tuple[bool, set[uuid.UUID]]:
    """installation- or organization-wide VIEW_AUDIT sees every event (Super Admin,
    Manager in this single-org pilot). A narrower, team-scoped grant (Team Leader,
    Team Captain) sees only events performed by themselves or by an active member of
    their own team(s) - a real, non-leaking slice of the trail rather than
    everything, without needing every existing record_audit call site retrofitted
    with a team/organization_id (most don't set one)."""
    sees_everyone = False
    team_ids: set[uuid.UUID] = set()
    for assignment in authz.effective_role_assignments(db, actor_id):
        if VIEW_AUDIT not in ROLE_CAPABILITIES.get(assignment.role_code, set()):
            continue
        if assignment.scope_type == "installation":
            sees_everyone = True
        elif assignment.scope_type == "organization" and assignment.scope_id is None:
            sees_everyone = True
        elif assignment.scope_type == "team" and assignment.scope_id is not None:
            team_ids.add(assignment.scope_id)
    return sees_everyone, team_ids


@router.get("/audit-events")
def audit_events(
    limit: int = 50,
    db: Session = Depends(get_session),
    actor: User = Depends(get_current_user),
) -> list[dict]:
    if not authz.has_assigned_capability(db, actor.id, VIEW_AUDIT):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized")
    sees_everyone, team_ids = _audit_visibility(db, actor.id)
    stmt = select(AuditEvent).order_by(desc(AuditEvent.occurred_at)).limit(min(limit, 200))
    if not sees_everyone:
        visible_actor_ids = {actor.id}
        if team_ids:
            visible_actor_ids |= set(
                db.scalars(
                    select(TeamMembership.user_id).where(
                        TeamMembership.team_id.in_(team_ids),
                        TeamMembership.membership_status == "active",
                    )
                )
            )
        stmt = stmt.where(AuditEvent.actor_user_id.in_(visible_actor_ids))
    rows = db.scalars(stmt)
    return [
        {
            "id": str(row.id),
            "occurred_at": row.occurred_at.isoformat(),
            "action": row.action,
            "result": row.result,
            "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
            "target_type": row.target_type,
            "target_id": str(row.target_id) if row.target_id else None,
            "reason_code": row.reason_code,
        }
        for row in rows
    ]
