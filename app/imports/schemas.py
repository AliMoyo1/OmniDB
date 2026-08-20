"""Request and response models for the import API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ImportMappingRequest(BaseModel):
    phone_column: str
    name_column: str | None = None
    metadata_columns: list[str] = []


class ImportJobOut(BaseModel):
    id: str
    campaign_id: str
    state: str
    source_filename_display: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_rows: int
    suppression_hits: int
    decision_version: int
    created_at: datetime
    committed_at: datetime | None


class InvalidExample(BaseModel):
    row_number: int
    detail: str | None


class ImportPreviewOut(BaseModel):
    job: ImportJobOut
    invalid_examples: list[InvalidExample]


class ImportDecisionRequest(BaseModel):
    decision: str  # "approve" | "reject" | "cancel"
    note: str | None = None


class ImportDecisionOut(BaseModel):
    decision_version: int
    decision: str


class ImportCommitRequest(BaseModel):
    decision_version: int
    idempotency_key: str


class ImportCommitOut(BaseModel):
    job_id: str
    inserted: int
    suppressed: int
