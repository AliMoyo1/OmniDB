"""Create the first CipherContact Super Admin through a controlled operator command.

This module intentionally has no HTTP route. It uses a transaction-scoped PostgreSQL
advisory lock to serialize first-admin creation and emits the one-time activation token
only to a root-controlled file, never to stdout, logs, or an audit-event payload.
"""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.auth.service import issue_activation_token, normalize_email
from app.authz.capabilities import ROLE_SUPER_ADMIN
from app.db_locks import lock_initial_super_admin
from app.models.authz import RoleAssignment
from app.models.base import utcnow
from app.models.identity import User

DEFAULT_TOKEN_DIRECTORY = Path("/var/lib/ciphercontact/bootstrap")
DEFAULT_TOKEN_FILE = DEFAULT_TOKEN_DIRECTORY / "initial-super-admin.activation"


class BootstrapError(RuntimeError):
    """A controlled bootstrap failure that is safe to show to an operator."""


class BootstrapAlreadyInitialized(BootstrapError):
    """An active Super Admin already exists, so first-admin bootstrap must stop."""


class BootstrapValidationError(BootstrapError):
    """An operator supplied invalid identity or token-file input."""


class TokenFileError(BootstrapError):
    """The activation token could not be written to the approved destination."""


@dataclass(frozen=True)
class BootstrapResult:
    user_id: uuid.UUID
    activation_token: str


def _validate_identity(
    *, email: str, display_name: str, workforce_id: str | None
) -> tuple[str, str, str]:
    normalized_email = normalize_email(email)
    local_part, separator, domain = normalized_email.partition("@")
    if (
        not separator
        or not local_part
        or not domain
        or "." not in domain
        or len(normalized_email) > 320
        or any(character.isspace() for character in normalized_email)
    ):
        raise BootstrapValidationError("email must be a valid, non-empty address")

    normalized_name = display_name.strip()
    if not normalized_name or len(normalized_name) > 200:
        raise BootstrapValidationError("display name must contain 1 to 200 characters")

    normalized_workforce_id = (workforce_id or local_part).strip()
    if (
        not normalized_workforce_id
        or len(normalized_workforce_id) > 150
        or any(character.isspace() for character in normalized_workforce_id)
    ):
        raise BootstrapValidationError(
            "workforce ID must contain 1 to 150 non-whitespace characters"
        )
    return normalized_email, normalized_name, normalized_workforce_id


def bootstrap_super_admin(
    db: Session,
    *,
    email: str,
    display_name: str,
    workforce_id: str | None = None,
) -> BootstrapResult:
    """Create exactly the first active Super Admin inside the caller's transaction.

    The advisory lock serializes two operators racing an empty database. Once any
    active Super Admin assignment exists, the command refuses to create another;
    subsequent high-privilege accounts must follow a separately approved ops process.
    """
    normalized_email, normalized_name, normalized_workforce_id = _validate_identity(
        email=email,
        display_name=display_name,
        workforce_id=workforce_id,
    )
    lock_initial_super_admin(db)

    active_super_admin = db.scalar(
        select(RoleAssignment.id)
        .where(
            RoleAssignment.role_code == ROLE_SUPER_ADMIN,
            RoleAssignment.status == "active",
            RoleAssignment.effective_to.is_(None),
        )
        .limit(1)
    )
    if active_super_admin is not None:
        raise BootstrapAlreadyInitialized(
            "an active Super Admin already exists; first-admin bootstrap is disabled"
        )

    duplicate = db.scalar(
        select(User.id)
        .where(
            or_(
                User.email == normalized_email,
                User.workforce_id == normalized_workforce_id,
            )
        )
        .limit(1)
    )
    if duplicate is not None:
        raise BootstrapValidationError(
            "a user with this email address or workforce ID already exists"
        )

    now = utcnow()
    user = User(
        workforce_id=normalized_workforce_id,
        email=normalized_email,
        display_name=normalized_name,
    )
    db.add(user)
    db.flush()
    db.add(
        RoleAssignment(
            user_id=user.id,
            role_code=ROLE_SUPER_ADMIN,
            scope_type="installation",
            scope_id=None,
            effective_from=now,
            appointment_type="permanent",
            reason_code="ops_bootstrap",
            appointed_by=None,
            approved_by=None,
        )
    )
    activation_token = issue_activation_token(db, user.id, created_by=None)
    record_audit(
        db,
        action="ops.bootstrap_super_admin",
        result="success",
        target_type="user",
        target_id=user.id,
        reason_code="ops_bootstrap",
        event_metadata={
            "role_code": ROLE_SUPER_ADMIN,
            "scope_type": "installation",
            "provisioning_path": "operator_cli",
        },
    )
    db.flush()
    return BootstrapResult(user_id=user.id, activation_token=activation_token)


def write_activation_token(
    token: str,
    token_file: Path,
    *,
    allowed_directory: Path = DEFAULT_TOKEN_DIRECTORY,
) -> None:
    """Write a one-time token once, with mode 0600, in the approved volume only."""
    if not token_file.is_absolute():
        raise TokenFileError("activation token file must use an absolute path")
    try:
        resolved_directory = allowed_directory.resolve(strict=True)
        resolved_parent = token_file.parent.resolve(strict=True)
    except OSError as exc:
        raise TokenFileError("activation token directory is unavailable") from exc
    if resolved_parent != resolved_directory or token_file.name in {"", ".", ".."}:
        raise TokenFileError("activation token file must be directly inside the approved directory")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(token_file, flags, 0o600)
    except FileExistsError as exc:
        raise TokenFileError(
            "activation token file already exists; refusing to overwrite it"
        ) from exc
    except OSError as exc:
        raise TokenFileError("activation token file could not be created") from exc

    try:
        # The production image is Linux; this keeps local Windows validation portable.
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        try:
            token_file.unlink(missing_ok=True)
        except OSError:
            pass
        raise TokenFileError("activation token file could not be written") from exc


def _remove_token_file(token_file: Path) -> None:
    try:
        token_file.unlink(missing_ok=True)
    except OSError:
        pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create CipherContact's first Super Admin.")
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--workforce-id")
    parser.add_argument(
        "--activation-token-file",
        type=Path,
        default=DEFAULT_TOKEN_FILE,
        help="root-controlled output path in the dedicated bootstrap volume",
    )
    parser.add_argument(
        "--confirm-initial-super-admin",
        action="store_true",
        help="required acknowledgement that this creates the first installation Super Admin",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.confirm_initial_super_admin:
        print("ERROR: --confirm-initial-super-admin is required", file=sys.stderr)
        return 2

    token_written = False
    try:
        # Load settings and the database engine only for the operator command.
        from app.db import SessionLocal

        with SessionLocal() as db:
            try:
                result = bootstrap_super_admin(
                    db,
                    email=args.email,
                    display_name=args.display_name,
                    workforce_id=args.workforce_id,
                )
                write_activation_token(result.activation_token, args.activation_token_file)
                token_written = True
                db.commit()
            except (BootstrapError, OSError, SQLAlchemyError):
                db.rollback()
                if token_written:
                    _remove_token_file(args.activation_token_file)
                raise
    except BootstrapError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except (OSError, SQLAlchemyError):
        print(
            "ERROR: bootstrap failed before a credential could be issued",
            file=sys.stderr,
        )
        return 3

    print(f"Initial Super Admin created (user ID: {result.user_id}).")
    print(f"Activation token written to: {args.activation_token_file}")
    print("It expires in 24 hours. Deliver it through an approved offline channel.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
