"""Staged bulk-workforce import jobs, rows, and decisions (plan 10.2, 11.2).

Sibling of app/models/imports.py (campaign-contact import), not a shared table:
see PHASE-4B-PLAN.md "Reuse versus new" for why the two stay separate.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class WorkforceImportJob(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "workforce_import_jobs"

    import_type: Mapped[str] = mapped_column(String(30))
    uploader_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    source_filename_display: Mapped[str] = mapped_column(String(255))
    generated_storage_key: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="quarantined")
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    warning_rows: Mapped[int] = mapped_column(Integer, default=0)
    invalid_rows: Mapped[int] = mapped_column(Integer, default=0)
    high_risk_rows: Mapped[int] = mapped_column(Integer, default=0)
    decision_version: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    committed_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class WorkforceImportRow(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "workforce_import_rows"

    import_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_import_jobs.id"))
    row_number: Mapped[int] = mapped_column(Integer)
    action: Mapped[str | None] = mapped_column(String(20), nullable=True)
    external_workforce_id: Mapped[str | None] = mapped_column(String(150), nullable=True)
    normalized_identity: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    parsed_values: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    validation_result: Mapped[str] = mapped_column(String(20))
    validation_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    conflict_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    risk_level: Mapped[str] = mapped_column(String(20), default="routine")
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    committed_entity_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    committed_entity_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    # Field values this row overwrote, captured immediately before its own commit-time
    # mutation. Reversal restores this only if the entity's current value still equals
    # what this row itself produced - otherwise the row is left alone and reported as
    # conflicting (PHASE-4B-PLAN.md "Data model").
    pre_commit_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkforceImportDecision(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "workforce_import_decisions"

    import_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workforce_import_jobs.id"))
    decision_version: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(30))
    decision_tier: Mapped[str] = mapped_column(String(20), default="standard")
    decided_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
