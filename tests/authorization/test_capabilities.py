from __future__ import annotations

import uuid

import pytest

from app.authz import service as authz
from app.authz.capabilities import (
    CREATE_AGENT,
    RESET_USER_AUTH,
    ROLE_AGENT,
    ROLE_MANAGER,
    ROLE_SUPER_ADMIN,
)


def test_capabilities_are_default_deny():
    assert authz.capabilities_for(set()) == set()
    assert authz.capabilities_for({ROLE_AGENT}) == set()


def test_super_admin_can_reset_auth():
    caps = authz.capabilities_for({ROLE_SUPER_ADMIN})
    assert RESET_USER_AUTH in caps


def test_manager_cannot_reset_auth_but_can_create_agent():
    caps = authz.capabilities_for({ROLE_MANAGER})
    assert RESET_USER_AUTH not in caps
    assert CREATE_AGENT in caps


def test_self_approval_is_blocked():
    actor = uuid.uuid4()
    with pytest.raises(authz.SelfApprovalError):
        authz.assert_not_self(actor, actor)
    authz.assert_not_self(actor, uuid.uuid4())
