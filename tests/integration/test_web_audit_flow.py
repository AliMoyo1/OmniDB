"""Integration tests for the server-rendered audit trail (/audit): rendering,
authorization, and that the filter form round-trips through the page - the
scoping and filter-narrowing logic itself is covered at the service/JSON-API
level in tests/integration/test_reporting_and_audit_flow.py.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import login, make_user_with_role

pytestmark = pytest.mark.integration


def _manager() -> TestClient:
    from app.main import app

    email = f"auditmgr-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, "manager")
    client = TestClient(app, follow_redirects=False)
    login(client, email)
    return client


def test_unauthenticated_audit_redirects_to_login():
    from app.main import app

    client = TestClient(app, follow_redirects=False)
    resp = client.get("/audit")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_agent_without_view_audit_is_redirected_away():
    from app.main import app

    email = f"auditagent-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, "agent")
    client = TestClient(app, follow_redirects=False)
    login(client, email)
    resp = client.get("/audit")
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/dashboard")


def test_manager_sees_own_login_event():
    client = _manager()
    resp = client.get("/audit")
    assert resp.status_code == 200
    assert "auth.login" in resp.text


def test_action_filter_round_trips_and_narrows():
    client = _manager()
    unique = uuid.uuid4().hex[:8]

    # A filter matching nothing real should render an empty, not broken, page,
    # and repopulate the submitted value back into the input.
    resp = client.get(f"/audit?action=nonexistent-action-{unique}")
    assert resp.status_code == 200
    assert f'value="nonexistent-action-{unique}"' in resp.text
    assert "No audit activity matches" in resp.text

    # A filter matching the login this manager just performed should find it.
    resp = client.get("/audit?action=auth.login")
    assert resp.status_code == 200
    assert "auth.login" in resp.text
