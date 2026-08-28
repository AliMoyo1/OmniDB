"""Agent work API (/api/v1/work, /api/v1/agent).

Every route is gated by the WORK_QUEUE capability as a coarse role check. True
object-level authorization - that a lease belongs to the calling agent - is enforced in
the service layer (app/work/service.py), not here, per plan 6.4.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.auth.dependencies import require_csrf
from app.authz.capabilities import WORK_QUEUE
from app.authz.dependencies import require_capability
from app.db import get_session
from app.flags.service import FeatureDisabledError
from app.models.identity import User
from app.reporting import agent_stats
from app.work import service as work_service
from app.work.schemas import (
    AgentStatsOut,
    CallbackItemOut,
    CompleteOut,
    CompleteRequest,
    LeaseOut,
    RenewOut,
    RenewRequest,
    SkipRequest,
)
from app.work.service import (
    DispositionMismatch,
    IdempotencyConflict,
    LeaseConflict,
    MissingRequiredField,
)

work_router = APIRouter(prefix="/api/v1/work", tags=["work"])
agent_router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

_agent_only = Depends(require_capability(WORK_QUEUE))


def _lease_out(result: work_service.LeaseResult) -> LeaseOut:
    return LeaseOut(
        work_item_id=str(result.work_item_id),
        lease_id=str(result.lease_id),
        lease_expires_at=result.lease_expires_at,
        campaign_id=str(result.campaign_id),
        campaign_name=result.campaign_name,
        phone_e164=result.phone_e164,
        contact_name=result.contact_name,
        approved_metadata=result.approved_metadata,
        is_callback=result.is_callback,
    )


@work_router.post("/next", response_model=None, dependencies=[Depends(require_csrf)])
def lease_next(
    db: Session = Depends(get_session),
    user: User = _agent_only,
) -> LeaseOut | Response:
    # response_model=None disables automatic response inference so a raw Response can
    # be returned untouched for the no-work case, avoiding any ambiguity around a 204
    # (which must never carry a body) combined with a declared Optional response model.
    try:
        result = work_service.lease_next(db, user.id)
    except FeatureDisabledError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    db.commit()
    if result is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return _lease_out(result)


@work_router.post(
    "/{work_item_id}/complete", response_model=CompleteOut, dependencies=[Depends(require_csrf)]
)
def complete_work_item(
    work_item_id: uuid.UUID,
    payload: CompleteRequest,
    db: Session = Depends(get_session),
    user: User = _agent_only,
) -> CompleteOut:
    try:
        result = work_service.complete_work_item(
            db,
            work_item_id=work_item_id,
            agent_id=user.id,
            lease_id=payload.lease_id,
            disposition_id=payload.disposition_id,
            notes=payload.notes,
            callback_at=payload.callback_at,
            self_reported_duration_seconds=payload.self_reported_duration_seconds,
            idempotency_key=payload.idempotency_key,
        )
    except LeaseConflict as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "lease_conflict", "message": str(exc)}
        ) from None
    except IdempotencyConflict as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"code": "idempotency_conflict", "message": str(exc)},
        ) from None
    except (DispositionMismatch, MissingRequiredField) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    except FeatureDisabledError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
    db.commit()
    return CompleteOut(
        attempt_id=str(result.attempt_id), work_item_state=result.work_item_state,
        semantic_outcome=result.semantic_outcome, callback_at=result.callback_at,
    )


@work_router.post("/{work_item_id}/skip", dependencies=[Depends(require_csrf)])
def skip_work_item(
    work_item_id: uuid.UUID,
    payload: SkipRequest,
    db: Session = Depends(get_session),
    user: User = _agent_only,
) -> dict:
    try:
        work_item = work_service.skip_work_item(
            db, work_item_id=work_item_id, agent_id=user.id,
            lease_id=payload.lease_id, reason=payload.reason,
        )
    except LeaseConflict as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "lease_conflict", "message": str(exc)}
        ) from None
    except MissingRequiredField as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from None
    db.commit()
    return {"work_item_id": str(work_item.id), "state": work_item.state}


@work_router.post(
    "/{work_item_id}/lease/renew", response_model=RenewOut, dependencies=[Depends(require_csrf)]
)
def renew_lease(
    work_item_id: uuid.UUID,
    payload: RenewRequest,
    db: Session = Depends(get_session),
    user: User = _agent_only,
) -> RenewOut:
    try:
        work_item = work_service.renew_lease(
            db, work_item_id, user.id, payload.lease_id
        )
    except LeaseConflict as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, {"code": "lease_conflict", "message": str(exc)}
        ) from None
    db.commit()
    return RenewOut(
        work_item_id=str(work_item.id), lease_id=str(work_item.lease_id),
        lease_expires_at=work_item.lease_expires_at,
    )


@agent_router.get("/callbacks", response_model=list[CallbackItemOut])
def list_callbacks(
    db: Session = Depends(get_session),
    user: User = _agent_only,
) -> list[CallbackItemOut]:
    items = work_service.list_agent_callbacks(db, user.id)
    return [
        CallbackItemOut(
            work_item_id=str(i.work_item_id), campaign_id=str(i.campaign_id),
            campaign_name=i.campaign_name, reference=i.reference, due_at=i.due_at,
        )
        for i in items
    ]


@agent_router.get("/stats", response_model=AgentStatsOut)
def get_stats(
    db: Session = Depends(get_session),
    user: User = _agent_only,
) -> AgentStatsOut:
    return AgentStatsOut(**agent_stats.get_today_stats(db, user.id))
