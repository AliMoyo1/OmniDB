"""Authorization service: effective-dated roles, capability checks, self-approval guard.

Default deny everywhere. Object scope is resolved on the server; nothing is trusted from
the client.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth import sessions as sess
from app.authz.capabilities import ROLE_CAPABILITIES
from app.models.authz import RoleAssignment
from app.models.base import utcnow


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
    # Installation and organization scopes cover narrower object scopes.
    if ra.scope_type in ("installation", "organization"):
        return True
    if scope_type is None:
        return True  # capability is not tied to a specific object
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


def assert_not_self(requester_id: uuid.UUID, subject_id: uuid.UUID) -> None:
    if requester_id == subject_id:
        raise SelfApprovalError("a user may not authorize this action on themselves")


def invalidate_sessions_on_privilege_change(db: Session, user_id: uuid.UUID) -> None:
    """Role or privilege changes revoke the affected user's active sessions (plan 6.4)."""
    sess.revoke_all_for_user(db, user_id)
