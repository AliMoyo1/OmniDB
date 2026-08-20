"""Administration API (/api/v1/admin): identity, resets, audit search."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.auth import sessions as sess
from app.auth.dependencies import get_current_user, require_csrf
from app.auth.service import issue_activation_token
from app.authz import service as authz
from app.authz.capabilities import RESET_USER_AUTH, VIEW_AUDIT
from app.authz.dependencies import require_capability
from app.db import get_session
from app.models.audit import AuditEvent
from app.models.identity import User

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

_require_reset_auth = Depends(require_capability(RESET_USER_AUTH))
_require_view_audit = Depends(require_capability(VIEW_AUDIT))


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


@router.post("/users/{user_id}/reset-2fa", dependencies=[Depends(require_csrf)])
def reset_2fa(
    user_id: uuid.UUID,
    db: Session = Depends(get_session),
    actor: User = _require_reset_auth,
) -> dict[str, str]:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    target.totp_secret_ciphertext = None
    target.totp_enrolled = False
    sess.revoke_all_for_user(db, target.id)
    record_audit(
        db, action="admin.reset_2fa", result="success", actor_user_id=actor.id,
        target_type="user", target_id=target.id,
    )
    db.commit()
    return {"status": "ok"}


@router.post("/users/{user_id}/reset-password", dependencies=[Depends(require_csrf)])
def reset_password(
    user_id: uuid.UUID,
    db: Session = Depends(get_session),
    actor: User = _require_reset_auth,
) -> dict[str, str]:
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    target.password_hash = None  # cleared; the user must re-activate
    sess.revoke_all_for_user(db, target.id)
    token = issue_activation_token(target.id)
    record_audit(
        db, action="admin.reset_password", result="success", actor_user_id=actor.id,
        target_type="user", target_id=target.id,
    )
    db.commit()
    # The activation token is handed to the user through an approved offline process (D-23).
    return {"status": "ok", "activation_token": token}


@router.get("/audit-events")
def audit_events(
    limit: int = 50,
    db: Session = Depends(get_session),
    actor: User = _require_view_audit,
) -> list[dict]:
    rows = db.scalars(
        select(AuditEvent).order_by(desc(AuditEvent.occurred_at)).limit(min(limit, 200))
    )
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
