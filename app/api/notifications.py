"""Notification inbox API (/api/v1/notifications).

Every endpoint acts only on the caller's own notifications - there is no
capability gate, because a notification is private to its recipient. Thin layer
over app/notifications/service.py.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_csrf
from app.db import get_session
from app.models.identity import User
from app.models.notifications import Notification
from app.notifications import service as notifications_service
from app.notifications.schemas import NotificationOut, UnreadCountOut

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


def _out(notification: Notification) -> NotificationOut:
    return NotificationOut(
        id=str(notification.id),
        category=notification.category,
        title=notification.title,
        body=notification.body,
        related_entity_type=notification.related_entity_type,
        related_entity_id=(
            str(notification.related_entity_id) if notification.related_entity_id else None
        ),
        read_at=notification.read_at,
        created_at=notification.created_at,
    )


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> list[NotificationOut]:
    return [
        _out(n)
        for n in notifications_service.list_for_user(
            db, user.id, unread_only=unread_only, limit=limit
        )
    ]


@router.get("/unread-count", response_model=UnreadCountOut)
def unread_count(
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> UnreadCountOut:
    return UnreadCountOut(unread=notifications_service.unread_count(db, user.id))


@router.post("/{notification_id}/read", dependencies=[Depends(require_csrf)])
def mark_read(
    notification_id: uuid.UUID,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    if not notifications_service.mark_read(db, notification_id, user.id):
        # Not found OR not the caller's - indistinguishable on purpose, so one
        # user can never probe another's inbox by id.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "notification not found")
    db.commit()
    return {"status": "read"}


@router.post("/read-all", dependencies=[Depends(require_csrf)])
def mark_all_read(
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
) -> dict:
    marked = notifications_service.mark_all_read(db, user.id)
    db.commit()
    return {"marked": marked}
