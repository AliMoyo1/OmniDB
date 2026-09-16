"""Celery tasks for achievement refresh (plan 7.5). Each task owns its own DB
session and never raises into the request that enqueued it - a failure here
is a gamification delay, not a work-flow failure.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.flags import service as flags
from app.gamification import service as gamification_service
from app.models.base import utcnow
from app.models.work import CallAttempt
from app.worker import celery_app

logger = logging.getLogger(__name__)

# How far back to look for "recently active" agents during reconciliation -
# wide enough to catch a worker outage spanning a shift, not so wide that
# every agent who ever logged an attempt gets rechecked every run.
_RECONCILIATION_WINDOW = timedelta(hours=24)


@celery_app.task(
    name="app.gamification.tasks.refresh_agent_achievements_task",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3},
    retry_backoff=True,
    retry_jitter=True,
)
def refresh_agent_achievements_task(agent_id: str) -> list[str]:
    with SessionLocal() as db:
        # Defensive no-op (plan 9): a task already queued when the flag was
        # on must not keep awarding after a rollback disables it.
        if not flags.is_enabled(db, "agent_gamification_enabled"):
            return []
        try:
            awarded = gamification_service.refresh_agent_achievements(db, uuid.UUID(agent_id))
            db.commit()
            return awarded
        except Exception:
            db.rollback()
            logger.exception("achievement refresh task failed for agent %s", agent_id)
            raise


@celery_app.task(
    name="app.gamification.tasks.reconcile_agent_achievements_task",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 2},
    retry_backoff=True,
    retry_jitter=True,
)
def reconcile_agent_achievements_task() -> int:
    with SessionLocal() as db:
        if not flags.is_enabled(db, "agent_gamification_enabled"):
            return 0
        try:
            since = utcnow() - _RECONCILIATION_WINDOW
            agent_ids = db.scalars(
                select(CallAttempt.agent_id).where(CallAttempt.created_at >= since).distinct()
            ).all()
            for agent_id in agent_ids:
                gamification_service.refresh_agent_achievements(db, agent_id)
            db.commit()
            return len(agent_ids)
        except Exception:
            db.rollback()
            logger.exception("achievement reconciliation task failed")
            raise
