"""Staged campaign-import jobs, rows, and decisions."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class ImportJob(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "import_jobs"

    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("campaigns.id"))
    uploader_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    source_filename_display: Mapped[str] = mapped_column(String(255))
    generated_storage_key: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20), default="uploaded")
    parser_version: Mapped[str] = mapped_column(String(20), default="1")
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    invalid_rows: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0)
    suppression_hits: Mapped[int] = mapped_column(Integer, default=0)
    decision_version: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Recorded at commit time so a repeated idempotency key can return the same result
    # without recomputing it (plan 11.8 Stage F item 7).
    committed_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class ImportRow(UUIDMixin, Base):
    __tablename__ = "import_rows"

    import_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_jobs.id"))
    row_number: Mapped[int] = mapped_column(Integer)
    raw_phone_protected: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    phone_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    canonical_values: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    validation_result: Mapped[str] = mapped_column(String(20))
    validation_detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    duplicate_category: Mapped[str | None] = mapped_column(String(30), nullable=True)
    suppression_match: Mapped[bool] = mapped_column(default=False)


class ImportDecision(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "import_decisions"

    import_job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("import_jobs.id"))
    decision_version: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(30))
    decided_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
