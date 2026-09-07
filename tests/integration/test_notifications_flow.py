"""Integration tests for the notification inbox (real Postgres + Redis).

Covers the inbox in its own right (privacy, read/mark, unread count) and the
first real producer: workforce-import decisions/commits notifying the uploader.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.flags import service as flags_service
from app.notifications import service as notifications_service
from tests.integration.conftest import csrf_headers, login, make_user, make_user_with_role

pytestmark = pytest.mark.integration


def _enable_workforce_import() -> None:
    flag_toggler_id = make_user(f"notif-flag-{uuid.uuid4().hex[:8]}@example.com")
    with SessionLocal() as db:
        flags_service.set_flag(
            db, "workforce_import_enabled", True, actor_id=flag_toggler_id,
            reason_code="test_setup",
        )
        db.commit()


@pytest.fixture(autouse=True)
def _flag_on():
    _enable_workforce_import()


def _client_for(email: str) -> TestClient:
    from app.main import app

    client = TestClient(app, follow_redirects=False)
    login(client, email)
    return client


def _manager(prefix: str = "notifmgr") -> tuple[TestClient, str]:
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    user_id = make_user_with_role(email, "manager")
    return _client_for(email), str(user_id)


def _upload_users_create(client: TestClient, headers: dict) -> str:
    wid = f"notif-{uuid.uuid4().hex[:8]}"
    csv_text = (
        "action,external_workforce_id,login_identifier,display_name,start_date,end_date\r\n"
        f"create,{wid},{wid}@example.com,Name,,\r\n"
    )
    files = {"file": ("f.csv", csv_text.encode("utf-8"), "text/csv")}
    resp = client.post(
        "/api/v1/workforce/imports", files=files, data={"import_type": "users"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def test_notifications_are_private_to_their_recipient():
    """A notification is private: another user cannot see it or mark it read,
    and probing another's notification id is indistinguishable from a missing
    one (404 either way)."""
    owner, owner_id = _manager(prefix="notifowner")
    other, _ = _manager(prefix="notifother")

    with SessionLocal() as db:
        n = notifications_service.notify(
            db, recipient_id=uuid.UUID(owner_id), category="test",
            title="A private message", body="only for the owner",
        )
        db.commit()
        notification_id = str(n.id)

    # The other user sees nothing and cannot mark it read.
    assert other.get("/api/v1/notifications").json() == []
    other_headers = csrf_headers(other)
    denied = other.post(f"/api/v1/notifications/{notification_id}/read", headers=other_headers)
    assert denied.status_code == 404, denied.text

    # The owner sees it, and its unread count is 1.
    owner_list = owner.get("/api/v1/notifications").json()
    assert [n["id"] for n in owner_list] == [notification_id]
    assert owner.get("/api/v1/notifications/unread-count").json()["unread"] == 1

    # The owner marks it read; the count drops.
    owner_headers = csrf_headers(owner)
    ok = owner.post(f"/api/v1/notifications/{notification_id}/read", headers=owner_headers)
    assert ok.status_code == 200, ok.text
    assert owner.get("/api/v1/notifications/unread-count").json()["unread"] == 0
    assert owner.get("/api/v1/notifications?unread_only=true").json() == []


def test_import_decision_by_another_user_notifies_the_uploader():
    """The first real producer: when someone other than the uploader records a
    decision on an import, the uploader is notified, with a pointer back to the
    job."""
    uploader, _uploader_id = _manager(prefix="notifuploader")
    up_headers = csrf_headers(uploader)
    job_id = _upload_users_create(uploader, up_headers)

    approver, _ = _manager(prefix="notifapprover")
    ap_headers = csrf_headers(approver)
    decision = approver.patch(
        f"/api/v1/workforce/imports/{job_id}/decisions",
        json={"decision": "approve", "decision_tier": "standard"}, headers=ap_headers,
    )
    assert decision.status_code == 200, decision.text

    inbox = uploader.get("/api/v1/notifications").json()
    assert len(inbox) == 1, inbox
    note = inbox[0]
    assert note["category"] == "workforce_import.decision"
    assert "approved" in note["title"]
    assert note["related_entity_type"] == "workforce_import_job"
    assert note["related_entity_id"] == job_id
    assert note["read_at"] is None


def test_self_decision_does_not_notify():
    """No self-notification: if the uploader records their own decision, they
    already know - no inbox noise."""
    uploader, _ = _manager(prefix="notifselfdecide")
    headers = csrf_headers(uploader)
    job_id = _upload_users_create(uploader, headers)

    decision = uploader.patch(
        f"/api/v1/workforce/imports/{job_id}/decisions",
        json={"decision": "approve", "decision_tier": "standard"}, headers=headers,
    )
    assert decision.status_code == 200, decision.text
    assert uploader.get("/api/v1/notifications").json() == []


def test_inbox_page_renders_and_mark_all_read_clears_the_badge():
    """The server-rendered inbox lists the user's notifications, and mark-all-
    read clears the unread badge that the shared shell shows on every page."""
    owner, owner_id = _manager(prefix="notifpage")
    with SessionLocal() as db:
        for i in range(3):
            notifications_service.notify(
                db, recipient_id=uuid.UUID(owner_id), category="test",
                title=f"Message {i}", body="body",
            )
        db.commit()

    page = owner.get("/notifications")
    assert page.status_code == 200
    assert "Message 0" in page.text
    assert "Your inbox." in page.text

    # The dashboard shell shows the unread badge (3) too.
    dash = owner.get("/dashboard")
    assert "Inbox" in dash.text

    # Web form endpoints take the CSRF token as a form field (verify_form_csrf),
    # not the x-csrf-token header the JSON API uses.
    cleared = owner.post(
        "/notifications/read-all", data={"csrf_token": owner.cookies.get("cc_csrf")}
    )
    assert cleared.status_code == 303
    assert owner.get("/api/v1/notifications/unread-count").json()["unread"] == 0


def _needs_review(client: TestClient, job_id: str) -> list:
    return [
        n
        for n in client.get("/api/v1/notifications").json()
        if n["category"] == "workforce_import.needs_review" and n["related_entity_id"] == job_id
    ]


def test_high_risk_import_broadcasts_to_qualified_non_uploader_approvers():
    """When a parsed import genuinely needs a second person (it has high-risk
    rows), the people who can actually reach and approve it are pinged - the
    push half of the two-person workflow. The uploader is excluded (separation
    of duties), and someone with no approval capability is never a candidate."""
    from app.authz.capabilities import ROLE_AGENT
    from app.workforce import service as workforce_service

    uploader, uploader_id = _manager(prefix="brdcstuploader")
    up_headers = csrf_headers(uploader)

    # A target holding an org-scoped agent role, so deactivating it is high-risk
    # and approving it needs authority over that org scope.
    twid = f"brdcst-target-{uuid.uuid4().hex[:8]}"
    target_id = make_user(f"{twid}@example.com")
    with SessionLocal() as db:
        workforce_service.assign_role(
            db, target_user_id=target_id, role_code=ROLE_AGENT, scope_type="organization",
            scope_id=None, appointed_by=uuid.UUID(uploader_id), reason_code="test_setup",
        )
        db.commit()

    # A second manager (org-wide authority -> can access + approve), created
    # before the upload so the broadcast can reach them, and a plain agent who
    # holds no approval capability at all.
    approver, _ = _manager(prefix="brdcstapprover")
    agent_email = f"brdcst-agent-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(agent_email, "agent")
    agent = _client_for(agent_email)

    deact_csv = f"external_workforce_id,reason_code\r\n{twid},performance_review\r\n"
    files = {"file": ("d.csv", deact_csv.encode("utf-8"), "text/csv")}
    up = uploader.post(
        "/api/v1/workforce/imports", files=files,
        data={"import_type": "explicit_deactivations"}, headers=up_headers,
    )
    assert up.status_code == 200, up.text
    job_id = up.json()["id"]
    assert uploader.get(f"/api/v1/workforce/imports/{job_id}").json()["high_risk_rows"] == 1

    assert len(_needs_review(approver, job_id)) == 1
    assert _needs_review(uploader, job_id) == []  # uploader excluded (two-person rule)
    assert _needs_review(agent, job_id) == []  # agent holds no approval capability


def test_routine_only_import_does_not_broadcast_for_review():
    """A routine-only import (no high-risk rows) needs no second person - the
    uploader can carry it - so it pings nobody for review."""
    watcher, _ = _manager(prefix="brdcstwatcher")
    uploader, _ = _manager(prefix="brdcstroutine")
    headers = csrf_headers(uploader)
    job_id = _upload_users_create(uploader, headers)
    assert uploader.get(f"/api/v1/workforce/imports/{job_id}").json()["high_risk_rows"] == 0
    assert _needs_review(watcher, job_id) == []
