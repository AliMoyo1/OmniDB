"""Workforce import job orchestration: quarantine, parse, preview, decide, atomic
commit, compensating reversal, cleanup.

Follows PHASE-4B-PLAN.md's mapping of master plan 11.2 steps 1-13. The two-person
high-risk rule (a job with any high_risk row needs a second, separately-capable,
non-uploader decision before it can commit) is enforced here, not only at the HTTP
layer, so no caller can bypass it.
"""

from __future__ import annotations

import itertools
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import PurePosixPath

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.authz import service as authz
from app.config import get_settings
from app.flags import service as flags
from app.imports import storage, validators
from app.imports.parser import ParseLimitExceeded, parse_file
from app.models.authz import ReportingAssignment, RoleAssignment
from app.models.base import utcnow
from app.models.identity import Team, TeamMembership, User
from app.models.workforce_imports import (
    WorkforceImportDecision,
    WorkforceImportJob,
    WorkforceImportRow,
)
from app.workforce import service as workforce_service
from app.workforce.service import ROLE_APPOINTMENT_CAPABILITY, DuplicateIdentity
from app.workforce_imports import classify

_PARSE_BATCH_SIZE = 500
IMPORT_TYPES = (
    "users",
    "explicit_deactivations",
    "team_memberships",
    "role_assignments",
    "reporting_assignments",
)
REQUIRED_COLUMNS = {
    "users": {"action", "external_workforce_id", "login_identifier", "display_name"},
    "explicit_deactivations": {"external_workforce_id", "reason_code"},
    "team_memberships": {"action", "external_workforce_id", "team_code"},
    "role_assignments": {
        "action", "external_workforce_id", "role_code", "scope_type", "reason_code",
    },
    "reporting_assignments": {"external_workforce_id", "supervisor_workforce_id"},
}
_CLASSIFIERS = {
    "users": classify.classify_users_row,
    "explicit_deactivations": classify.classify_deactivation_row,
    "team_memberships": classify.classify_team_membership_row,
    "role_assignments": classify.classify_role_assignment_row,
    "reporting_assignments": classify.classify_reporting_assignment_row,
}


class WorkforceImportError(Exception):
    """Base class for workforce import pipeline errors."""


class UploadRejected(WorkforceImportError):
    pass


class UnknownImportType(WorkforceImportError):
    pass


class ImportNotReady(WorkforceImportError):
    pass


class StaleDecisionVersion(WorkforceImportError):
    pass


class SelfApproval(WorkforceImportError):
    pass


class InsufficientApprovalAuthority(WorkforceImportError):
    pass


def _truncate_filename_preserving_extension(display_filename: str, max_len: int = 255) -> str:
    if len(display_filename) <= max_len:
        return display_filename
    parts = PurePosixPath(display_filename)
    suffix = parts.suffix
    keep = max(0, max_len - len(suffix))
    return parts.stem[:keep] + suffix


def create_import_job(
    db: Session,
    *,
    import_type: str,
    uploader_id: uuid.UUID,
    display_filename: str,
    file_chunks: Iterable[bytes],
) -> WorkforceImportJob:
    if import_type not in IMPORT_TYPES:
        raise UnknownImportType(f"unsupported import type: {import_type}")
    flags.require_enabled(db, "workforce_import_enabled")
    settings = get_settings()
    validators.check_extension(display_filename)  # cheap check before writing anything
    stored_filename = _truncate_filename_preserving_extension(display_filename)
    storage_key = storage.generate_storage_key()
    try:
        size, file_hash = storage.write_streamed(
            storage_key, file_chunks, settings.upload_max_bytes
        )
        validators.validate_upload(storage.path_for(storage_key), display_filename)
    except (storage.UploadTooLarge, validators.UploadValidationError) as exc:
        storage.delete(storage_key)
        raise UploadRejected(str(exc)) from exc
    except Exception:
        storage.delete(storage_key)
        raise

    job = WorkforceImportJob(
        import_type=import_type,
        uploader_id=uploader_id,
        source_filename_display=stored_filename,
        generated_storage_key=storage_key,
        file_hash=file_hash,
        state="quarantined",
        expires_at=utcnow() + timedelta(hours=settings.import_expiry_hours),
    )
    db.add(job)
    db.flush()
    record_audit(
        db, action="workforce_import.upload", result="success", actor_user_id=uploader_id,
        target_type="workforce_import_job", target_id=job.id,
        event_metadata={"import_type": import_type, "file_hash": file_hash, "size": size},
    )
    return job


def parse_job(db: Session, job_id: uuid.UUID) -> None:
    job = db.execute(
        select(WorkforceImportJob).where(WorkforceImportJob.id == job_id).with_for_update()
    ).scalar_one_or_none()
    if job is None or job.state != "quarantined":
        return

    job.state = "parsing"
    db.flush()
    ext = validators.check_extension(job.source_filename_display)
    path = storage.path_for(job.generated_storage_key)

    total = valid = warning = invalid = high_risk = 0
    seen_identities: set[str] = set()
    batch: list[WorkforceImportRow] = []
    classifier = _CLASSIFIERS[job.import_type]

    try:
        rows_iter = parse_file(path, ext)
        first_row = next(rows_iter, None)
        if first_row is not None:
            missing = REQUIRED_COLUMNS[job.import_type] - set(first_row.values)
            if missing:
                raise ParseLimitExceeded(
                    f"file header is missing required column(s): {', '.join(sorted(missing))} "
                    "- download the current template"
                )
        all_rows = itertools.chain([first_row], rows_iter) if first_row is not None else ()

        for row in all_rows:
            total += 1
            result = classifier(row, db=db, seen_identities=seen_identities)
            batch.append(
                WorkforceImportRow(
                    import_job_id=job.id,
                    row_number=result.row_number,
                    action=result.action,
                    external_workforce_id=result.external_workforce_id,
                    normalized_identity=result.normalized_identity,
                    parsed_values=result.parsed_values,
                    validation_result=result.validation_result,
                    validation_detail=result.validation_detail,
                    conflict_type=result.conflict_type,
                    risk_level=result.risk_level,
                )
            )
            if result.validation_result == "invalid":
                invalid += 1
            elif result.validation_result == "warning":
                warning += 1
            else:
                valid += 1
            if result.risk_level == "high_risk" and result.validation_result == "valid":
                # Warning-tier high-risk rows (e.g. "already inactive") are no-ops at
                # commit time - they must not force an approval step for nothing.
                high_risk += 1

            if len(batch) >= _PARSE_BATCH_SIZE:
                db.add_all(batch)
                db.flush()
                batch.clear()

        if batch:
            db.add_all(batch)
            db.flush()

    except (ParseLimitExceeded, UnicodeDecodeError, ValueError) as exc:
        job.state = "failed"
        job.error_summary = str(exc)[:1000]
        # AuditEvent.reason_code is String(50) - the full message is preserved above
        # in error_summary (String(1000)); this is a short tag, not the detail.
        record_audit(
            db, action="workforce_import.parse", result="failure", actor_user_id=job.uploader_id,
            target_type="workforce_import_job", target_id=job.id, reason_code=str(exc)[:50],
        )
        return

    job.total_rows = total
    job.valid_rows = valid
    job.warning_rows = warning
    job.invalid_rows = invalid
    job.high_risk_rows = high_risk
    job.state = "parsed"
    record_audit(
        db, action="workforce_import.parse", result="success", actor_user_id=job.uploader_id,
        target_type="workforce_import_job", target_id=job.id,
        event_metadata={"total": total, "valid": valid, "invalid": invalid, "high_risk": high_risk},
    )


def get_preview(db: Session, job: WorkforceImportJob) -> list[WorkforceImportRow]:
    return list(
        db.scalars(
            select(WorkforceImportRow)
            .where(
                WorkforceImportRow.import_job_id == job.id,
                WorkforceImportRow.validation_result == "invalid",
            )
            .order_by(WorkforceImportRow.row_number)
            .limit(5)
        )
    )


def _high_risk_rows(db: Session, job: WorkforceImportJob) -> list[WorkforceImportRow]:
    return list(
        db.scalars(
            select(WorkforceImportRow).where(
                WorkforceImportRow.import_job_id == job.id,
                WorkforceImportRow.risk_level == "high_risk",
                WorkforceImportRow.validation_result == "valid",
            )
        )
    )


def _row_authority_ok(db: Session, approver_id: uuid.UUID, job: WorkforceImportJob,
                       row: WorkforceImportRow) -> bool:
    """The precise authority bar for one high-risk row's specific effect - not the
    same check for every import type, since "may disable this user" and "may grant
    this exact role at this exact scope" are different questions with different
    existing answers elsewhere in this codebase."""
    if job.import_type == "explicit_deactivations":
        if row.normalized_identity is None:
            return True
        return workforce_service.can_manage_user(db, approver_id, row.normalized_identity)
    if job.import_type == "role_assignments" and row.action == "assign":
        values = row.parsed_values or {}
        role_code = values.get("role_code")
        scope_type = values.get("scope_type")
        scope_id = uuid.UUID(values["scope_id"]) if values.get("scope_id") else None
        capability = ROLE_APPOINTMENT_CAPABILITY.get(role_code) if role_code else None
        if capability is None or scope_type is None:
            return False
        return authz.has_scope_capability(
            db, approver_id, capability, scope_type=scope_type, scope_id=scope_id
        )
    return True


def _assert_qualified_high_risk_approver(
    db: Session, job: WorkforceImportJob, approver_id: uuid.UUID
) -> None:
    """Separation of duties plus per-row live authority - reused by both the
    high_risk decision and, again, by commit and reverse (which may run well
    after the decision was recorded, against whatever authority exists then)."""
    if approver_id == job.uploader_id:
        raise SelfApproval("the uploader cannot also approve a high-risk import")
    for row in _high_risk_rows(db, job):
        if not _row_authority_ok(db, approver_id, job, row):
            raise InsufficientApprovalAuthority(
                f"approver lacks authority over row {row.row_number}"
            )


def record_decision(
    db: Session,
    job: WorkforceImportJob,
    *,
    decided_by: uuid.UUID,
    decision: str,
    decision_tier: str,
    note: str | None,
) -> WorkforceImportDecision:
    if decision not in ("approve", "reject", "cancel"):
        raise ValueError("decision must be approve, reject, or cancel")
    if decision_tier not in ("standard", "high_risk"):
        raise ValueError("decision_tier must be standard or high_risk")
    if decision_tier == "high_risk":
        if job.high_risk_rows == 0:
            raise ValueError("this import has no high-risk rows to approve")
        if decision == "approve":
            _assert_qualified_high_risk_approver(db, job, decided_by)

    job.decision_version += 1
    row = WorkforceImportDecision(
        import_job_id=job.id,
        decision_version=job.decision_version,
        decision=decision,
        decision_tier=decision_tier,
        decided_by=decided_by,
        note=note,
    )
    db.add(row)
    db.flush()
    record_audit(
        db, action="workforce_import.decide", result="success", actor_user_id=decided_by,
        target_type="workforce_import_job", target_id=job.id, reason_code=decision,
        event_metadata={"decision_tier": decision_tier},
    )
    return row


def _latest_decision(
    db: Session, job: WorkforceImportJob, *, tier: str
) -> WorkforceImportDecision | None:
    """The most recent decision recorded for one tier, independent of the other
    tier's activity. Both tiers share job.decision_version as a single incrementing
    counter (each decision call, either tier, bumps it once), so a tier's own
    decision is not necessarily AT the job's current version once the other tier
    has since recorded one too - filtering on an exact version match here would
    make an already-recorded standard approval unfindable the moment a high-risk
    decision is recorded afterward. commit_job separately requires the caller's
    supplied decision_version to equal the current job.decision_version, which is
    the actual staleness guard against a *third* decision landing after the one
    being acted on."""
    return db.scalar(
        select(WorkforceImportDecision)
        .where(
            WorkforceImportDecision.import_job_id == job.id,
            WorkforceImportDecision.decision_tier == tier,
        )
        .order_by(WorkforceImportDecision.decision_version.desc())
        .limit(1)
    )


def current_decisions(
    db: Session, job: WorkforceImportJob
) -> dict[str, WorkforceImportDecision | None]:
    """The most recent standard and (if applicable) high_risk decision, for
    display - a rejected/cancelled decision still shows here rather than looking
    like "nothing decided yet"."""
    return {
        "standard": _latest_decision(db, job, tier="standard"),
        "high_risk": _latest_decision(db, job, tier="high_risk") if job.high_risk_rows else None,
    }


@dataclass
class _RowOutcome:
    row_number: int
    outcome: str  # "created" | "updated" | "reactivated" | "deactivated" | "skipped_conflict"
    activation_token: str | None = None


def _commit_users_row(
    db: Session, row: WorkforceImportRow, *, actor_id: uuid.UUID
) -> _RowOutcome | None:
    values = row.parsed_values or {}
    if row.action == "create":
        try:
            user, token = workforce_service.create_user(
                db, email=values["login_identifier"], display_name=values["display_name"],
                workforce_id=row.external_workforce_id, created_by=actor_id,
            )
        except DuplicateIdentity:
            row.conflict_type = "already_exists"
            return _RowOutcome(row.row_number, "skipped_conflict")
        if values.get("start_date"):
            user.start_date = date.fromisoformat(values["start_date"])
        if values.get("end_date"):
            user.end_date = date.fromisoformat(values["end_date"])
        row.committed_entity_type = "user"
        row.committed_entity_id = user.id
        return _RowOutcome(row.row_number, "created", activation_token=token)

    if row.normalized_identity is None:
        return None
    # normalized_identity was resolved against a real user at parse time, and users
    # are never hard-deleted (only deactivated) - the row is guaranteed to exist.
    target = db.execute(
        select(User).where(User.id == row.normalized_identity).with_for_update()
    ).scalar_one()

    if row.action == "update":
        snapshot = {
            field: (getattr(target, field).isoformat() if field in ("start_date", "end_date")
                    and getattr(target, field) else getattr(target, field))
            for field in values
        }
        changes = {
            field: (date.fromisoformat(value) if field in ("start_date", "end_date") else value)
            for field, value in values.items()
        }
        workforce_service.update_user(db, target, changes=changes, actor_id=actor_id)
        row.pre_commit_snapshot = snapshot
        row.committed_entity_type = "user"
        row.committed_entity_id = target.id
        return _RowOutcome(row.row_number, "updated")

    if row.action == "reactivate":
        if target.active:
            row.conflict_type = "already_active"
            return _RowOutcome(row.row_number, "skipped_conflict")
        workforce_service.reactivate_user(db, target, actor_id=actor_id, reason_code="bulk_import")
        row.committed_entity_type = "user"
        row.committed_entity_id = target.id
        return _RowOutcome(row.row_number, "reactivated")

    return None


def _commit_deactivation_row(
    db: Session, row: WorkforceImportRow, *, actor_id: uuid.UUID
) -> _RowOutcome | None:
    if row.normalized_identity is None:
        return None
    target = db.execute(
        select(User).where(User.id == row.normalized_identity).with_for_update()
    ).scalar_one()
    if not target.active:
        row.conflict_type = "already_inactive"
        return _RowOutcome(row.row_number, "skipped_conflict")
    reason_code = (row.parsed_values or {}).get("reason_code") or "bulk_import"
    try:
        workforce_service.disable_user(db, target, actor_id=actor_id, reason_code=reason_code)
    except authz.SelfApprovalError:
        # The approver's own identity is the row's target - a file cannot let the
        # approver act on themselves any more than the manual disable screen would.
        row.conflict_type = "self_target_not_allowed"
        return _RowOutcome(row.row_number, "skipped_conflict")
    row.committed_entity_type = "user"
    row.committed_entity_id = target.id
    return _RowOutcome(row.row_number, "deactivated")


def _commit_team_membership_row(
    db: Session, row: WorkforceImportRow, *, actor_id: uuid.UUID
) -> _RowOutcome | None:
    if row.normalized_identity is None:
        return None
    values = row.parsed_values or {}
    team = db.get(Team, uuid.UUID(values["team_id"]))
    if team is None:
        row.conflict_type = "unknown_team"
        return _RowOutcome(row.row_number, "skipped_conflict")
    current = db.execute(
        select(TeamMembership)
        .where(
            TeamMembership.team_id == team.id, TeamMembership.user_id == row.normalized_identity,
            TeamMembership.membership_status == "active", TeamMembership.effective_to.is_(None),
        )
        .with_for_update()
    ).scalar_one_or_none()

    if row.action == "add":
        if current is not None:
            row.conflict_type = "already_member"
            return _RowOutcome(row.row_number, "skipped_conflict")
        membership = workforce_service.add_team_membership(
            db, team, user_id=row.normalized_identity, added_by=actor_id
        )
        outcome = "added"
    else:  # end
        if current is None:
            row.conflict_type = "not_a_member"
            return _RowOutcome(row.row_number, "skipped_conflict")
        membership = workforce_service.end_team_membership(
            db, current, ended_by=actor_id, reason_code=values.get("reason_code")
        )
        outcome = "ended"
    row.committed_entity_type = "team_membership"
    row.committed_entity_id = membership.id
    return _RowOutcome(row.row_number, outcome)


def _commit_role_assignment_row(
    db: Session, row: WorkforceImportRow, *, actor_id: uuid.UUID
) -> _RowOutcome | None:
    if row.normalized_identity is None:
        return None
    values = row.parsed_values or {}
    role_code = values["role_code"]
    scope_type = values["scope_type"]
    scope_id = uuid.UUID(values["scope_id"]) if values.get("scope_id") else None
    current = db.execute(
        select(RoleAssignment)
        .where(
            RoleAssignment.user_id == row.normalized_identity,
            RoleAssignment.role_code == role_code, RoleAssignment.scope_type == scope_type,
            RoleAssignment.scope_id == scope_id, RoleAssignment.status == "active",
            RoleAssignment.effective_to.is_(None),
        )
        .with_for_update()
    ).scalar_one_or_none()

    if row.action == "assign":
        if current is not None:
            row.conflict_type = "already_granted"
            return _RowOutcome(row.row_number, "skipped_conflict")
        try:
            assignment = workforce_service.assign_role(
                db, target_user_id=row.normalized_identity, role_code=role_code,
                scope_type=scope_type, scope_id=scope_id, appointed_by=actor_id,
                reason_code=values["reason_code"],
            )
        except authz.SelfApprovalError:
            row.conflict_type = "self_target_not_allowed"
            return _RowOutcome(row.row_number, "skipped_conflict")
        outcome = "assigned"
    else:  # end
        if current is None:
            row.conflict_type = "not_assigned"
            return _RowOutcome(row.row_number, "skipped_conflict")
        try:
            assignment = workforce_service.end_role_assignment(
                db, current, ended_by=actor_id, reason_code=values["reason_code"]
            )
        except authz.SelfApprovalError:
            row.conflict_type = "self_target_not_allowed"
            return _RowOutcome(row.row_number, "skipped_conflict")
        outcome = "ended"
    row.committed_entity_type = "role_assignment"
    row.committed_entity_id = assignment.id
    return _RowOutcome(row.row_number, outcome)


def _commit_reporting_assignment_row(
    db: Session, row: WorkforceImportRow, *, actor_id: uuid.UUID
) -> _RowOutcome | None:
    if row.normalized_identity is None:
        return None
    values = row.parsed_values or {}
    supervisor_id = uuid.UUID(values["supervisor_user_id"])
    prior_supervisor_id = db.scalar(
        select(ReportingAssignment.supervisor_user_id).where(
            ReportingAssignment.subordinate_user_id == row.normalized_identity,
            ReportingAssignment.context_type == "organization",
            ReportingAssignment.context_id.is_(None),
            ReportingAssignment.assignment_type == "primary",
            ReportingAssignment.status == "active",
            ReportingAssignment.effective_to.is_(None),
        )
    )
    if prior_supervisor_id == supervisor_id:
        # Re-validated at commit time, not trusted from preview: someone else may
        # have already set this exact supervisor since parse. A no-op, not a
        # blocking conflict.
        row.conflict_type = "already_set"
        return _RowOutcome(row.row_number, "skipped_conflict")
    try:
        line = workforce_service.set_reporting_line(
            db, subordinate_user_id=row.normalized_identity, supervisor_user_id=supervisor_id,
            assigned_by=actor_id, reason_code=values.get("reason_code"),
        )
    except workforce_service.SelfSupervision:
        row.conflict_type = "self_target_not_allowed"
        return _RowOutcome(row.row_number, "skipped_conflict")
    row.pre_commit_snapshot = {
        "prior_supervisor_id": str(prior_supervisor_id) if prior_supervisor_id else None
    }
    row.committed_entity_type = "reporting_assignment"
    row.committed_entity_id = line.id
    return _RowOutcome(row.row_number, "set")


_COMMIT_ROW_FUNCTIONS = {
    "users": _commit_users_row,
    "explicit_deactivations": _commit_deactivation_row,
    "team_memberships": _commit_team_membership_row,
    "role_assignments": _commit_role_assignment_row,
    "reporting_assignments": _commit_reporting_assignment_row,
}


def commit_job(
    db: Session,
    job_id: uuid.UUID,
    *,
    actor_id: uuid.UUID,
    decision_version: int,
    idempotency_key: str,
) -> dict:
    job = db.execute(
        select(WorkforceImportJob).where(WorkforceImportJob.id == job_id).with_for_update()
    ).scalar_one_or_none()
    if job is None:
        raise ImportNotReady("import job not found")

    if job.state == "committed":
        if job.idempotency_key == idempotency_key:
            # Tokens were single-use and shown only on the original commit; a replay
            # proves the same result without reissuing credentials.
            stored = job.committed_result or {"job_id": str(job.id), "outcomes": []}
            return {**stored, "activation_tokens": {}}
        raise ImportNotReady("import already committed under a different idempotency key")
    if job.state != "parsed":
        raise ImportNotReady(f"import job is not ready to commit (state={job.state})")
    if job.decision_version != decision_version:
        raise StaleDecisionVersion("decision version is stale; re-review the preview")

    standard = _latest_decision(db, job, tier="standard")
    if standard is None or standard.decision != "approve":
        raise ImportNotReady("import has not been approved for commit")

    high_risk_decision = None
    if job.high_risk_rows > 0:
        high_risk_decision = _latest_decision(db, job, tier="high_risk")
        if high_risk_decision is None or high_risk_decision.decision != "approve":
            raise ImportNotReady("high-risk rows require a separate high-risk approval")
        # Re-verify live, not the state at decision time (plan 11.2 step 9).
        _assert_qualified_high_risk_approver(db, job, high_risk_decision.decided_by)

    rows = db.scalars(
        select(WorkforceImportRow)
        .where(
            WorkforceImportRow.import_job_id == job.id,
            WorkforceImportRow.validation_result == "valid",
        )
        .order_by(WorkforceImportRow.row_number.asc())
    )

    outcomes: list[dict] = []
    activations: dict[str, str] = {}
    for row in rows:
        row_actor = (
            high_risk_decision.decided_by
            if row.risk_level == "high_risk" and high_risk_decision is not None
            else standard.decided_by
        )
        outcome = _COMMIT_ROW_FUNCTIONS[job.import_type](db, row, actor_id=row_actor)
        if outcome is not None:
            outcomes.append({"row_number": outcome.row_number, "outcome": outcome.outcome})
            if outcome.activation_token and row.external_workforce_id:
                activations[row.external_workforce_id] = outcome.activation_token
        db.flush()

    # Only the outcome list is persisted (committed_result backs idempotent replay -
    # plan 11.2 step 13). Activation tokens are one-time, same as every other secret
    # in this build (MFA setup key, bootstrap token): returned to this call only,
    # never stored, never reissued on a replayed commit.
    result = {"job_id": str(job.id), "outcomes": outcomes}
    job.state = "committed"
    job.committed_at = utcnow()
    job.idempotency_key = idempotency_key
    job.committed_result = result
    record_audit(
        db, action="workforce_import.commit", result="success", actor_user_id=actor_id,
        target_type="workforce_import_job", target_id=job.id,
        event_metadata={"import_type": job.import_type, "row_count": len(outcomes)},
    )
    return {**result, "activation_tokens": activations}


def _reverse_user_row(
    db: Session, row: WorkforceImportRow, *, actor_id: uuid.UUID
) -> str | None:
    # committed_entity_id references a user, never hard-deleted - guaranteed to exist.
    target = db.execute(
        select(User).where(User.id == row.committed_entity_id).with_for_update()
    ).scalar_one()

    if row.action in ("create", "reactivate"):
        conflicts = not target.active
    elif row.action == "deactivate":
        conflicts = target.active
    else:  # update
        values = row.parsed_values or {}
        conflicts = any(
            (getattr(target, field).isoformat() if field in ("start_date", "end_date")
             and getattr(target, field) else getattr(target, field)) != value
            for field, value in values.items()
        )
    if conflicts:
        return None

    if row.action in ("create", "reactivate"):
        workforce_service.disable_user(db, target, actor_id=actor_id, reason_code="import_reversal")
        return "deactivated"
    if row.action == "deactivate":
        workforce_service.reactivate_user(
            db, target, actor_id=actor_id, reason_code="import_reversal"
        )
        return "reactivated"
    # update
    snapshot = row.pre_commit_snapshot or {}
    changes = {
        field: (date.fromisoformat(value) if field in ("start_date", "end_date") and value
                else value)
        for field, value in snapshot.items()
    }
    workforce_service.update_user(
        db, target, changes=changes, actor_id=actor_id, reason_code="import_reversal"
    )
    return "restored"


def _reverse_team_membership_row(
    db: Session, row: WorkforceImportRow, *, actor_id: uuid.UUID
) -> str | None:
    membership = db.execute(
        select(TeamMembership).where(TeamMembership.id == row.committed_entity_id)
        .with_for_update()
    ).scalar_one()
    currently_active = membership.membership_status == "active" and membership.effective_to is None
    if row.action == "add":
        if not currently_active:
            return None
        workforce_service.end_team_membership(
            db, membership, ended_by=actor_id, reason_code="import_reversal"
        )
        return "ended"
    # end
    if currently_active:
        return None
    # A real membership's team_id, never hard-deleted - guaranteed to exist.
    team = db.execute(select(Team).where(Team.id == membership.team_id)).scalar_one()
    workforce_service.add_team_membership(db, team, user_id=membership.user_id, added_by=actor_id)
    return "added"


def _reverse_role_assignment_row(
    db: Session, row: WorkforceImportRow, *, actor_id: uuid.UUID
) -> str | None:
    assignment = db.execute(
        select(RoleAssignment).where(RoleAssignment.id == row.committed_entity_id)
        .with_for_update()
    ).scalar_one()
    currently_active = assignment.status == "active" and assignment.effective_to is None
    if row.action == "assign":
        if not currently_active:
            return None
        workforce_service.end_role_assignment(
            db, assignment, ended_by=actor_id, reason_code="import_reversal"
        )
        return "ended"
    # end
    if currently_active:
        return None
    workforce_service.assign_role(
        db, target_user_id=assignment.user_id, role_code=assignment.role_code,
        scope_type=assignment.scope_type, scope_id=assignment.scope_id,
        appointed_by=actor_id, reason_code="import_reversal",
    )
    return "assigned"


def _reverse_reporting_assignment_row(
    db: Session, row: WorkforceImportRow, *, actor_id: uuid.UUID
) -> str | None:
    line = db.execute(
        select(ReportingAssignment).where(ReportingAssignment.id == row.committed_entity_id)
        .with_for_update()
    ).scalar_one()
    if not (line.status == "active" and line.effective_to is None):
        return None
    snapshot = row.pre_commit_snapshot or {}
    prior_supervisor_id = snapshot.get("prior_supervisor_id")
    if prior_supervisor_id:
        # Restoring the prior supervisor naturally supersedes (ends) this line -
        # set_reporting_line always ends whatever active primary line came before.
        workforce_service.set_reporting_line(
            db, subordinate_user_id=line.subordinate_user_id,
            supervisor_user_id=uuid.UUID(prior_supervisor_id),
            assigned_by=actor_id, reason_code="import_reversal",
        )
    else:
        workforce_service.end_reporting_line(
            db, line, ended_by=actor_id, reason_code="import_reversal"
        )
    return "restored"


_REVERSE_ROW_FUNCTIONS = {
    "user": _reverse_user_row,
    "team_membership": _reverse_team_membership_row,
    "role_assignment": _reverse_role_assignment_row,
    "reporting_assignment": _reverse_reporting_assignment_row,
}


def reverse_job(db: Session, job_id: uuid.UUID, *, actor_id: uuid.UUID) -> dict:
    job = db.execute(
        select(WorkforceImportJob).where(WorkforceImportJob.id == job_id).with_for_update()
    ).scalar_one_or_none()
    if job is None:
        raise ImportNotReady("import job not found")
    if job.state != "committed":
        raise ImportNotReady("only a committed import can be reversed")
    if job.reversed_at is not None:
        raise ImportNotReady("import has already been reversed")
    if job.high_risk_rows > 0:
        _assert_qualified_high_risk_approver(db, job, actor_id)

    reversed_rows: list[dict] = []
    skipped_rows: list[dict] = []
    rows = db.scalars(
        select(WorkforceImportRow).where(
            WorkforceImportRow.import_job_id == job.id,
            WorkforceImportRow.committed_entity_id.isnot(None),
            WorkforceImportRow.reversed_at.is_(None),
        )
    )
    now = utcnow()
    for row in rows:
        entity_type = row.committed_entity_type
        if entity_type is None:
            continue
        outcome = _REVERSE_ROW_FUNCTIONS[entity_type](db, row, actor_id=actor_id)
        if outcome is None:
            row.conflict_type = "reversal_conflict"
            skipped_rows.append({"row_number": row.row_number, "reason": "changed_since_commit"})
            continue
        row.reversed_at = now
        reversed_rows.append({"row_number": row.row_number, "outcome": outcome})
        db.flush()

    job.reversed_at = now
    record_audit(
        db, action="workforce_import.reverse", result="success", actor_user_id=actor_id,
        target_type="workforce_import_job", target_id=job.id,
        event_metadata={"reversed": len(reversed_rows), "skipped": len(skipped_rows)},
    )
    return {"job_id": str(job.id), "reversed": reversed_rows, "skipped": skipped_rows}


def cleanup_committed_source(job: WorkforceImportJob) -> None:
    storage.delete(job.generated_storage_key)


def cleanup_expired_jobs(db: Session) -> int:
    now = utcnow()
    expired = db.scalars(select(WorkforceImportJob).where(WorkforceImportJob.expires_at < now))
    count = 0
    for job in expired:
        storage.delete(job.generated_storage_key)
        if job.state == "committed":
            job.expires_at = None
        else:
            db.query(WorkforceImportRow).filter(WorkforceImportRow.import_job_id == job.id).delete()
            job.state = "expired"
        count += 1
    return count
