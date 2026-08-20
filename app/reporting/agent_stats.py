"""Approved personal metrics for an agent, computed from immutable call attempts.

Reproducible from source events, not a separate mutable counter (plan 10.2 metric_rollups
note; here computed live since Phase 3 volumes don't yet need a precomputed rollup).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.campaign import CampaignDispositionDefinition
from app.models.work import CallAttempt


def _count(
    db: Session, agent_id: uuid.UUID, since: datetime, *, only_flag: str | None = None
) -> int:
    stmt = select(func.count(CallAttempt.id)).where(
        CallAttempt.agent_id == agent_id, CallAttempt.created_at >= since
    )
    if only_flag == "connected":
        stmt = stmt.join(
            CampaignDispositionDefinition,
            CallAttempt.disposition_definition_id == CampaignDispositionDefinition.id,
        ).where(CampaignDispositionDefinition.counts_as_connected.is_(True))
    elif only_flag == "conversion":
        stmt = stmt.join(
            CampaignDispositionDefinition,
            CallAttempt.disposition_definition_id == CampaignDispositionDefinition.id,
        ).where(CampaignDispositionDefinition.counts_as_conversion.is_(True))
    elif only_flag == "dnc":
        stmt = stmt.where(CallAttempt.explicit_dnc_requested.is_(True))
    return db.scalar(stmt) or 0


def get_today_stats(db: Session, agent_id: uuid.UUID) -> dict:
    today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "period": "today",
        "total_attempts": _count(db, agent_id, today_start),
        "connected": _count(db, agent_id, today_start, only_flag="connected"),
        "conversions": _count(db, agent_id, today_start, only_flag="conversion"),
        "dnc_requests": _count(db, agent_id, today_start, only_flag="dnc"),
    }
