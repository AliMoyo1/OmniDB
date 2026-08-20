"""Authorization dependencies (default-deny capability gate)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.authz import service as authz
from app.db import get_session
from app.models.identity import User


def require_capability(capability: str) -> Callable[..., User]:
    """Return a dependency that yields the current user only if they hold `capability`."""

    def dependency(
        db: Session = Depends(get_session),
        user: User = Depends(get_current_user),
    ) -> User:
        if not authz.has_capability(db, user.id, capability):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not authorized")
        return user

    return dependency
