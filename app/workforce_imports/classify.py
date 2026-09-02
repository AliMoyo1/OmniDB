"""Per-row classification for the `users` and `explicit_deactivations` import types.

Classification is advisory at parse time, same principle as the campaign
importer (app/imports/classify.py): commit_job in service.py re-resolves
identity and re-checks current state before writing anything, so a row
classified against a stale snapshot never silently applies.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.service import normalize_email
from app.imports.parser import ParsedRow, sanitize_text
from app.models.authz import ReportingAssignment, RoleAssignment
from app.models.identity import Team, TeamMembership, User
from app.workforce.service import ROLE_APPOINTMENT_CAPABILITY

_MAX_WORKFORCE_ID_LEN = 150
_MAX_DISPLAY_NAME_LEN = 200
_MAX_REASON_CODE_LEN = 50
_USERS_ACTIONS = {"create", "update", "reactivate"}
_TEAM_MEMBERSHIP_ACTIONS = {"add", "end"}
_ROLE_ASSIGNMENT_ACTIONS = {"assign", "end"}
_SCOPE_TYPES = {"installation", "organization", "team"}


@dataclass
class WorkforceRowClassification:
    row_number: int
    action: str | None
    external_workforce_id: str | None
    normalized_identity: uuid.UUID | None
    parsed_values: dict | None
    validation_result: str  # "valid" | "warning" | "invalid"
    validation_detail: str | None
    conflict_type: str | None
    risk_level: str  # "routine" | "high_risk"


def _clean(value: str | None) -> str:
    return sanitize_text((value or "").strip())


def _valid_workforce_id(value: str) -> bool:
    return bool(value) and len(value) <= _MAX_WORKFORCE_ID_LEN and not any(
        character.isspace() for character in value
    )


def _valid_email(value: str) -> bool:
    if not value or len(value) > 320 or any(character.isspace() for character in value):
        return False
    local_part, separator, domain = value.partition("@")
    return bool(separator and local_part and domain and "." in domain)


def _parse_date(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


def _lookup_user_id(db: Session, workforce_id: str) -> uuid.UUID | None:
    return db.scalar(select(User.id).where(User.workforce_id == workforce_id))


def _lookup_user_active(db: Session, user_id: uuid.UUID) -> bool:
    return bool(db.scalar(select(User.active).where(User.id == user_id)))


def _valid_reason_code(value: str) -> bool:
    return bool(value) and len(value) <= _MAX_REASON_CODE_LEN and bool(
        re.fullmatch(r"[a-z0-9_]+", value)
    )


def _lookup_team(db: Session, team_code: str) -> Team | None:
    return db.scalar(
        select(Team).where(Team.external_code == team_code, Team.status == "active")
    )


def _invalid(row_number: int, *, action: str | None, external_workforce_id: str | None,
             detail: str, conflict_type: str | None = None) -> WorkforceRowClassification:
    return WorkforceRowClassification(
        row_number=row_number, action=action, external_workforce_id=external_workforce_id or None,
        normalized_identity=None, parsed_values=None, validation_result="invalid",
        validation_detail=detail, conflict_type=conflict_type, risk_level="routine",
    )


def classify_users_row(
    row: ParsedRow, *, db: Session, seen_identities: set[str]
) -> WorkforceRowClassification:
    action = _clean(row.values.get("action")).lower()
    external_workforce_id = _clean(row.values.get("external_workforce_id"))

    if not _valid_workforce_id(external_workforce_id):
        return _invalid(
            row.row_number, action=action or None, external_workforce_id=None,
            detail="external_workforce_id is required and must not contain whitespace",
        )
    if action not in _USERS_ACTIONS:
        detail = (
            "action must be create, update, or reactivate - use the deactivations "
            "template for deactivate"
            if action == "deactivate"
            else "action must be create, update, or reactivate"
        )
        return _invalid(
            row.row_number, action=action or None, external_workforce_id=external_workforce_id,
            detail=detail,
        )
    if external_workforce_id in seen_identities:
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="external_workforce_id repeated within this file",
            conflict_type="duplicate_in_file",
        )
    seen_identities.add(external_workforce_id)

    existing_user_id = _lookup_user_id(db, external_workforce_id)

    if action == "create":
        if existing_user_id is not None:
            return _invalid(
                row.row_number, action=action, external_workforce_id=external_workforce_id,
                detail="a user with this external_workforce_id already exists - use update",
                conflict_type="already_exists",
            )
        login_identifier = normalize_email(_clean(row.values.get("login_identifier")))
        display_name = _clean(row.values.get("display_name"))
        if not _valid_email(login_identifier):
            return _invalid(
                row.row_number, action=action, external_workforce_id=external_workforce_id,
                detail="login_identifier must be a valid email address",
            )
        if not display_name or len(display_name) > _MAX_DISPLAY_NAME_LEN:
            return _invalid(
                row.row_number, action=action, external_workforce_id=external_workforce_id,
                detail="display_name is required (max 200 characters)",
            )
        try:
            start_date = _parse_date(_clean(row.values.get("start_date")))
            end_date = _parse_date(_clean(row.values.get("end_date")))
        except ValueError:
            return _invalid(
                row.row_number, action=action, external_workforce_id=external_workforce_id,
                detail="start_date/end_date must be YYYY-MM-DD",
            )
        parsed_values = {
            "login_identifier": login_identifier,
            "display_name": display_name,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        }
        return WorkforceRowClassification(
            row_number=row.row_number, action=action, external_workforce_id=external_workforce_id,
            normalized_identity=None, parsed_values=parsed_values, validation_result="valid",
            validation_detail=None, conflict_type=None, risk_level="routine",
        )

    # update / reactivate both require an existing identity.
    if existing_user_id is None:
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="no existing user matches this external_workforce_id",
            conflict_type="unknown_identity",
        )

    if action == "reactivate":
        already_active = db.scalar(select(User.active).where(User.id == existing_user_id))
        if already_active:
            return WorkforceRowClassification(
                row_number=row.row_number, action=action,
                external_workforce_id=external_workforce_id, normalized_identity=existing_user_id,
                parsed_values={}, validation_result="warning",
                validation_detail="user is already active", conflict_type="already_active",
                risk_level="routine",
            )
        return WorkforceRowClassification(
            row_number=row.row_number, action=action, external_workforce_id=external_workforce_id,
            normalized_identity=existing_user_id, parsed_values={}, validation_result="valid",
            validation_detail=None, conflict_type=None, risk_level="routine",
        )

    # update: at least one field must actually change.
    display_name = _clean(row.values.get("display_name"))
    try:
        start_date = _parse_date(_clean(row.values.get("start_date")))
        end_date = _parse_date(_clean(row.values.get("end_date")))
    except ValueError:
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="start_date/end_date must be YYYY-MM-DD",
        )
    update_values: dict[str, str] = {}
    if display_name:
        if len(display_name) > _MAX_DISPLAY_NAME_LEN:
            return _invalid(
                row.row_number, action=action, external_workforce_id=external_workforce_id,
                detail="display_name must be at most 200 characters",
            )
        update_values["display_name"] = display_name
    if start_date:
        update_values["start_date"] = start_date.isoformat()
    if end_date:
        update_values["end_date"] = end_date.isoformat()
    if not update_values:
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="update row has no display_name, start_date, or end_date to change",
        )
    return WorkforceRowClassification(
        row_number=row.row_number, action=action, external_workforce_id=external_workforce_id,
        normalized_identity=existing_user_id, parsed_values=update_values,
        validation_result="valid", validation_detail=None, conflict_type=None,
        risk_level="routine",
    )


def classify_deactivation_row(
    row: ParsedRow, *, db: Session, seen_identities: set[str]
) -> WorkforceRowClassification:
    external_workforce_id = _clean(row.values.get("external_workforce_id"))
    reason_code = _clean(row.values.get("reason_code"))

    if not _valid_workforce_id(external_workforce_id):
        return _invalid(
            row.row_number, action="deactivate", external_workforce_id=None,
            detail="external_workforce_id is required and must not contain whitespace",
        )
    if external_workforce_id in seen_identities:
        return _invalid(
            row.row_number, action="deactivate", external_workforce_id=external_workforce_id,
            detail="external_workforce_id repeated within this file",
            conflict_type="duplicate_in_file",
        )
    seen_identities.add(external_workforce_id)

    if not _valid_reason_code(reason_code):
        return _invalid(
            row.row_number, action="deactivate", external_workforce_id=external_workforce_id,
            detail="reason_code is required (lowercase letters, digits, underscore only)",
        )

    user_id = _lookup_user_id(db, external_workforce_id)
    if user_id is None:
        return _invalid(
            row.row_number, action="deactivate", external_workforce_id=external_workforce_id,
            detail="no existing user matches this external_workforce_id",
            conflict_type="unknown_identity",
        )

    currently_active = db.scalar(select(User.active).where(User.id == user_id))
    if not currently_active:
        return WorkforceRowClassification(
            row_number=row.row_number, action="deactivate",
            external_workforce_id=external_workforce_id, normalized_identity=user_id,
            parsed_values={"reason_code": reason_code}, validation_result="warning",
            validation_detail="user is already inactive", conflict_type="already_inactive",
            risk_level="high_risk",
        )
    return WorkforceRowClassification(
        row_number=row.row_number, action="deactivate", external_workforce_id=external_workforce_id,
        normalized_identity=user_id, parsed_values={"reason_code": reason_code},
        validation_result="valid", validation_detail=None, conflict_type=None,
        risk_level="high_risk",
    )


def classify_team_membership_row(
    row: ParsedRow, *, db: Session, seen_identities: set[str]
) -> WorkforceRowClassification:
    action = _clean(row.values.get("action")).lower()
    external_workforce_id = _clean(row.values.get("external_workforce_id"))
    team_code = _clean(row.values.get("team_code"))
    reason_code = _clean(row.values.get("reason_code")) or None

    if not _valid_workforce_id(external_workforce_id):
        return _invalid(
            row.row_number, action=action or None, external_workforce_id=None,
            detail="external_workforce_id is required and must not contain whitespace",
        )
    if action not in _TEAM_MEMBERSHIP_ACTIONS:
        return _invalid(
            row.row_number, action=action or None, external_workforce_id=external_workforce_id,
            detail="action must be add or end",
        )
    if not team_code:
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="team_code is required",
        )
    # A person can legitimately belong to more than one team, so duplicate detection
    # keys on the (identity, team) pair, not identity alone.
    dedup_key = f"{external_workforce_id}:{team_code}"
    if dedup_key in seen_identities:
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="this external_workforce_id/team_code pair repeats within this file",
            conflict_type="duplicate_in_file",
        )
    seen_identities.add(dedup_key)

    team = _lookup_team(db, team_code)
    if team is None:
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="no active team matches this team_code", conflict_type="unknown_team",
        )
    user_id = _lookup_user_id(db, external_workforce_id)
    if user_id is None:
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="no existing user matches this external_workforce_id",
            conflict_type="unknown_identity",
        )

    currently_member = db.scalar(
        select(TeamMembership.id).where(
            TeamMembership.team_id == team.id, TeamMembership.user_id == user_id,
            TeamMembership.membership_status == "active",
            TeamMembership.effective_to.is_(None),
        )
    ) is not None

    if action == "add" and currently_member:
        return WorkforceRowClassification(
            row_number=row.row_number, action=action, external_workforce_id=external_workforce_id,
            normalized_identity=user_id, parsed_values={"team_id": str(team.id)},
            validation_result="warning", validation_detail="already an active member of this team",
            conflict_type="already_member", risk_level="routine",
        )
    if action == "end" and not currently_member:
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="user is not a current active member of this team",
            conflict_type="not_a_member",
        )
    return WorkforceRowClassification(
        row_number=row.row_number, action=action, external_workforce_id=external_workforce_id,
        normalized_identity=user_id,
        parsed_values={"team_id": str(team.id), "reason_code": reason_code},
        validation_result="valid", validation_detail=None, conflict_type=None,
        risk_level="routine",
    )


def classify_role_assignment_row(
    row: ParsedRow, *, db: Session, seen_identities: set[str]
) -> WorkforceRowClassification:
    action = _clean(row.values.get("action")).lower()
    external_workforce_id = _clean(row.values.get("external_workforce_id"))
    role_code = _clean(row.values.get("role_code")).lower()
    scope_type = _clean(row.values.get("scope_type")).lower()
    scope_code = _clean(row.values.get("scope_code"))
    reason_code = _clean(row.values.get("reason_code"))

    if not _valid_workforce_id(external_workforce_id):
        return _invalid(
            row.row_number, action=action or None, external_workforce_id=None,
            detail="external_workforce_id is required and must not contain whitespace",
        )
    if action not in _ROLE_ASSIGNMENT_ACTIONS:
        return _invalid(
            row.row_number, action=action or None, external_workforce_id=external_workforce_id,
            detail="action must be assign or end",
        )
    if role_code not in ROLE_APPOINTMENT_CAPABILITY:
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail=f"role_code must be one of: {', '.join(sorted(ROLE_APPOINTMENT_CAPABILITY))}",
            conflict_type="unknown_role",
        )
    if scope_type not in _SCOPE_TYPES:
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="scope_type must be installation, organization, or team",
        )
    if not _valid_reason_code(reason_code):
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="reason_code is required (lowercase letters, digits, underscore only)",
        )

    scope_id: uuid.UUID | None = None
    if scope_type == "team":
        if not scope_code:
            return _invalid(
                row.row_number, action=action, external_workforce_id=external_workforce_id,
                detail="scope_code is required when scope_type is team",
            )
        team = _lookup_team(db, scope_code)
        if team is None:
            return _invalid(
                row.row_number, action=action, external_workforce_id=external_workforce_id,
                detail="no active team matches this scope_code", conflict_type="unknown_team",
            )
        scope_id = team.id

    dedup_key = f"{external_workforce_id}:{role_code}:{scope_type}:{scope_id}"
    if dedup_key in seen_identities:
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="this identity/role/scope combination repeats within this file",
            conflict_type="duplicate_in_file",
        )
    seen_identities.add(dedup_key)

    user_id = _lookup_user_id(db, external_workforce_id)
    if user_id is None:
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="no existing user matches this external_workforce_id",
            conflict_type="unknown_identity",
        )

    currently_active = db.scalar(
        select(RoleAssignment.id).where(
            RoleAssignment.user_id == user_id, RoleAssignment.role_code == role_code,
            RoleAssignment.scope_type == scope_type, RoleAssignment.scope_id == scope_id,
            RoleAssignment.status == "active", RoleAssignment.effective_to.is_(None),
        )
    ) is not None

    parsed_values = {
        "role_code": role_code, "scope_type": scope_type,
        "scope_id": str(scope_id) if scope_id else None, "reason_code": reason_code,
    }
    if action == "assign" and currently_active:
        return WorkforceRowClassification(
            row_number=row.row_number, action=action, external_workforce_id=external_workforce_id,
            normalized_identity=user_id, parsed_values=parsed_values, validation_result="warning",
            validation_detail="user already holds this exact role at this scope",
            conflict_type="already_granted", risk_level="high_risk",
        )
    if action == "end" and not currently_active:
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="no active assignment matches this identity/role/scope to end",
            conflict_type="not_assigned",
        )
    return WorkforceRowClassification(
        row_number=row.row_number, action=action, external_workforce_id=external_workforce_id,
        normalized_identity=user_id, parsed_values=parsed_values, validation_result="valid",
        validation_detail=None, conflict_type=None,
        # Granting authority is the direction that needs the second approver (plan
        # 11.2 step 8: "role elevations... require the configured higher approval").
        # Ending an assignment only ever removes authority.
        risk_level="high_risk" if action == "assign" else "routine",
    )


def classify_reporting_assignment_row(
    row: ParsedRow, *, db: Session, seen_identities: set[str]
) -> WorkforceRowClassification:
    external_workforce_id = _clean(row.values.get("external_workforce_id"))
    supervisor_workforce_id = _clean(row.values.get("supervisor_workforce_id"))
    reason_code = _clean(row.values.get("reason_code")) or None
    action = _clean(row.values.get("action") or "set").lower()

    if not _valid_workforce_id(external_workforce_id):
        return _invalid(
            row.row_number, action=action or None, external_workforce_id=None,
            detail="external_workforce_id is required and must not contain whitespace",
        )
    if action != "set":
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="action must be set",
        )
    if not _valid_workforce_id(supervisor_workforce_id):
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="supervisor_workforce_id is required and must not contain whitespace",
        )
    if external_workforce_id == supervisor_workforce_id:
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="a user may not supervise themselves",
        )
    if external_workforce_id in seen_identities:
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="external_workforce_id repeats within this file",
            conflict_type="duplicate_in_file",
        )
    seen_identities.add(external_workforce_id)

    subordinate_id = _lookup_user_id(db, external_workforce_id)
    if subordinate_id is None:
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="no existing user matches this external_workforce_id",
            conflict_type="unknown_identity",
        )
    supervisor_id = _lookup_user_id(db, supervisor_workforce_id)
    if supervisor_id is None:
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="no existing user matches supervisor_workforce_id",
            conflict_type="unknown_supervisor",
        )
    if not _lookup_user_active(db, supervisor_id):
        return _invalid(
            row.row_number, action=action, external_workforce_id=external_workforce_id,
            detail="supervisor_workforce_id does not match an active user",
            conflict_type="unknown_supervisor",
        )

    current_supervisor_id = db.scalar(
        select(ReportingAssignment.supervisor_user_id).where(
            ReportingAssignment.subordinate_user_id == subordinate_id,
            ReportingAssignment.context_type == "organization",
            ReportingAssignment.context_id.is_(None),
            ReportingAssignment.assignment_type == "primary",
            ReportingAssignment.status == "active",
            ReportingAssignment.effective_to.is_(None),
        )
    )
    parsed_values = {"supervisor_user_id": str(supervisor_id), "reason_code": reason_code}
    if current_supervisor_id == supervisor_id:
        return WorkforceRowClassification(
            row_number=row.row_number, action=action, external_workforce_id=external_workforce_id,
            normalized_identity=subordinate_id, parsed_values=parsed_values,
            validation_result="warning",
            validation_detail="this is already the subordinate's active supervisor",
            conflict_type="already_set", risk_level="routine",
        )
    return WorkforceRowClassification(
        row_number=row.row_number, action=action, external_workforce_id=external_workforce_id,
        normalized_identity=subordinate_id, parsed_values=parsed_values,
        validation_result="valid", validation_detail=None, conflict_type=None,
        risk_level="routine",
    )
