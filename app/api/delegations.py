"""Delegation API (/api/v1/delegations): grant, revoke, and list time-limited
scoped delegations (plan v0.3 11.3). Creating one is self-authorized - the
service verifies the delegator actually holds every capability at the scope - so
there is no separate capability gate, but a grant is a privilege change and so
requires step-up reauthentication, like the other privileged admin actions.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.dependencies import (
    get_current_user,
    require_csrf,
    require_recent_reauthentication,
)
from app.authz import delegations as delegation_service
from app.db import get_session
from app.models.authz import Delegation
from app.models.identity import User

router = APIRouter(prefix="/api/v1/delegations", tags=["delegations"])


class DelegationCreateRequest(BaseModel):
    delegate_id: uuid.UUID
    capability_set: list[str]
    scope_type: str
    scope_id: uuid.UUID | None = None
    effective_from: datetime
    effective_to: datetime | None = None
    reason_code: str | None = None


class DelegationOut(BaseModel):
    id: str
    delegator_user_id: str
    delegate_user_id: str
    capability_set: list[str]
    scope_type: str
    scope_id: str | None
    effective_from: datetime
    effective_to: datetime | None
    reason_code: str | None
    revoked_at: datetime | None
    created_at: datetime


def _out(delegation: Delegation) -> DelegationOut:
    return DelegationOut(
        id=str(delegation.id),
        delegator_user_id=str(delegation.delegator_user_id),
        delegate_user_id=str(delegation.delegate_user_id),
        capability_set=list(delegation.capability_set or []),
        scope_type=delegation.scope_type,
        scope_id=str(delegation.scope_id) if delegation.scope_id else None,
        effective_from=delegation.effective_from,
        effective_to=delegation.effective_to,
        reason_code=delegation.reason_code,
        revoked_at=delegation.revoked_at,
        created_at=delegation.created_at,
    )


@router.post(
    "",
    response_model=DelegationOut,
    dependencies=[Depends(require_csrf), Depends(require_recent_reauthentication)],
)
def create_delegation(
    payload: DelegationCreateRequest,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> DelegationOut:
    try:
        delegation = delegation_service.create_delegation(
            db,
            delegator_id=user.id,
            delegate_id=payload.delegate_id,
            capability_set=payload.capability_set,
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
            reason_code=payload.reason_code,
        )
    except delegation_service.InsufficientDelegatorAuthority as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    except delegation_service.DelegationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    db.commit()
    return _out(delegation)


@router.delete("/{delegation_id}", dependencies=[Depends(require_csrf)])
def revoke_delegation(
    delegation_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    try:
        delegation_service.revoke_delegation(db, delegation_id, actor_id=user.id)
    except delegation_service.DelegationNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    except delegation_service.NotAuthorizedToRevoke as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from None
    db.commit()
    return {"status": "revoked"}


@router.get("", response_model=list[DelegationOut])
def list_delegations(
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[DelegationOut]:
    # Everything the caller granted or currently holds.
    return [
        _out(d)
        for d in delegation_service.list_delegations(db, involving_user_id=user.id)
    ]
