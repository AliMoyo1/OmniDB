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
from app.models.identity import User

_MAX_WORKFORCE_ID_LEN = 150
_MAX_DISPLAY_NAME_LEN = 200
_MAX_REASON_CODE_LEN = 50
_USERS_ACTIONS = {"create", "update", "reactivate"}


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

    if not reason_code or len(reason_code) > _MAX_REASON_CODE_LEN or not re.fullmatch(
        r"[a-z0-9_]+", reason_code
    ):
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
