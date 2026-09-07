"""Request and response models for the notification API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class NotificationOut(BaseModel):
    id: str
    category: str
    title: str
    body: str | None
    related_entity_type: str | None
    related_entity_id: str | None
    read_at: datetime | None
    created_at: datetime


class UnreadCountOut(BaseModel):
    unread: int
