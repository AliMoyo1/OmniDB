"""In-app notification inbox service.

A notification is private to its recipient: every read and mutation here is
scoped to one user's own rows, never gated by a shared capability. Producers
call notify(); the recipient reads and marks their own inbox.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.base import utcnow
from app.models.notifications import Notification
from app.notifications import email as email_channel


def notify(
    db: Session,
    *,
    recipient_id: uuid.UUID,
    category: str,
    title: str,
    body: str | None = None,
    related_entity_type: str | None = None,
    related_entity_id: uuid.UUID | None = None,
) -> Notification:
    """Create one notification for one recipient. Flushed but not committed - the
    caller commits as part of whatever unit of work raised the event, so a
    notification is never persisted for an action that then rolls back."""
    notification = Notification(
        recipient_user_id=recipient_id,
        category=category,
        title=title,
        body=body,
        related_entity_type=related_entity_type,
        related_entity_id=related_entity_id,
    )
    db.add(notification)
    db.flush()
    # Offer it to the dormant email channel too (no-op unless switched on). The
    # in-app row is the source of truth; email is an additional delivery.
    email_channel.dispatch(db, notification)
    return notification


def list_for_user(
    db: Session, user_id: uuid.UUID, *, unread_only: bool = False, limit: int = 50
) -> list[Notification]:
    stmt = select(Notification).where(Notification.recipient_user_id == user_id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
    return list(db.scalars(stmt))


def unread_count(db: Session, user_id: uuid.UUID) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.recipient_user_id == user_id,
                Notification.read_at.is_(None),
            )
        )
        or 0
    )


def mark_read(db: Session, notification_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    """Mark one notification read. Scoped to the caller's own rows: a
    notification that does not exist OR belongs to someone else is
    indistinguishable here (both return False), so one user can never probe for
    or touch another's inbox. Idempotent - re-marking an already-read one keeps
    its original read_at."""
    notification = db.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.recipient_user_id == user_id,
        )
    )
    if notification is None:
        return False
    if notification.read_at is None:
        notification.read_at = utcnow()
    return True


def mark_all_read(db: Session, user_id: uuid.UUID) -> int:
    """Mark every one of this user's unread notifications read. Returns how many
    were flipped. A user's unread set is small (their own inbox), so this reads
    and stamps them rather than issuing a bulk UPDATE, keeping an exact count."""
    now = utcnow()
    unread = list(
        db.scalars(
            select(Notification).where(
                Notification.recipient_user_id == user_id,
                Notification.read_at.is_(None),
            )
        )
    )
    for notification in unread:
        notification.read_at = now
    return len(unread)
