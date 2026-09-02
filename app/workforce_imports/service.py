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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import PurePosixPath

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.authz import service as authz
from app.authz.capabilities import APPOINT_TEAM_CAPTAIN, ASSIGN_CAMPAIGN_AGENT
from app.campaigns import service as campaign_service
from app.campaigns.service import CampaignAssignmentError
from app.config import get_settings
from app.flags import service as flags
from app.imports import storage, validators
from app.imports.parser import ParseLimitExceeded, parse_file, read_header
from app.models.authz import ReportingAssignment, RoleAssignment
from app.models.base import utcnow
from app.models.campaign import Campaign, CampaignUserAssignment
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
# Matches authz.service's own bulk-query chunk size, kept separate since that
# one is a private module constant - this bounds the target_ids IN (...)
# clause the same way, well under Postgres's ~65k bind-parameter limit even
# at the full 100,000-row import maximum.
_AUTHORITY_CHUNK_SIZE = 1000
IMPORT_TYPES = (
    "users",
    "explicit_deactivations",
    "team_memberships",
    "role_assignments",
    "reporting_assignments",
    "campaign_user_assignments",
)
REQUIRED_COLUMNS = {
    "users": {"action", "external_workforce_id", "login_identifier", "display_name"},
    "explicit_deactivations": {"external_workforce_id", "reason_code"},
    "team_memberships": {"action", "external_workforce_id", "team_code"},
    "role_assignments": {
        "action", "external_workforce_id", "role_code", "scope_type", "reason_code",
    },
    "reporting_assignments": {"external_workforce_id", "supervisor_workforce_id"},
    "campaign_user_assignments": {"action", "external_workforce_id", "campaign_code"},
}
# Required plus optional columns each classifier actually reads. Anything in a
# header beyond this set is rejected at parse time, not silently ignored - an
# unrecognized column (a stray "password" pasted in from a different export,
# for one) has no business sitting in quarantine storage even briefly, and a
# typo'd column name should fail loudly rather than be read as blank forever.
ALLOWED_COLUMNS = {
    "users": REQUIRED_COLUMNS["users"] | {"start_date", "end_date"},
    "explicit_deactivations": REQUIRED_COLUMNS["explicit_deactivations"],
    "team_memberships": REQUIRED_COLUMNS["team_memberships"] | {"reason_code"},
    "role_assignments": REQUIRED_COLUMNS["role_assignments"] | {"scope_code"},
    "reporting_assignments": REQUIRED_COLUMNS["reporting_assignments"] | {"reason_code", "action"},
    "campaign_user_assignments": (
        REQUIRED_COLUMNS["campaign_user_assignments"] | {"team_code", "reason_code"}
    ),
}
_CLASSIFIERS = {
    "users": classify.classify_users_row,
    "explicit_deactivations": classify.classify_deactivation_row,
    "team_memberships": classify.classify_team_membership_row,
    "role_assignments": classify.classify_role_assignment_row,
    "reporting_assignments": classify.classify_reporting_assignment_row,
    "campaign_user_assignments": classify.classify_campaign_assignment_row,
}
# The blanket, coarse "may even open this surface at all" gate the web and JSON
# API layers both use. Every real authorization decision happens per-row (see
# _row_requirement/_bulk_authority_ok); this is only a pre-filter, but it still
# needs to actually include every capability a real import type's rows can
# require - not just
# ROLE_APPOINTMENT_CAPABILITY's values, which would silently exclude a Team
# Captain who holds ASSIGN_CAMPAIGN_AGENT but no workforce-appointment
# capability from ever reaching campaign_user_assignments at all.
UPLOAD_CAPABILITIES = frozenset(ROLE_APPOINTMENT_CAPABILITY.values()) | {ASSIGN_CAMPAIGN_AGENT}


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


class UnresolvedBlockingErrors(WorkforceImportError):
    pass


class WarningsNotAcknowledged(WorkforceImportError):
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
    # Distinct object-level authorization requirements, accumulated as rows are
    # classified so can_access_job never has to re-derive them from every row
    # later. Capped: past _REQUIREMENTS_CAP distinct entries the footprint is
    # marked over_cap instead of stored in full (see _REQUIREMENTS_CAP).
    footprint_requirements: set[tuple] = set()
    footprint_over_cap = False

    try:
        # Read independently of the data-row loop below, not inferred from the
        # first yielded row - a header-only file has zero data rows, so a
        # header derived that way would silently skip every check here and let
        # the file become a "successfully parsed," zero-row job.
        header = set(read_header(path, ext))
        missing = REQUIRED_COLUMNS[job.import_type] - header
        if missing:
            raise ParseLimitExceeded(
                f"file header is missing required column(s): {', '.join(sorted(missing))} "
                "- download the current template"
            )
        unknown = header - ALLOWED_COLUMNS[job.import_type]
        if unknown:
            raise ParseLimitExceeded(
                f"file header has unrecognized column(s): {', '.join(sorted(unknown))} "
                "- download the current template"
            )

        for row in parse_file(path, ext):
            total += 1
            result = classifier(row, db=db, seen_identities=seen_identities)
            import_row = WorkforceImportRow(
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
            batch.append(import_row)
            if not footprint_over_cap:
                requirement = _row_requirement(job, import_row)
                if requirement is not None:
                    footprint_requirements.add(requirement)
                    if len(footprint_requirements) > _REQUIREMENTS_CAP:
                        footprint_over_cap = True
                        footprint_requirements.clear()
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

        if total == 0:
            raise ParseLimitExceeded(
                "file has no data rows - upload a file with at least one row"
            )

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
    job.authorization_footprint = {
        "over_cap": footprint_over_cap,
        "requirements": (
            []
            if footprint_over_cap
            else [_serialize_requirement(req) for req in footprint_requirements]
        ),
    }
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


def _committable_rows(
    db: Session, job: WorkforceImportJob, *, risk_level: str | None = None
) -> list[WorkforceImportRow]:
    conditions = [
        WorkforceImportRow.import_job_id == job.id,
        WorkforceImportRow.validation_result == "valid",
    ]
    if risk_level is not None:
        conditions.append(WorkforceImportRow.risk_level == risk_level)
    return list(db.scalars(select(WorkforceImportRow).where(*conditions)))


def _row_requirement(
    job: WorkforceImportJob, row: WorkforceImportRow
) -> tuple | None:
    """The one authorization fact this row's effect depends on, as an opaque key -
    "may disable this user," "may grant this exact role at this exact scope," and
    "may manage this exact team's roster" are different questions with different
    existing answers elsewhere in this codebase (the manual, one-row-at-a-time
    screens each already enforce their own version of this; bulk import must not
    be a looser path to the same effect - plan 11.2's "a file cannot grant the
    uploader more authority"). Returning a key rather than a bool lets the bulk
    check below collect every row's *distinct* requirement and resolve each one
    once, instead of re-deriving and re-querying it per row. None means the row
    needs no check at all - reserved for the one case where that is actually
    safe: a plain user creation, which matches create_user's own blanket bar
    and names no existing target to protect. ("deny",) means the row can
    never be authorized by a non-uploader, independent of who's asking - an
    unresolvable role/scope/campaign reference, or (just as importantly) an
    unresolved identity/team target. An unresolved target is NOT the same as
    "nothing to check": the row still named a real person or team the
    uploader was trying to act on, so a non-uploader with no relationship to
    that row must not get a free pass on it just because it happens to be
    invalid - a job consisting entirely of unresolvable rows would otherwise
    pass every check trivially, the same fail-open shape the zero-rows fix
    closed for an unparsed job, just reached through invalid rows instead of
    an empty file."""
    values = row.parsed_values or {}
    if job.import_type in ("explicit_deactivations", "reporting_assignments"):
        if row.normalized_identity is None:
            return ("deny",)
        return ("user", row.normalized_identity)
    if job.import_type == "users":
        if row.action == "create":
            # A *valid* create names a brand-new identity with no role and no
            # scope - the blanket appointment capability (already required at
            # the HTTP layer) is the only bar, matching create_user's own
            # manual screen, so it correctly contributes no scope constraint.
            # An INVALID create (malformed email, a duplicate-in-file) has
            # failed validation and will never commit; letting it also return
            # None would let a job of nothing but invalid creates be seen and
            # rejected by any capability holder, the same fail-open the
            # unresolved-target cases already close - so it denies instead.
            return None if row.validation_result == "valid" else ("deny",)
        if row.action in ("update", "reactivate"):
            if row.normalized_identity is None:
                return ("deny",)
            return ("user", row.normalized_identity)
        return ("deny",)
    if job.import_type == "team_memberships":
        team_id = values.get("team_id")
        if team_id is None:
            return ("deny",)
        return ("scope", APPOINT_TEAM_CAPTAIN, "team", uuid.UUID(team_id))
    if job.import_type == "role_assignments":
        role_code = values.get("role_code")
        scope_type = values.get("scope_type")
        scope_id = uuid.UUID(values["scope_id"]) if values.get("scope_id") else None
        capability = ROLE_APPOINTMENT_CAPABILITY.get(role_code) if role_code else None
        if capability is None or scope_type is None:
            return ("deny",)
        return ("scope", capability, scope_type, scope_id)
    if job.import_type == "campaign_user_assignments":
        campaign_id = values.get("campaign_id")
        if campaign_id is None:
            return ("deny",)
        return ("campaign", uuid.UUID(campaign_id))
    return ("deny",)


# A pure safety valve on the stored footprint's size. Above this many DISTINCT
# requirements a job is marked over_cap and its requirements are re-derived from
# rows when its access is resolved (still resolved - never skipped or hidden),
# rather than storing an unbounded JSONB blob. Set well above any realistic
# distinct count: a normal import references a handful of teams/campaigns, and
# even a bulk user operation is bounded by the actual workforce size, so the
# reviewer's 1,001-user example stays comfortably in the cheap stored path -
# only a genuinely pathological import (tens of thousands of distinct real
# targets) ever trips this.
_REQUIREMENTS_CAP = 10_000


def _serialize_requirement(req: tuple) -> list:
    """A requirement tuple as JSON-safe data (UUIDs to strings, None kept)."""
    return [str(part) if isinstance(part, uuid.UUID) else part for part in req]


def _deserialize_requirement(entry: list) -> tuple:
    """Inverse of _serialize_requirement - reconstructs the tuple can_access_job
    feeds to _authority_over_requirements from stored footprint data."""
    kind = entry[0]
    if kind == "deny":
        return ("deny",)
    if kind == "user":
        return ("user", uuid.UUID(entry[1]))
    if kind == "scope":
        scope_id = uuid.UUID(entry[3]) if entry[3] is not None else None
        return ("scope", entry[1], entry[2], scope_id)
    if kind == "campaign":
        return ("campaign", uuid.UUID(entry[1]))
    raise ValueError(f"unrecognized stored requirement: {entry!r}")


@dataclass
class _SatisfiedAuthority:
    """Which of a set of distinct requirement components an actor is authorized
    for - the resolved lookup a per-job (or per-list) decision reads from."""

    targets: set[uuid.UUID]
    scopes: set[tuple[str, str, uuid.UUID | None]]
    campaigns: set[uuid.UUID]


def _satisfied_authority(
    db: Session,
    actor_id: uuid.UUID,
    *,
    target_ids: set[uuid.UUID],
    scope_tuples: set[tuple[str, str, uuid.UUID | None]],
    campaign_ids: set[uuid.UUID],
) -> _SatisfiedAuthority:
    """Resolve, in a query count bounded by a small fixed number of round trips
    (NOT by how many distinct values are passed), exactly which of the given
    target users / (capability, scope_type, scope_id) tuples / campaigns the
    actor is authorized for. This is the shared bulk core: one job's access
    check passes its own distinct requirements; the list view passes the UNION
    across every candidate job and resolves them all at once, so 200 jobs cost
    one resolution, not 200. Every decision still goes through the same trusted
    authz primitives the manual one-row screens rely on."""
    # Bulk fetch every distinct target's own active roles, chunked so a single
    # IN (...) clause never approaches Postgres's ~65k bind-parameter limit
    # even at the full 100,000-row import size.
    now = utcnow()
    target_roles_by_id: dict[uuid.UUID, list[RoleAssignment]] = {tid: [] for tid in target_ids}
    for chunk in itertools.batched(target_ids, _AUTHORITY_CHUNK_SIZE):
        for assignment in db.scalars(
            select(RoleAssignment).where(
                RoleAssignment.user_id.in_(chunk),
                RoleAssignment.status == "active",
                RoleAssignment.effective_from <= now,
                or_(RoleAssignment.effective_to.is_(None), RoleAssignment.effective_to > now),
            )
        ):
            target_roles_by_id[assignment.user_id].append(assignment)

    # The scopes actually needing resolution are the direct scope requirements
    # plus the scopes each target's own appointment roles occupy (a target is
    # reachable if the actor covers any one of them).
    all_scope_tuples = set(scope_tuples)
    for roles in target_roles_by_id.values():
        for ra in roles:
            capability = ROLE_APPOINTMENT_CAPABILITY.get(ra.role_code)
            if capability is not None:
                all_scope_tuples.add((capability, ra.scope_type, ra.scope_id))

    # Grouped by capability, since scope_capabilities_matched answers "which of
    # these (scope_type, scope_id) pairs does the actor cover for ONE
    # capability" in a bounded number of round trips regardless of how many
    # distinct pairs are asked.
    scope_ok: dict[tuple[str, str, uuid.UUID | None], bool] = {}
    tuples_by_capability: dict[str, set[tuple[str, uuid.UUID | None]]] = {}
    for capability, scope_type, scope_id in all_scope_tuples:
        tuples_by_capability.setdefault(capability, set()).add((scope_type, scope_id))
    for capability, scope_requests in tuples_by_capability.items():
        matched = authz.scope_capabilities_matched(db, actor_id, capability, scope_requests)
        for scope_type, scope_id in scope_requests:
            scope_ok[(capability, scope_type, scope_id)] = (scope_type, scope_id) in matched

    matched_campaigns = authz.campaign_ids_with_capability(
        db, actor_id, ASSIGN_CAMPAIGN_AGENT, campaign_ids
    )

    no_role_fallback_ok = (
        any(
            authz.has_assigned_capability(db, actor_id, capability)
            for capability in ROLE_APPOINTMENT_CAPABILITY.values()
        )
        if any(not target_roles_by_id[tid] for tid in target_ids)
        else False
    )

    def _target_ok(target_id: uuid.UUID) -> bool:
        roles = target_roles_by_id.get(target_id, [])
        if not roles:
            return no_role_fallback_ok
        return any(
            scope_ok.get(
                (ROLE_APPOINTMENT_CAPABILITY[ra.role_code], ra.scope_type, ra.scope_id), False
            )
            for ra in roles
            if ra.role_code in ROLE_APPOINTMENT_CAPABILITY
        )

    return _SatisfiedAuthority(
        targets={tid for tid in target_ids if _target_ok(tid)},
        scopes={tup for tup in scope_tuples if scope_ok.get(tup, False)},
        campaigns=matched_campaigns & campaign_ids,
    )


def _distinct_requirement_sets(
    requirements: Iterable[tuple | None],
) -> tuple[set[uuid.UUID], set[tuple[str, str, uuid.UUID | None]], set[uuid.UUID]]:
    """The distinct target ids / scope tuples / campaign ids a requirement list
    references, for feeding _satisfied_authority."""
    target_ids = {req[1] for req in requirements if req is not None and req[0] == "user"}
    scope_tuples: set[tuple[str, str, uuid.UUID | None]] = {
        req[1:] for req in requirements if req is not None and req[0] == "scope"
    }
    campaign_ids = {req[1] for req in requirements if req is not None and req[0] == "campaign"}
    return target_ids, scope_tuples, campaign_ids


def _requirements_satisfied(
    requirements: Iterable[tuple | None], satisfied: _SatisfiedAuthority
) -> bool:
    """Whether every requirement is met by an already-resolved authority set -
    a deny anywhere fails outright; every user/scope/campaign requirement must
    appear in the satisfied lookup; a None requirement (a valid user creation)
    contributes nothing."""
    for req in requirements:
        if req is None:
            continue
        kind = req[0]
        if kind == "deny":
            return False
        if kind == "user" and req[1] not in satisfied.targets:
            return False
        if kind == "scope" and req[1:] not in satisfied.scopes:
            return False
        if kind == "campaign" and req[1] not in satisfied.campaigns:
            return False
    return True


def _authority_over_requirements(
    db: Session, actor_id: uuid.UUID, requirements: Sequence[tuple | None]
) -> bool:
    """Whether `actor_id` is authorized for every requirement in the list - the
    single-set resolution used by the live per-row check (_assert_rows_
    authorized) and the single-job access check (can_access_job). The list view
    resolves the UNION across candidates instead (see visible_jobs), so this is
    not called once per candidate there."""
    if any(req == ("deny",) for req in requirements):
        return False
    target_ids, scope_tuples, campaign_ids = _distinct_requirement_sets(requirements)
    satisfied = _satisfied_authority(
        db, actor_id, target_ids=target_ids, scope_tuples=scope_tuples, campaign_ids=campaign_ids
    )
    return _requirements_satisfied(requirements, satisfied)


def _assert_rows_authorized(
    db: Session, job: WorkforceImportJob, actor_id: uuid.UUID, *, risk_level: str
) -> None:
    # The approve/commit/reverse path always resolves live from the job's own
    # committable rows (valid-only, by risk level), never from the stored
    # access footprint - the footprint is over ALL rows for the access gate,
    # a deliberately different (and coarser) set than what may actually be
    # committed. This is one job the actor is already acting on, so loading
    # its committable rows here is bounded and appropriate.
    rows = _committable_rows(db, job, risk_level=risk_level)
    requirements = [_row_requirement(job, row) for row in rows]
    if not _authority_over_requirements(db, actor_id, requirements):
        raise InsufficientApprovalAuthority(
            "not authorized for the effect of one or more rows in this import"
        )


def _job_access_requirements(
    db: Session, job: WorkforceImportJob
) -> list[tuple | None] | None:
    """The distinct requirement list to resolve a non-uploader's access to this
    job against, or None if the job is not accessible to a non-uploader at all
    (still parsing, failed to parse, or successfully parsed but empty). Cheap
    from the stored footprint in the normal case; a job whose footprint is
    absent (parsed before the column existed) or was too large to store in full
    (over_cap) falls back to deriving requirements from its rows - correct, and
    rare, but no longer a *skip*: a large job must still be reachable by a
    qualified approver, not hidden from the only discovery screen."""
    footprint = job.authorization_footprint
    if footprint is not None and not footprint.get("over_cap"):
        return [_deserialize_requirement(entry) for entry in footprint.get("requirements", [])]
    if job.state != "parsed":
        return None
    rows = list(
        db.scalars(select(WorkforceImportRow).where(WorkforceImportRow.import_job_id == job.id))
    )
    if not rows:
        return None
    return [_row_requirement(job, row) for row in rows]


def can_access_job(db: Session, actor_id: uuid.UUID, job: WorkforceImportJob) -> bool:
    """Whether this actor has any legitimate reach into this specific job at all -
    not just "holds some import-related capability somewhere." Without this, any
    holder of any import capability could open, decide, commit, or reverse *any*
    job regardless of scope, including committing someone else's already-approved
    job to collect its one-time activation tokens. The uploader always can;
    anyone else needs the same per-row authority acting on the job would require,
    over *every* row it contains - not only the currently-valid ones, since an
    invalid or warning row can still name a real team, role, or campaign, and
    merely viewing that should require the same authority acting on it would.

    Resolved against the job's stored authorization_footprint (computed once at
    parse time), or - for a job whose footprint is absent (parsed before the
    column) or over_cap (too many distinct requirements to store in full) - from
    its rows. A job is never hidden from a qualified approver just because it is
    large: over_cap resolves the same access, only more expensively for that one
    job. A NULL footprint on a job that never finished parsing is uploader-only,
    since there is nothing yet to scope-check and a fail-open default would let
    any capability holder read a job the parser never finished."""
    if actor_id == job.uploader_id:
        return True
    requirements = _job_access_requirements(db, job)
    if requirements is None:
        return False
    return _authority_over_requirements(db, actor_id, requirements)


def _assert_job_accessible(db: Session, job: WorkforceImportJob, actor_id: uuid.UUID) -> None:
    if not can_access_job(db, actor_id, job):
        raise InsufficientApprovalAuthority("not authorized for this import job")


def visible_jobs(
    db: Session, actor_id: uuid.UUID, *, limit: int = 50, candidate_limit: int = 200
) -> list[WorkforceImportJob]:
    """The list view's job set, scoped to what this actor can actually reach -
    the same per-job authority commit/decide/reverse require, not the blanket
    "holds some import capability" gate. Resolves authorization for the WHOLE
    candidate batch in one bulk pass rather than once per job: it gathers every
    non-uploader candidate's distinct requirements (from each job's stored
    footprint - no row load in the normal case), unions them, resolves the
    actor's authority over that union a single time, then decides each job from
    the resolved lookup. So a 200-job list costs one authorization resolution,
    not 200, and no job is skipped merely for being large - a qualified approver
    still finds every import they may act on."""
    candidates = list(
        db.scalars(
            select(WorkforceImportJob)
            .order_by(WorkforceImportJob.created_at.desc())
            .limit(candidate_limit)
        )
    )

    # Gather each non-uploader candidate's requirements once, and union their
    # distinct components for a single bulk resolution.
    requirements_by_job: dict[uuid.UUID, list[tuple | None]] = {}
    union_targets: set[uuid.UUID] = set()
    union_scopes: set[tuple[str, str, uuid.UUID | None]] = set()
    union_campaigns: set[uuid.UUID] = set()
    for job in candidates:
        if job.uploader_id == actor_id:
            continue
        requirements = _job_access_requirements(db, job)
        if requirements is None:
            continue
        requirements_by_job[job.id] = requirements
        targets, scopes, campaigns = _distinct_requirement_sets(requirements)
        union_targets |= targets
        union_scopes |= scopes
        union_campaigns |= campaigns

    satisfied = _satisfied_authority(
        db, actor_id, target_ids=union_targets, scope_tuples=union_scopes,
        campaign_ids=union_campaigns,
    )

    visible: list[WorkforceImportJob] = []
    for job in candidates:
        if job.uploader_id == actor_id:
            accessible = True
        else:
            requirements = requirements_by_job.get(job.id)
            accessible = requirements is not None and _requirements_satisfied(
                requirements, satisfied
            )
        if accessible:
            visible.append(job)
            if len(visible) >= limit:
                break
    return visible


def _assert_qualified_high_risk_approver(
    db: Session, job: WorkforceImportJob, approver_id: uuid.UUID
) -> None:
    """Separation of duties plus per-row live authority - reused by both the
    high_risk decision and, again, by commit and reverse (which may run well
    after the decision was recorded, against whatever authority exists then)."""
    if approver_id == job.uploader_id:
        raise SelfApproval("the uploader cannot also approve a high-risk import")
    _assert_rows_authorized(db, job, approver_id, risk_level="high_risk")


def record_decision(
    db: Session,
    job_id: uuid.UUID,
    *,
    decided_by: uuid.UUID,
    decision: str,
    decision_tier: str,
    note: str | None,
    acknowledge_warnings: bool = False,
) -> WorkforceImportDecision:
    # Locked here, not trusted from an already-loaded object a caller passed in -
    # two concurrent decision calls on the same job must not both read the same
    # job.decision_version and both write a row claiming it (the same shape of
    # race staffing-capacity and leasing already close elsewhere in this build).
    job = db.execute(
        select(WorkforceImportJob).where(WorkforceImportJob.id == job_id).with_for_update()
    ).scalar_one_or_none()
    if job is None:
        raise ImportNotReady("import job not found")
    _assert_job_accessible(db, job, decided_by)
    if job.state != "parsed":
        # Row counts (invalid_rows, warning_rows, high_risk_rows) are only
        # meaningful once parsing has actually finished - deciding on a job
        # that is still quarantined/parsing (or that failed to parse) would
        # check the blocking-errors/warnings gates below against numbers that
        # are still all zero by default, regardless of what the file actually
        # contains once parsing completes.
        raise ImportNotReady(f"import job is not ready to decide on (state={job.state})")
    if decision not in ("approve", "reject", "cancel"):
        raise ValueError("decision must be approve, reject, or cancel")
    if decision_tier not in ("standard", "high_risk"):
        raise ValueError("decision_tier must be standard or high_risk")
    if decision_tier == "high_risk":
        if job.high_risk_rows == 0:
            raise ValueError("this import has no high-risk rows to approve")
        if decision == "approve":
            _assert_qualified_high_risk_approver(db, job, decided_by)
    elif decision == "approve":
        # Routine rows are not exempt from "a file cannot grant more authority" -
        # they just don't also need a second, non-uploader approver the way
        # high-risk rows do.
        _assert_rows_authorized(db, job, decided_by, risk_level="routine")

    if decision == "approve":
        # "Uploader resolves all blocking errors and explicitly accepts warnings"
        # (plan 11.2 step 7) was previously only UI copy - nothing backend-side
        # actually stopped an approval, and commit_job silently excludes invalid
        # rows from its own row query, so approving a file with unresolved errors
        # would quietly commit only the rows that happened to be valid.
        if job.invalid_rows > 0:
            raise UnresolvedBlockingErrors(
                f"{job.invalid_rows} row(s) have blocking errors - fix and re-upload "
                "before this import can be approved"
            )
        if job.warning_rows > 0 and not acknowledge_warnings:
            raise WarningsNotAcknowledged(
                f"{job.warning_rows} row(s) have warnings that must be explicitly "
                "acknowledged before approval"
            )

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
        # Re-validated at commit time, not trusted from preview: the team could
        # have been deactivated since parse.
        if team.status != "active":
            row.conflict_type = "unknown_team"
            return _RowOutcome(row.row_number, "skipped_conflict")
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
    # Re-validated at commit time, not trusted from preview: the supervisor
    # could have been deactivated since parse.
    supervisor_active = db.scalar(select(User.active).where(User.id == supervisor_id))
    if not supervisor_active:
        row.conflict_type = "unknown_supervisor"
        return _RowOutcome(row.row_number, "skipped_conflict")
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


def _commit_campaign_assignment_row(
    db: Session, row: WorkforceImportRow, *, actor_id: uuid.UUID
) -> _RowOutcome | None:
    if row.normalized_identity is None:
        return None
    values = row.parsed_values or {}
    # A real committed campaign, never hard-deleted - guaranteed to exist.
    campaign = db.execute(
        select(Campaign).where(Campaign.id == uuid.UUID(values["campaign_id"]))
    ).scalar_one()
    team_id = uuid.UUID(values["team_id"]) if values.get("team_id") else None

    if row.action == "assign":
        try:
            assignment = campaign_service.assign_agent_to_campaign(
                db, campaign, agent_id=row.normalized_identity, team_id=team_id,
                actor_id=actor_id,
            )
        except CampaignAssignmentError:
            row.conflict_type = "already_assigned"
            return _RowOutcome(row.row_number, "skipped_conflict")
        row.committed_entity_type = "campaign_user_assignment"
        row.committed_entity_id = assignment.id
        return _RowOutcome(row.row_number, "assigned")

    # end
    current = db.execute(
        select(CampaignUserAssignment)
        .where(
            CampaignUserAssignment.user_id == row.normalized_identity,
            CampaignUserAssignment.campaign_id == campaign.id,
            CampaignUserAssignment.assignment_type == "primary",
            CampaignUserAssignment.status == "active",
            CampaignUserAssignment.effective_to.is_(None),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if current is None:
        row.conflict_type = "not_assigned"
        return _RowOutcome(row.row_number, "skipped_conflict")
    campaign_service.end_user_assignment(
        db, current, actor_id=actor_id, reason_code=values.get("reason_code")
    )
    row.committed_entity_type = "campaign_user_assignment"
    row.committed_entity_id = current.id
    return _RowOutcome(row.row_number, "ended")


_COMMIT_ROW_FUNCTIONS = {
    "users": _commit_users_row,
    "explicit_deactivations": _commit_deactivation_row,
    "team_memberships": _commit_team_membership_row,
    "role_assignments": _commit_role_assignment_row,
    "reporting_assignments": _commit_reporting_assignment_row,
    "campaign_user_assignments": _commit_campaign_assignment_row,
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
    # Checked against the actual caller here, not only against whoever recorded
    # the standard decision below - without this, any holder of any import
    # capability could commit someone else's already-approved job and collect
    # its one-time activation tokens, regardless of their own scope.
    _assert_job_accessible(db, job, actor_id)

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
    if job.invalid_rows > 0:
        # Re-verified, not just trusted from decision time - defense in depth
        # matching every other "commit re-checks live state" rule in this file.
        raise UnresolvedBlockingErrors(
            f"{job.invalid_rows} row(s) still have blocking errors"
        )

    standard = _latest_decision(db, job, tier="standard")
    if standard is None or standard.decision != "approve":
        raise ImportNotReady("import has not been approved for commit")
    # Re-verify live, not the state at decision time - same principle as the
    # high-risk re-check just below.
    _assert_rows_authorized(db, job, standard.decided_by, risk_level="routine")

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
            if row.committed_entity_id is not None:
                # The entity's own audit event (workforce.user.disable, etc.) has no
                # idea it was called from inside a bulk import - this is the direct
                # link from the affected record back to the import job and the
                # approver whose authority actually caused it, promised by plan
                # 11.2's "audit trace from every affected record to import,
                # uploader, and approver" and previously only reconstructable via
                # the import tables, not from the audit stream itself.
                record_audit(
                    db, action="workforce_import.row_commit", result="success",
                    actor_user_id=row_actor, target_type=row.committed_entity_type,
                    target_id=row.committed_entity_id,
                    event_metadata={
                        "import_job_id": str(job.id), "import_type": job.import_type,
                        "row_number": row.row_number, "outcome": outcome.outcome,
                    },
                )
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
    if row.action == "add":
        currently_active = (
            membership.membership_status == "active" and membership.effective_to is None
        )
        if not currently_active:
            return None
        workforce_service.end_team_membership(
            db, membership, ended_by=actor_id, reason_code="import_reversal"
        )
        return "ended"
    # end: this specific (now-ended) membership row can never become active
    # again on its own - it isn't proof nothing else has since re-added the
    # user. Check live state for the (team, user) pair instead, or reversal
    # would blindly re-add someone who was already legitimately re-added
    # through some other action since.
    still_a_member = db.scalar(
        select(TeamMembership.id).where(
            TeamMembership.team_id == membership.team_id,
            TeamMembership.user_id == membership.user_id,
            TeamMembership.membership_status == "active",
            TeamMembership.effective_to.is_(None),
        )
    )
    if still_a_member is not None:
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
    if row.action == "assign":
        currently_active = assignment.status == "active" and assignment.effective_to is None
        if not currently_active:
            return None
        workforce_service.end_role_assignment(
            db, assignment, ended_by=actor_id, reason_code="import_reversal"
        )
        return "ended"
    # end: this specific (now-ended) assignment can never become active again
    # on its own - it isn't proof nothing has since re-granted the same role
    # at the same scope. Check live state for (user, role, scope) instead, or
    # reversal would call assign_role regardless, which ends any current grant
    # (assign_role's own supersede rule) and silently clobbers a legitimate
    # newer one with a reversal-created replacement.
    still_active_elsewhere = db.scalar(
        select(RoleAssignment.id).where(
            RoleAssignment.user_id == assignment.user_id,
            RoleAssignment.role_code == assignment.role_code,
            RoleAssignment.scope_type == assignment.scope_type,
            RoleAssignment.scope_id == assignment.scope_id,
            RoleAssignment.status == "active",
            RoleAssignment.effective_to.is_(None),
        )
    )
    if still_active_elsewhere is not None:
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


def _reverse_campaign_assignment_row(
    db: Session, row: WorkforceImportRow, *, actor_id: uuid.UUID
) -> str | None:
    assignment = db.execute(
        select(CampaignUserAssignment).where(CampaignUserAssignment.id == row.committed_entity_id)
        .with_for_update()
    ).scalar_one()
    currently_active = assignment.status == "active" and assignment.effective_to is None
    if row.action == "assign":
        if not currently_active:
            return None
        campaign_service.end_user_assignment(db, assignment, actor_id=actor_id)
        return "ended"
    # end
    if currently_active:
        return None
    values = row.parsed_values or {}
    campaign = db.execute(
        select(Campaign).where(Campaign.id == assignment.campaign_id)
    ).scalar_one()
    team_id = uuid.UUID(values["team_id"]) if values.get("team_id") else None
    try:
        campaign_service.assign_agent_to_campaign(
            db, campaign, agent_id=assignment.user_id, team_id=team_id, actor_id=actor_id,
        )
    except CampaignAssignmentError:
        return None
    return "assigned"


_REVERSE_ROW_FUNCTIONS = {
    "user": _reverse_user_row,
    "team_membership": _reverse_team_membership_row,
    "role_assignment": _reverse_role_assignment_row,
    "reporting_assignment": _reverse_reporting_assignment_row,
    "campaign_user_assignment": _reverse_campaign_assignment_row,
}


def reverse_job(db: Session, job_id: uuid.UUID, *, actor_id: uuid.UUID) -> dict:
    job = db.execute(
        select(WorkforceImportJob).where(WorkforceImportJob.id == job_id).with_for_update()
    ).scalar_one_or_none()
    if job is None:
        raise ImportNotReady("import job not found")
    _assert_job_accessible(db, job, actor_id)
    if job.state != "committed":
        raise ImportNotReady("only a committed import can be reversed")
    if job.reversed_at is not None:
        raise ImportNotReady("import has already been reversed")
    if job.high_risk_rows > 0:
        _assert_qualified_high_risk_approver(db, job, actor_id)
    _assert_rows_authorized(db, job, actor_id, risk_level="routine")

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
        if row.committed_entity_id is not None:
            record_audit(
                db, action="workforce_import.row_reverse", result="success",
                actor_user_id=actor_id, target_type=entity_type,
                target_id=row.committed_entity_id,
                event_metadata={
                    "import_job_id": str(job.id), "import_type": job.import_type,
                    "row_number": row.row_number, "outcome": outcome,
                },
            )
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
