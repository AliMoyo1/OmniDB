"""Server-enforced, audited rollout flags (master plan 21.2).

A flag is a second, orthogonal gate next to the existing capability checks -
"not a substitute for authorization." Every real check in this build (imports,
campaign launch, leasing, callbacks, Viewer role grants) happens once, in the
service layer, so both the web and JSON API surfaces get it for free rather
than needing the same check duplicated at every caller.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.models.flags import FeatureFlag

# Permanently false for MVP (master plan 21.2) - enforced here, not just
# documented, the same shape as ADR-009's PROTECTED_DNC_SEMANTIC_CODES: one
# specific thing the code itself refuses to allow, not a convention trusted to
# hold under a direct database edit or a future caller that forgets why.
PERMANENTLY_DISABLED = frozenset({"ai_enabled"})

KNOWN_FLAGS = (
    "campaign_import_enabled",
    "campaign_launch_enabled",
    "shared_pool_enabled",
    "callbacks_enabled",
    "viewer_enabled",
    "retention_execution_enabled",
    "analytics_enabled",
    "ai_enabled",
    "workforce_import_enabled",
)


class FeatureDisabledError(Exception):
    """Raised by a service-layer check when the governing flag is off."""

    def __init__(self, flag_key: str):
        self.flag_key = flag_key
        super().__init__(f"{flag_key} is currently disabled")


class UnknownFlag(Exception):
    pass


class PermanentlyDisabledFlag(Exception):
    pass


def is_enabled(db: Session, flag_key: str) -> bool:
    """Fails safe: an unseeded or unknown key reads as disabled, never
    silently permissive."""
    return bool(db.scalar(select(FeatureFlag.enabled).where(FeatureFlag.flag_key == flag_key)))


def require_enabled(db: Session, flag_key: str) -> None:
    if not is_enabled(db, flag_key):
        raise FeatureDisabledError(flag_key)


def list_flags(db: Session) -> list[FeatureFlag]:
    return list(db.scalars(select(FeatureFlag).order_by(FeatureFlag.flag_key)))


def set_flag(
    db: Session,
    flag_key: str,
    enabled: bool,
    *,
    actor_id: uuid.UUID,
    reason_code: str | None = None,
) -> FeatureFlag:
    if flag_key not in KNOWN_FLAGS:
        raise UnknownFlag(f"{flag_key} is not a known flag")
    if enabled and flag_key in PERMANENTLY_DISABLED:
        raise PermanentlyDisabledFlag(f"{flag_key} is permanently disabled for MVP")
    flag = db.get(FeatureFlag, flag_key)
    if flag is None:
        flag = FeatureFlag(flag_key=flag_key, enabled=enabled)
        db.add(flag)
    else:
        flag.enabled = enabled
    flag.updated_by = actor_id
    db.flush()
    record_audit(
        db, action="flags.set", result="success", actor_user_id=actor_id,
        target_type="feature_flag", reason_code=reason_code,
        event_metadata={"flag_key": flag_key, "enabled": enabled},
    )
    return flag
