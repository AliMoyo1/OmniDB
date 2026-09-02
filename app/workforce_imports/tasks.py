"""Celery tasks wrapping the workforce import service. Each task owns its own DB session."""

from __future__ import annotations

import logging
import uuid

from app.db import SessionLocal
from app.worker import celery_app
from app.workforce_imports import service

logger = logging.getLogger(__name__)


@celery_app.task(
    name="app.workforce_imports.tasks.parse_workforce_import_job_task",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 2},
    retry_backoff=True,
    retry_jitter=True,
)
def parse_workforce_import_job_task(job_id: str) -> None:
    with SessionLocal() as db:
        try:
            service.parse_job(db, uuid.UUID(job_id))
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("workforce import parse task failed for job %s", job_id)
            raise


@celery_app.task(
    name="app.workforce_imports.tasks.cleanup_expired_workforce_imports_task",
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 2},
    retry_backoff=True,
    retry_jitter=True,
)
def cleanup_expired_workforce_imports_task() -> int:
    with SessionLocal() as db:
        try:
            count = service.cleanup_expired_jobs(db)
            db.commit()
            return count
        except Exception:
            db.rollback()
            logger.exception("workforce import cleanup task failed")
            raise
