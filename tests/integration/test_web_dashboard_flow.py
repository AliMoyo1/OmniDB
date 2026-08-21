"""Integration tests for the server-rendered dashboard (Phase 4A-4): real HTML
forms, session-cookie page auth, and the form-based CSRF path - distinct from the
JSON API's header-based CSRF, covered separately in the other integration tests.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models.authz import RoleAssignment
from tests.integration.conftest import TEST_PASSWORD, login, make_user_with_role

_PROVENANCE_FORM = {
    "name": "Web Test Campaign",
    "purpose": "Customer outreach",
    "data_source": "CRM export",
    "data_obtained_at": "2026-01-01",
    "lawful_basis_or_consent_reference": "consent-ref-123",
}


def _csrf(client: TestClient) -> str:
    token = client.cookies.get("cc_csrf")
    assert token, "csrf cookie not set; did you log in first?"
    return token


def _manager(email_prefix: str = "webmgr") -> tuple[TestClient, str]:
    from app.main import app

    email = f"{email_prefix}-{uuid.uuid4().hex[:8]}@example.com"
    user_id = make_user_with_role(email, "manager")
    client = TestClient(app, follow_redirects=False)
    login(client, email)
    return client, str(user_id)


def test_unauthenticated_dashboard_redirects_to_login():
    from app.main import app

    client = TestClient(app, follow_redirects=False)
    resp = client.get("/dashboard")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_login_page_renders_and_has_a_form():
    from app.main import app

    client = TestClient(app, follow_redirects=False)
    resp = client.get("/login")
    assert resp.status_code == 200
    assert '<form method="post" action="/login">' in resp.text


def test_login_success_sets_cookies_and_redirects_to_dashboard():
    from app.main import app

    email = f"weblogin-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, "manager")
    client = TestClient(app, follow_redirects=False)
    resp = client.post(
        "/login", data={"email": email, "password": TEST_PASSWORD, "totp_code": ""}
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"
    assert client.cookies.get("cc_session")
    assert client.cookies.get("cc_csrf")


def test_login_wrong_password_shows_error_and_preserves_email():
    from app.main import app

    email = f"webwrong-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, "manager")
    client = TestClient(app, follow_redirects=False)
    resp = client.post("/login", data={"email": email, "password": "not the password"})
    assert resp.status_code == 401
    assert "Invalid email, password, or code." in resp.text
    assert f'value="{email}"' in resp.text


def test_dashboard_shows_manager_sections():
    client, _ = _manager()
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "Campaigns" in resp.text
    assert "Workforce" in resp.text
    assert "Teams" in resp.text
    assert "Recent audit activity" in resp.text
    assert 'action="/dashboard/campaigns"' in resp.text


def test_agent_sees_no_dashboard_sections():
    from app.main import app

    email = f"webagent-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, "agent")
    client = TestClient(app, follow_redirects=False)
    login(client, email)
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "Campaigns" not in resp.text
    assert "Workforce" not in resp.text
    assert "Recent audit activity" not in resp.text


def test_create_campaign_via_form():
    client, _ = _manager()
    resp = client.post(
        "/dashboard/campaigns", data={"csrf_token": _csrf(client), **_PROVENANCE_FORM}
    )
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/dashboard?flash_success=")

    dashboard = client.get("/dashboard")
    assert "Web Test Campaign" in dashboard.text


def test_create_campaign_with_bad_csrf_token_is_rejected():
    client, _ = _manager()
    # A distinct name from _PROVENANCE_FORM's: campaigns are org-wide visible with
    # no per-test rollback, so reusing the same name as the positive creation test
    # could pass this assertion for the wrong reason (seeing that other campaign)
    # regardless of whether this CSRF check actually did its job.
    payload = {**_PROVENANCE_FORM, "name": "Should Never Exist Campaign"}
    resp = client.post(
        "/dashboard/campaigns", data={"csrf_token": "not-a-real-token", **payload}
    )
    assert resp.status_code == 303
    assert "flash_error" in resp.headers["location"]

    dashboard = client.get("/dashboard")
    assert "Should Never Exist Campaign" not in dashboard.text


def test_create_user_via_form_renders_activation_token_directly():
    client, _ = _manager()
    email = f"created-{uuid.uuid4().hex[:8]}@example.com"
    resp = client.post(
        "/dashboard/users",
        data={"csrf_token": _csrf(client), "email": email, "display_name": "Created User"},
    )
    # Rendered directly, not redirected - a one-time secret must never sit in a URL.
    assert resp.status_code == 200
    assert email in resp.text
    assert "<code>" in resp.text
    assert resp.url.path != "/dashboard"


def test_assign_role_via_form_then_visible_on_dashboard():
    # Checked against the database directly, not by scraping the rendered page:
    # "team_captain" also appears unconditionally in every row's role dropdown
    # <option>, so a text-in-page assertion would pass even if the assignment
    # silently failed.
    client, _ = _manager()
    target_id = make_user_with_role(f"target-{uuid.uuid4().hex[:8]}@example.com", "agent")
    resp = client.post(
        f"/dashboard/users/{target_id}/roles",
        data={
            "csrf_token": _csrf(client), "role_code": "team_captain",
            "scope_type": "organization", "scope_id": "",
        },
    )
    assert resp.status_code == 303
    assert "flash_success" in resp.headers["location"]

    with SessionLocal() as db:
        assignment = db.scalar(
            select(RoleAssignment).where(
                RoleAssignment.user_id == target_id,
                RoleAssignment.role_code == "team_captain",
                RoleAssignment.status == "active",
            )
        )
        assert assignment is not None
        assert assignment.scope_type == "organization"
        assert assignment.scope_id is None


def test_logout_revokes_the_session():
    client, _ = _manager()
    resp = client.post("/logout", data={"csrf_token": _csrf(client)})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"

    after = client.get("/dashboard")
    assert after.status_code == 303
    assert after.headers["location"] == "/login"
