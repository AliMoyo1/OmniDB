"""Dormant email delivery channel for notifications.

A Phase 0 decision (plan): the platform ships with "in-app inbox notifications and
a dormant email capability." This is that capability - the seam is wired into
notify() but switched OFF by default (`email_notifications_enabled`), so the pilot
delivers in-app only with zero extra work per notification. A later build supplies
real SMTP delivery by replacing exactly one function (`_deliver`).

IMPORTANT for the future SMTP build: real delivery must happen POST-COMMIT. A
notification is created inside the unit of work that raised its event and is not
persisted if that work rolls back, so an email must never be sent inline here (it
could announce an action that then reverts). The correct shape is to enqueue a
delivery task after the caller commits. The stub below only logs its intent, which
is safe to do inline; do not add an inline send.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.identity import User
from app.models.notifications import Notification

logger = logging.getLogger(__name__)


def _channel_enabled() -> bool:
    """Whether the email channel is switched on. Indirected through a function so
    the wiring can be exercised in tests without depending on process settings."""
    return get_settings().email_notifications_enabled


def _deliver(to_address: str, subject: str, body: str) -> None:
    """The single real send point - the one function a future SMTP build replaces.
    Today it is a stub: it records the intent to send and returns. See the module
    docstring on why real delivery must be enqueued post-commit, not sent here."""
    logger.info(
        "email notification channel is enabled but SMTP delivery is not yet "
        "implemented; would have emailed %s (subject: %s)",
        to_address,
        subject,
    )


def dispatch(db: Session, notification: Notification) -> bool:
    """Offer one already-created in-app notification to the email channel. Returns
    whether it was handed to the channel. When the channel is off (the default),
    this is a no-op with no database work at all, so in-app-only delivery costs
    nothing. When on, it resolves the recipient's address and hands off to
    `_deliver`."""
    if not _channel_enabled():
        return False
    recipient = db.get(User, notification.recipient_user_id)
    if recipient is None or not recipient.email:
        return False
    _deliver(recipient.email, notification.title, notification.body or "")
    return True
