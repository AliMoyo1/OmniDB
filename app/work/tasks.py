"""Celery tasks wrapping the work-item service. Each task owns its own DB session."""

from __future__ import annotations

import logging

from app.db import SessionLocal
from app.work import service as work_service
from app.worker import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.work.tasks.reclaim_expired_leases_task")
def reclaim_expired_leases_task() -> int:
    with SessionLocal() as db:
        try:
            count = work_service.reclaim_expired_leases(db)
            db.commit()
            return count
        except Exception:
            db.rollback()
            logger.exception("expired-lease reclaim task failed")
            raise
