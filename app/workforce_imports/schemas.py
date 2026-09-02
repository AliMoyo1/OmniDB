"""Request and response models for the workforce-import API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class WorkforceImportJobOut(BaseModel):
    id: str
    import_type: str
    state: str
    source_filename_display: str
    total_rows: int
    valid_rows: int
    warning_rows: int
    invalid_rows: int
    high_risk_rows: int
    decision_version: int
    created_at: datetime
    committed_at: datetime | None
    reversed_at: datetime | None


class InvalidExample(BaseModel):
    row_number: int
    detail: str | None
    conflict_type: str | None


class WorkforceImportPreviewOut(BaseModel):
    job: WorkforceImportJobOut
    invalid_examples: list[InvalidExample]


class WorkforceImportDecisionRequest(BaseModel):
    decision: str  # "approve" | "reject" | "cancel"
    decision_tier: str  # "standard" | "high_risk"
    note: str | None = None
    acknowledge_warnings: bool = False


class WorkforceImportDecisionOut(BaseModel):
    decision_version: int
    decision: str
    decision_tier: str


class WorkforceImportCommitRequest(BaseModel):
    decision_version: int
    idempotency_key: str


class WorkforceImportCommitOut(BaseModel):
    job_id: str
    outcomes: list[dict]
    activation_tokens: dict[str, str]


class WorkforceImportReverseOut(BaseModel):
    job_id: str
    reversed: list[dict]
    skipped: list[dict]
