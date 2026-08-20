"""Celery tasks wrapping the import service. Each task owns its own DB session."""

from __future__ import annotations

import logging
import uuid

from app.db import SessionLocal
from app.imports import service
from app.worker import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.imports.tasks.parse_import_job_task", bind=True, max_retries=2)
def parse_import_job_task(
    self,
    job_id: str,
    phone_column: str,
    name_column: str | None,
    metadata_columns: list[str],
) -> None:
    with SessionLocal() as db:
        try:
            service.parse_job(
                db,
                uuid.UUID(job_id),
                phone_column=phone_column,
                name_column=name_column,
                metadata_columns=metadata_columns,
            )
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("import parse task failed for job %s", job_id)
            raise


@celery_app.task(name="app.imports.tasks.cleanup_expired_imports_task")
def cleanup_expired_imports_task() -> int:
    with SessionLocal() as db:
        try:
            count = service.cleanup_expired_jobs(db)
            db.commit()
            return count
        except Exception:
            db.rollback()
            logger.exception("import cleanup task failed")
            raise
