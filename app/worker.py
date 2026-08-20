"""Celery application: one worker/scheduler stack (ADR-010).

The web process enqueues tasks; a separate worker container executes them; Beat
triggers periodic jobs. Task bodies open their own database session because they run
outside the request lifecycle.
"""

from __future__ import annotations

from celery import Celery

from app.config import get_settings

_settings = get_settings()

celery_app = Celery("ciphercontact", broker=_settings.redis_url, backend=_settings.redis_url)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=600,
    task_soft_time_limit=540,
    task_always_eager=_settings.celery_task_always_eager,
    task_eager_propagates=_settings.celery_task_always_eager,
)

celery_app.conf.beat_schedule = {
    "cleanup-expired-imports": {
        "task": "app.imports.tasks.cleanup_expired_imports_task",
        "schedule": 3600.0,
    },
    "reclaim-expired-leases": {
        "task": "app.work.tasks.reclaim_expired_leases_task",
        "schedule": 120.0,
    },
}

celery_app.autodiscover_tasks(["app.imports", "app.work"])
