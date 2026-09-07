"""Delegation management: grant and revoke time-limited, scoped delegations of
capability (plan v0.3 section 11.3).

A delegation lets one user act with specific capabilities of another - to cover
an absent approver, say - within a scope and a time window. The safety rules are
the plan's: a delegation never includes a capability the delegator does not hold
at that scope (no privilege escalation), never delegates a role-elevation or
system-config capability, and is never granted to oneself. Expiry and revocation
are honored at authorization time (app/authz/service.py::active_delegations), so
an elapsed or revoked delegation simply grants nothing.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.authz import service as authz
from app.authz.capabilities import (
    CREATE_MANAGER,
    MANAGE_ROLES,
    RESET_USER_AUTH,
    ROLE_CAPABILITIES,
    TECHNICAL_CONFIG,
)
from app.models.authz import Delegation
from app.models.base import utcnow

# Every capability that exists (each is granted to at least one role).
ALL_CAPABILITIES: frozenset[str] = frozenset(
    capability for granted in ROLE_CAPABILITIES.values() for capability in granted
)

# Role-elevation and system-configuration capabilities can never be delegated
# (plan 11.3). Delegating the ability to appoint managers or reshape roles, or to
# reset another user's authentication or change technical config, would let a
# delegation quietly restructure who holds power - exactly what a scoped,
# temporary delegation must not do.
NON_DELEGABLE: frozenset[str] = frozenset(
    {TECHNICAL_CONFIG, RESET_USER_AUTH, CREATE_MANAGER, MANAGE_ROLES}
)

_VALID_SCOPE_TYPES = frozenset({"installation", "organization", "team", "campaign"})


class DelegationError(Exception):
    """Base class for delegation validation failures."""


class SelfDelegation(DelegationError):
    pass


class UnknownCapability(DelegationError):
    pass


class NonDelegableCapability(DelegationError):
    pass


class InvalidDelegationWindow(DelegationError):
    pass


class InsufficientDelegatorAuthority(DelegationError):
    pass


class DelegationNotFound(DelegationError):
    pass


class NotAuthorizedToRevoke(DelegationError):
    pass


def create_delegation(
    db: Session,
    *,
    delegator_id: uuid.UUID,
    delegate_id: uuid.UUID,
    capability_set: list[str],
    scope_type: str,
    scope_id: uuid.UUID | None,
    effective_from: datetime,
    effective_to: datetime | None,
    reason_code: str | None,
) -> Delegation:
    """Grant `delegate_id` the given capabilities at a scope for a window. Rejects
    self-delegation, unknown or non-delegable capabilities, an inverted window,
    and - the key guard - any capability the delegator does not themselves hold at
    that exact scope. Callers commit."""
    if delegate_id == delegator_id:
        raise SelfDelegation("a user cannot delegate to themselves")
    if scope_type not in _VALID_SCOPE_TYPES:
        raise DelegationError(f"unknown scope type: {scope_type}")
    if scope_type in ("team", "campaign") and scope_id is None:
        raise DelegationError(f"a {scope_type}-scoped delegation requires a scope id")

    caps = list(dict.fromkeys(capability_set))  # de-dup, order-preserving
    if not caps:
        raise DelegationError("a delegation must grant at least one capability")
    unknown = [c for c in caps if c not in ALL_CAPABILITIES]
    if unknown:
        raise UnknownCapability(f"unknown capability: {', '.join(sorted(unknown))}")
    forbidden = [c for c in caps if c in NON_DELEGABLE]
    if forbidden:
        raise NonDelegableCapability(
            f"these capabilities may not be delegated: {', '.join(sorted(forbidden))}"
        )
    if effective_to is not None and effective_to <= effective_from:
        raise InvalidDelegationWindow("effective_to must be after effective_from")

    # The delegator can only pass on authority they actually hold, at this scope -
    # a delegation is never an escalation path.
    lacking = [
        c
        for c in caps
        if not authz.has_scope_capability(
            db, delegator_id, c, scope_type=scope_type, scope_id=scope_id
        )
    ]
    if lacking:
        raise InsufficientDelegatorAuthority(
            "you do not hold these capabilities at this scope: " + ", ".join(sorted(lacking))
        )

    delegation = Delegation(
        delegator_user_id=delegator_id,
        delegate_user_id=delegate_id,
        capability_set=caps,
        scope_type=scope_type,
        scope_id=scope_id,
        effective_from=effective_from,
        effective_to=effective_to,
        reason_code=reason_code,
        approved_by=delegator_id,
    )
    db.add(delegation)
    db.flush()
    record_audit(
        db, action="delegation.create", result="success", actor_user_id=delegator_id,
        target_type="delegation", target_id=delegation.id, reason_code=reason_code,
        event_metadata={
            "delegate_user_id": str(delegate_id), "capabilities": caps,
            "scope_type": scope_type, "scope_id": str(scope_id) if scope_id else None,
        },
    )
    # The delegate's effective privileges just changed - rotate their sessions
    # (plan 11.3 step 6).
    authz.invalidate_sessions_on_privilege_change(db, delegate_id)
    return delegation


def revoke_delegation(
    db: Session, delegation_id: uuid.UUID, *, actor_id: uuid.UUID
) -> Delegation:
    """Revoke a delegation. Only the delegator who granted it may revoke it.
    Idempotent - revoking an already-revoked delegation leaves its original
    revoked_at. Callers commit."""
    delegation = db.get(Delegation, delegation_id)
    if delegation is None:
        raise DelegationNotFound("delegation not found")
    if delegation.delegator_user_id != actor_id:
        raise NotAuthorizedToRevoke("only the delegator may revoke this delegation")
    if delegation.revoked_at is None:
        delegation.revoked_at = utcnow()
        record_audit(
            db, action="delegation.revoke", result="success", actor_user_id=actor_id,
            target_type="delegation", target_id=delegation.id,
            event_metadata={"delegate_user_id": str(delegation.delegate_user_id)},
        )
        authz.invalidate_sessions_on_privilege_change(db, delegation.delegate_user_id)
    return delegation


def list_delegations(
    db: Session,
    *,
    delegator_id: uuid.UUID | None = None,
    delegate_id: uuid.UUID | None = None,
    involving_user_id: uuid.UUID | None = None,
) -> list[Delegation]:
    """Delegations filtered by delegator, delegate, or either (involving_user_id).
    Most recent first."""
    conditions = []
    if delegator_id is not None:
        conditions.append(Delegation.delegator_user_id == delegator_id)
    if delegate_id is not None:
        conditions.append(Delegation.delegate_user_id == delegate_id)
    if involving_user_id is not None:
        conditions.append(
            or_(
                Delegation.delegator_user_id == involving_user_id,
                Delegation.delegate_user_id == involving_user_id,
            )
        )
    stmt = select(Delegation)
    if conditions:
        stmt = stmt.where(*conditions)
    return list(db.scalars(stmt.order_by(Delegation.created_at.desc())))
