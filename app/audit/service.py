"""Append-only audit writing. Never store raw personal data here (plan 9.8)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.audit import AuditEvent


def record_audit(
    db: Session,
    *,
    action: str,
    result: str,
    actor_user_id: uuid.UUID | None = None,
    actor_role: str | None = None,
    organization_id: uuid.UUID | None = None,
    team_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
    source_ip: str | None = None,
    user_agent_summary: str | None = None,
    reason_code: str | None = None,
    event_metadata: dict | None = None,
) -> AuditEvent:
    event = AuditEvent(
        action=action,
        result=result,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        organization_id=organization_id,
        team_id=team_id,
        target_type=target_type,
        target_id=target_id,
        correlation_id=correlation_id,
        source_ip=source_ip,
        user_agent_summary=user_agent_summary,
        reason_code=reason_code,
        event_metadata=event_metadata,
    )
    db.add(event)
    return event
