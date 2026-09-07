"""Celery tasks for campaign retention (ADR-020). Each task owns its DB session."""

from __future__ import annotations

import logging

from app.campaigns import retention
from app.db import SessionLocal
from app.worker import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.campaigns.tasks.detect_completed_campaigns_task",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 2},
    retry_backoff=True,
    retry_jitter=True,
)
def detect_completed_campaigns_task() -> int:
    with SessionLocal() as db:
        try:
            marked = retention.detect_completed_campaigns(db)
            db.commit()
            return marked
        except Exception:
            db.rollback()
            logger.exception("campaign completion-detection task failed")
            raise


@celery_app.task(
    name="app.campaigns.tasks.purge_expired_campaign_data_task",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 2},
    retry_backoff=True,
    retry_jitter=True,
)
def purge_expired_campaign_data_task() -> int:
    with SessionLocal() as db:
        try:
            purged = retention.purge_expired_campaign_data(db)
            db.commit()
            return purged
        except Exception:
            db.rollback()
            logger.exception("campaign retention purge task failed")
            raise
