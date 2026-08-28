"""Feature-flags API (/api/v1/flags): view and toggle the rollout flags in
app/flags/service.py. Gated by MANAGE_ROLES for a first pass - the broadest
non-Super-Admin capability this build has, matching who can already reshape
role/team structure. Revisit a dedicated capability if flag management needs
to be delegated more narrowly during real pilot operations.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_csrf
from app.authz import service as authz
from app.authz.capabilities import MANAGE_ROLES
from app.db import get_session
from app.flags import service as flags_service
from app.flags.schemas import FlagOut, FlagSetRequest
from app.flags.service import PermanentlyDisabledFlag, UnknownFlag
from app.models.flags import FeatureFlag
from app.models.identity import User

router = APIRouter(prefix="/api/v1/flags", tags=["flags"])


def _require_manage_roles(
    db: Session = Depends(get_session), user: User = Depends(get_current_user)
) -> User:
    if not authz.has_assigned_capability(db, user.id, MANAGE_ROLES):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized")
    return user


def _flag_out(flag: FeatureFlag) -> FlagOut:
    return FlagOut(
        flag_key=flag.flag_key,
        enabled=flag.enabled,
        updated_by=str(flag.updated_by) if flag.updated_by else None,
        updated_at=flag.updated_at,
    )


@router.get("", response_model=list[FlagOut])
def list_flags(
    db: Session = Depends(get_session),
    user: User = Depends(_require_manage_roles),
) -> list[FlagOut]:
    return [_flag_out(flag) for flag in flags_service.list_flags(db)]


@router.post("/{flag_key}", response_model=FlagOut, dependencies=[Depends(require_csrf)])
def set_flag(
    flag_key: str,
    payload: FlagSetRequest,
    db: Session = Depends(get_session),
    user: User = Depends(_require_manage_roles),
) -> FlagOut:
    try:
        flag = flags_service.set_flag(
            db, flag_key, payload.enabled, actor_id=user.id, reason_code=payload.reason_code
        )
    except UnknownFlag as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except PermanentlyDisabledFlag as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    db.commit()
    return _flag_out(flag)
