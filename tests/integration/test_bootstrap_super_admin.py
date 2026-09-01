"""Integration coverage for the one-time, operator-only Super Admin bootstrap."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.authz.capabilities import ROLE_SUPER_ADMIN
from app.db import SessionLocal
from app.models.activation import ActivationToken
from app.models.audit import AuditEvent
from app.models.authz import RoleAssignment
from app.models.base import utcnow
from app.models.identity import User
from app.ops.bootstrap_super_admin import (
    BootstrapAlreadyInitialized,
    bootstrap_super_admin,
)
from app.security.tokens import hash_token

pytestmark = pytest.mark.integration


def test_bootstrap_creates_the_first_super_admin_once_and_audits_it():
    email = f"bootstrap-{uuid.uuid4().hex[:12]}@example.com"
    with SessionLocal() as db:
        # Other integration tests legitimately create Super Admins. End them only
        # inside this uncommitted transaction so this test can exercise first-admin
        # behavior without persisting or altering their data.
        now = utcnow()
        for assignment in db.scalars(
            select(RoleAssignment).where(
                RoleAssignment.role_code == ROLE_SUPER_ADMIN,
                RoleAssignment.status == "active",
                RoleAssignment.effective_to.is_(None),
            )
        ):
            assignment.status = "ended"
            assignment.effective_to = now
            assignment.ended_at = now
        db.flush()

        result = bootstrap_super_admin(
            db,
            email=email,
            display_name="Initial Administrator",
        )
        user = db.get(User, result.user_id)
        assert user is not None
        assert user.email == email
        assert user.password_hash is None
        assignment = db.scalar(
            select(RoleAssignment).where(
                RoleAssignment.user_id == user.id,
                RoleAssignment.role_code == ROLE_SUPER_ADMIN,
                RoleAssignment.scope_type == "installation",
                RoleAssignment.status == "active",
            )
        )
        assert assignment is not None
        activation = db.scalar(
            select(ActivationToken).where(
                ActivationToken.token_hash == hash_token(result.activation_token)
            )
        )
        assert activation is not None
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "ops.bootstrap_super_admin",
                AuditEvent.target_id == user.id,
            )
        )
        assert audit is not None
        assert audit.event_metadata == {
            "role_code": ROLE_SUPER_ADMIN,
            "scope_type": "installation",
            "provisioning_path": "operator_cli",
        }
        with pytest.raises(BootstrapAlreadyInitialized):
            bootstrap_super_admin(
                db,
                email=f"second-{uuid.uuid4().hex[:12]}@example.com",
                display_name="Second Administrator",
            )
        # Do not leak a real Super Admin into the shared integration database.
        db.rollback()
