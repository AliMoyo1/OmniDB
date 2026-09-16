"""Integration tests for the administrative user directory (/admin/users):

Phase A (read-only): a scoped, filtered, paginated view over
workforce_service.list_user_directory and directory_summary - scope, search,
filters, pagination stability, summary counts, and the
query-count-must-not-scale-with-rows requirement (plan 12).

Phase B (this file's detail/action tests): the per-user detail page and the
three credential-administration actions (issue/replace activation code, reset
password, reset MFA) - scope-gated viewing, RESET_USER_AUTH-gated acting
(independent of workforce-appointment authority), recent-reauthentication,
self-reset prevention, session revocation, audit without secrets, and
concurrent-issuance safety. Disable/reactivate stay covered on the existing
/workforce/users/{id} page (test_web_workforce_flow.py) - this file does not
duplicate that coverage.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.auth import service as auth_service
from app.db import SessionLocal, engine
from app.models.activation import ActivationToken
from app.models.audit import AuditEvent
from app.models.identity import TeamMembership, User
from app.models.session import Session as SessionModel
from app.security.tokens import hash_token
from app.workforce import service as workforce_service
from tests.integration.conftest import (
    TEST_PASSWORD,
    TEST_TOTP_SECRET,
    login,
    make_user,
    make_user_with_role,
)

pytestmark = pytest.mark.integration


def _make_team(prefix: str = "adminusr") -> uuid.UUID:
    creator_id = make_user(f"{prefix}-creator-{uuid.uuid4().hex[:8]}@example.com")
    code = f"{prefix}-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        team = workforce_service.create_team(
            db, name=f"Team {code}", external_code=code, parent_team_id=None,
            default_timezone="Africa/Harare", created_by=creator_id,
        )
        db.commit()
        return team.id


def _add_membership(team_id: uuid.UUID, user_id: uuid.UUID) -> None:
    with SessionLocal() as db:
        db.add(
            TeamMembership(team_id=team_id, user_id=user_id, effective_from=datetime.now(UTC))
        )
        db.commit()


def _issue_token(user_id: uuid.UUID, *, expires_delta: timedelta) -> None:
    with SessionLocal() as db:
        db.add(
            ActivationToken(
                user_id=user_id,
                token_hash=hash_token(uuid.uuid4().hex),
                expires_at=datetime.now(UTC) + expires_delta,
            )
        )
        db.commit()


def _make_bare_user(prefix: str) -> uuid.UUID:
    """No password, no activation token at all - activation_not_issued."""
    with SessionLocal() as db:
        email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
        user = User(workforce_id=email.split("@")[0], email=email, display_name="Bare User")
        db.add(user)
        db.commit()
        return user.id


def _client_for(email: str) -> TestClient:
    from app.main import app

    client = TestClient(app, follow_redirects=False)
    login(client, email)
    return client


def _csrf(client: TestClient) -> str:
    token = client.cookies.get("cc_csrf")
    assert token, "csrf cookie not set; did you log in first?"
    return token


def _reauthenticate(client: TestClient) -> None:
    resp = client.post(
        "/security/mfa/reauthenticate",
        data={
            "csrf_token": _csrf(client),
            "password": TEST_PASSWORD,
            "totp_code": pyotp.TOTP(TEST_TOTP_SECRET).now(),
        },
    )
    assert resp.status_code == 303, resp.text
    assert resp.headers["location"].startswith("/security/mfa")


# --- Authorization and scope --------------------------------------------------------


def test_unauthenticated_redirects_to_login():
    from app.main import app

    client = TestClient(app, follow_redirects=False)
    resp = client.get("/admin/users")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_agent_without_capability_is_redirected_away():
    email = f"adminusragent-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, "agent")
    client = _client_for(email)
    resp = client.get("/admin/users")
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/dashboard")


def test_viewer_without_capability_is_redirected_away():
    email = f"adminusrviewer-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, "viewer")
    client = _client_for(email)
    resp = client.get("/admin/users")
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/dashboard")


def test_team_leader_sees_only_their_own_team():
    team_a = _make_team("adminusrtla")
    team_b = _make_team("adminusrtlb")
    user_in_a = _make_bare_user("adminusrina")
    user_in_b = _make_bare_user("adminusrinb")
    _add_membership(team_a, user_in_a)
    _add_membership(team_b, user_in_b)

    leader = f"adminusrleader-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(leader, "team_leader", scope_type="team", scope_id=team_a)

    with SessionLocal() as db:
        leader_id = db.scalar(select(User.id).where(User.email == leader))
        page = workforce_service.list_user_directory(db, leader_id)
        visible_ids = {row.user.id for row in page.rows}
        assert user_in_a in visible_ids
        assert user_in_b not in visible_ids

        # A filter must never widen scope: asking for the other team's id by
        # direct URL/parameter forgery returns nothing, not team_b's roster.
        forged = workforce_service.list_user_directory(db, leader_id, team_id=team_b)
        assert forged.rows == []
        assert forged.total_count == 0


def test_manager_sees_users_across_teams():
    team_a = _make_team("adminusrmgra")
    user_in_a = _make_bare_user("adminusrmgru")
    _add_membership(team_a, user_in_a)

    manager = f"adminusrmgr2-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(manager, "manager")
    with SessionLocal() as db:
        manager_id = db.scalar(select(User.id).where(User.email == manager))
        target = db.get(User, user_in_a)
        assert target is not None
        # This suite's database is shared and never rolled back between test runs
        # (see test_web_workforce_flow.py's _create_user comment), so an unpaged,
        # unfiltered listing could genuinely have thousands of names ahead of this
        # one alphabetically - search for the exact workforce ID instead of
        # assuming an unbounded default page contains it.
        page = workforce_service.list_user_directory(
            db, manager_id, search=target.workforce_id
        )
        assert user_in_a in {row.user.id for row in page.rows}


def test_super_admin_sees_the_full_directory_via_reset_user_auth():
    """Plan 6.1: Super Administrator visibility is based on RESET_USER_AUTH at
    installation scope, independent of any workforce appointment capability -
    can_view_admin_directory and the scoping query both check it directly
    rather than relying only on visible_team_ids.

    Note: in this codebase's current ROLE_CAPABILITIES, super_admin's bundle
    already includes CREATE_MANAGER and MANAGE_ROLES alongside RESET_USER_AUTH
    (app/authz/capabilities.py), so a Super Administrator here also sees the
    bulk-import panel today - that bundling is an existing, pre-Phase-A role
    design choice, not something this directory page introduces. Full
    separation of "can reset credentials" from "can manage workforce" (plan
    invariant 9.4) would need a capability-model change, not a Phase A
    read-only directory.
    """
    email = f"adminusrsa2-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(email, "super_admin", scope_type="installation", scope_id=None)
    client = _client_for(email)

    resp = client.get("/admin/users")
    assert resp.status_code == 200

    with SessionLocal() as db:
        from app.authz import service as authz
        from app.authz.capabilities import RESET_USER_AUTH

        actor_id = db.scalar(select(User.id).where(User.email == email))
        assert workforce_service.can_view_admin_directory(db, actor_id) is True
        assert authz.has_assigned_capability(db, actor_id, RESET_USER_AUTH) is True
        page = workforce_service.list_user_directory(db, actor_id)
        assert page.sees_everyone is True


# --- Search and filters --------------------------------------------------------------


def test_search_matches_name_email_and_workforce_id():
    team = _make_team("adminusrsearch")
    target = _make_bare_user("adminusrfindme")
    _add_membership(team, target)
    with SessionLocal() as db:
        user = db.get(User, target)
        assert user is not None
        workforce_id, email, display_name = user.workforce_id, user.email, user.display_name

        leader = f"adminusrsearchtl-{uuid.uuid4().hex[:8]}@example.com"
        make_user_with_role(leader, "team_leader", scope_type="team", scope_id=team)
        leader_id = db.scalar(select(User.id).where(User.email == leader))

        for term in (workforce_id, email, display_name.split()[0]):
            page = workforce_service.list_user_directory(db, leader_id, search=term)
            assert target in {row.user.id for row in page.rows}, term


def test_status_filter_narrows_to_active_or_inactive():
    team = _make_team("adminusrstatus")
    active_user = _make_bare_user("adminusractive")
    inactive_user = _make_bare_user("adminusrinactive")
    _add_membership(team, active_user)
    _add_membership(team, inactive_user)
    with SessionLocal() as db:
        target = db.get(User, inactive_user)
        assert target is not None
        target.active = False
        db.commit()

        leader = f"adminusrstatustl-{uuid.uuid4().hex[:8]}@example.com"
        make_user_with_role(leader, "team_leader", scope_type="team", scope_id=team)
        leader_id = db.scalar(select(User.id).where(User.email == leader))

        active_page = workforce_service.list_user_directory(db, leader_id, status="active")
        active_ids = {row.user.id for row in active_page.rows}
        assert active_user in active_ids
        assert inactive_user not in active_ids

        inactive_page = workforce_service.list_user_directory(db, leader_id, status="inactive")
        inactive_ids = {row.user.id for row in inactive_page.rows}
        assert inactive_user in inactive_ids
        assert active_user not in inactive_ids


def test_activation_state_covers_every_derived_state():
    team = _make_team("adminusrstate")
    leader = f"adminusrstatetl-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(leader, "team_leader", scope_type="team", scope_id=team)

    not_issued = _make_bare_user("adminusrnotissued")
    code_active = _make_bare_user("adminusrcodeactive")
    code_expired = _make_bare_user("adminusrcodeexpired")
    mfa_required = make_user(
        f"adminusrmfareq-{uuid.uuid4().hex[:8]}@example.com", totp_enrolled=False
    )
    ready = make_user(f"adminusrready-{uuid.uuid4().hex[:8]}@example.com")
    disabled = make_user(f"adminusrdisabled-{uuid.uuid4().hex[:8]}@example.com")

    _issue_token(code_active, expires_delta=timedelta(hours=24))
    _issue_token(code_expired, expires_delta=timedelta(hours=-1))

    for uid in (not_issued, code_active, code_expired, mfa_required, ready, disabled):
        _add_membership(team, uid)
    with SessionLocal() as db:
        target = db.get(User, disabled)
        assert target is not None
        target.active = False
        db.commit()

        leader_id = db.scalar(select(User.id).where(User.email == leader))

        expected = {
            workforce_service.STATE_ACTIVATION_NOT_ISSUED: not_issued,
            workforce_service.STATE_ACTIVATION_CODE_ACTIVE: code_active,
            workforce_service.STATE_ACTIVATION_CODE_EXPIRED: code_expired,
            workforce_service.STATE_MFA_SETUP_REQUIRED: mfa_required,
            workforce_service.STATE_READY: ready,
            workforce_service.STATE_DISABLED: disabled,
        }
        for state, expected_id in expected.items():
            page = workforce_service.list_user_directory(
                db, leader_id, activation_state=state, page_size=10
            )
            ids = {row.user.id for row in page.rows}
            assert ids == {expected_id}, (state, ids)
            row = next(r for r in page.rows if r.user.id == expected_id)
            assert row.activation_state == state

        summary = workforce_service.directory_summary(db, leader_id)
        assert summary.total == 6
        assert summary.active == 5
        assert summary.inactive == 1
        assert summary.awaiting_activation == 3  # not_issued, code_active, code_expired
        assert summary.mfa_setup_required == 1
        assert summary.never_logged_in == 6  # none of these users have logged in


def test_role_filter_narrows_to_holders_of_that_role():
    team = _make_team("adminusrrole")
    leader = f"adminusrroletl-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(leader, "team_leader", scope_type="team", scope_id=team)

    captain_email = f"adminusrcaptain-{uuid.uuid4().hex[:8]}@example.com"
    captain_id = make_user_with_role(
        captain_email, "team_captain", scope_type="team", scope_id=team
    )
    plain_user = _make_bare_user("adminusrplain")
    _add_membership(team, captain_id)
    _add_membership(team, plain_user)

    with SessionLocal() as db:
        leader_id = db.scalar(select(User.id).where(User.email == leader))
        page = workforce_service.list_user_directory(db, leader_id, role="team_captain")
        ids = {row.user.id for row in page.rows}
        assert ids == {captain_id}


def test_team_filter_narrows_for_an_actor_who_sees_everyone():
    team_a = _make_team("adminusrtfa")
    team_b = _make_team("adminusrtfb")
    user_a = _make_bare_user("adminusrtfua")
    user_b = _make_bare_user("adminusrtfub")
    _add_membership(team_a, user_a)
    _add_membership(team_b, user_b)

    manager = f"adminusrtfmgr-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(manager, "manager")
    with SessionLocal() as db:
        manager_id = db.scalar(select(User.id).where(User.email == manager))
        page = workforce_service.list_user_directory(db, manager_id, team_id=team_a)
        ids = {row.user.id for row in page.rows}
        assert user_a in ids
        assert user_b not in ids


# --- Pagination ------------------------------------------------------------------


def test_pagination_has_no_duplicates_or_gaps():
    team = _make_team("adminusrpage")
    leader = f"adminusrpagetl-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(leader, "team_leader", scope_type="team", scope_id=team)
    created = {_make_bare_user(f"adminusrpu{i}") for i in range(12)}
    for uid in created:
        _add_membership(team, uid)

    with SessionLocal() as db:
        leader_id = db.scalar(select(User.id).where(User.email == leader))
        seen: list[uuid.UUID] = []
        for page_number in (1, 2, 3):
            page = workforce_service.list_user_directory(
                db, leader_id, page=page_number, page_size=5
            )
            seen.extend(row.user.id for row in page.rows)
        assert len(seen) == len(created)
        assert len(seen) == len(set(seen)), "pagination produced a duplicate row"
        assert set(seen) == created


# --- Query-count bound (plan 12: filters must not perform per-row queries) --------


def _make_scoped_team_with_agents(prefix: str, count: int) -> str:
    """A fresh team with `count` agents (each with a team-scoped role assignment,
    so both the role and team bounded-lookup queries have something to join
    against), plus a team leader scoped to it. Returns the leader's email."""
    team = _make_team(prefix)
    leader = f"{prefix}tl-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(leader, "team_leader", scope_type="team", scope_id=team)
    for i in range(count):
        uid = make_user_with_role(
            f"{prefix}u{i}-{uuid.uuid4().hex[:8]}@example.com",
            "agent",
            scope_type="team",
            scope_id=team,
        )
        _add_membership(team, uid)
    return leader


def _query_count_for_directory(leader_email: str) -> int:
    query_count = 0

    def _count(*_args, **_kwargs):
        nonlocal query_count
        query_count += 1

    with SessionLocal() as db:
        leader_id = db.scalar(select(User.id).where(User.email == leader_email))
        event.listen(engine, "before_cursor_execute", _count)
        try:
            workforce_service.list_user_directory(db, leader_id, page_size=100)
            workforce_service.directory_summary(db, leader_id)
        finally:
            event.remove(engine, "before_cursor_execute", _count)
    return query_count


def test_directory_query_count_does_not_scale_with_row_count():
    """Plan 12: role/team/activation-state lookups must be set-based, not one
    query per row. Rather than assert an exact statement count (which is a
    driver/pooling implementation detail), compare the same query at two very
    different row counts - a real N+1 would make the larger scope cost
    noticeably more, not the same."""
    small_leader = _make_scoped_team_with_agents("adminusrqcsmall", 3)
    large_leader = _make_scoped_team_with_agents("adminusrqclarge", 30)

    small_count = _query_count_for_directory(small_leader)
    large_count = _query_count_for_directory(large_leader)

    assert small_count == large_count, (
        f"query count grew from {small_count} (3 rows) to {large_count} (30 rows) - "
        "the directory is issuing at least one query per row somewhere"
    )


# --- Phase B: detail page and credential-administration actions --------------------


def _super_admin(prefix: str) -> tuple[TestClient, uuid.UUID]:
    email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
    actor_id = make_user_with_role(
        email, "super_admin", scope_type="installation", scope_id=None
    )
    return _client_for(email), actor_id


def test_detail_page_hides_security_panel_without_reset_capability():
    team = _make_team("adminusrdetnocap")
    target_id = _make_bare_user("adminusrdetnocaptarget")
    _add_membership(team, target_id)
    leader = f"adminusrdetnocaptl-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(leader, "team_leader", scope_type="team", scope_id=team)
    client = _client_for(leader)

    resp = client.get(f"/admin/users/{target_id}")
    assert resp.status_code == 200
    assert "Reset password" not in resp.text
    assert "Reset MFA" not in resp.text


def test_detail_page_same_response_for_out_of_scope_and_nonexistent():
    team_a = _make_team("adminusrdetscopea")
    team_b = _make_team("adminusrdetscopeb")
    other_user = _make_bare_user("adminusrdetscopeother")
    _add_membership(team_b, other_user)
    leader = f"adminusrdetscopetl-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(leader, "team_leader", scope_type="team", scope_id=team_a)
    client = _client_for(leader)

    out_of_scope = client.get(f"/admin/users/{other_user}")
    nonexistent = client.get(f"/admin/users/{uuid.uuid4()}")
    assert out_of_scope.status_code == nonexistent.status_code == 303
    assert out_of_scope.headers["location"] == nonexistent.headers["location"]


def test_super_admin_can_issue_activation_code_after_reauthentication():
    target_id = _make_bare_user("adminusrissue")
    client, actor_id = _super_admin("adminusrissuesa")

    detail = client.get(f"/admin/users/{target_id}")
    assert detail.status_code == 200
    assert "Issue code" in detail.text

    _reauthenticate(client)
    resp = client.post(
        f"/admin/users/{target_id}/activation-code", data={"csrf_token": _csrf(client)}
    )
    assert resp.status_code == 200, resp.text
    assert "will not be shown again" in resp.text

    with SessionLocal() as db:
        token_row = db.scalar(
            select(ActivationToken).where(
                ActivationToken.user_id == target_id, ActivationToken.used_at.is_(None)
            )
        )
        assert token_row is not None
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "admin.issue_activation_code",
                AuditEvent.target_id == target_id,
            )
        )
        assert audit is not None
        assert audit.actor_user_id == actor_id
        # Plan 9.12: no secret in audit metadata or reason.
        serialized = f"{audit.event_metadata} {audit.reason_code}"
        assert token_row.token_hash not in serialized


def test_issuing_activation_code_requires_recent_reauthentication():
    """Login itself sets reauthenticated_at (you just proved your identity), so
    a fresh session already passes the step-up check - this must age it past
    the step-up window first, the same way test_auth_flow.py's API-level
    equivalent does."""
    target_id = _make_bare_user("adminusrissuenoreauth")
    client, _ = _super_admin("adminusrissuenoreauthsa")

    session_token = client.cookies.get("cc_session")
    assert session_token
    with SessionLocal() as db:
        session_row = db.scalar(
            select(SessionModel).where(SessionModel.token_hash == hash_token(session_token))
        )
        assert session_row is not None
        session_row.reauthenticated_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()

    resp = client.post(
        f"/admin/users/{target_id}/activation-code", data={"csrf_token": _csrf(client)}
    )
    assert resp.status_code == 303
    assert "flash_error" in resp.headers["location"]

    with SessionLocal() as db:
        assert (
            db.scalar(select(ActivationToken).where(ActivationToken.user_id == target_id))
            is None
        )


def test_issue_activation_code_rejected_for_already_activated_user():
    target_id = make_user(f"adminusralreadyactive-{uuid.uuid4().hex[:8]}@example.com")
    client, _ = _super_admin("adminusralreadysa")
    _reauthenticate(client)

    resp = client.post(
        f"/admin/users/{target_id}/activation-code", data={"csrf_token": _csrf(client)}
    )
    assert resp.status_code == 303
    assert "flash_error" in resp.headers["location"]


def test_reset_password_revokes_sessions_and_issues_new_code():
    from app.main import app

    target_email = f"adminusrresetpw-{uuid.uuid4().hex[:8]}@example.com"
    target_id = make_user(target_email)
    target_client = TestClient(app, follow_redirects=False)
    login(target_client, target_email)
    target_session_token = target_client.cookies.get("cc_session")
    assert target_session_token

    admin_client, _ = _super_admin("adminusrresetpwsa")
    _reauthenticate(admin_client)
    resp = admin_client.post(
        f"/admin/users/{target_id}/reset-password", data={"csrf_token": _csrf(admin_client)}
    )
    assert resp.status_code == 200, resp.text

    with SessionLocal() as db:
        target = db.get(User, target_id)
        assert target is not None
        assert target.password_hash is None

        session_row = db.scalar(
            select(SessionModel).where(
                SessionModel.token_hash == hash_token(target_session_token)
            )
        )
        assert session_row is not None
        assert session_row.revoked_at is not None

        new_token_row = db.scalar(
            select(ActivationToken).where(
                ActivationToken.user_id == target_id, ActivationToken.used_at.is_(None)
            )
        )
        assert new_token_row is not None

        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "admin.reset_password", AuditEvent.target_id == target_id
            )
        )
        assert audit is not None


def test_reset_mfa_clears_enrollment_and_revokes_sessions():
    from app.main import app

    target_email = f"adminusrresetmfa-{uuid.uuid4().hex[:8]}@example.com"
    target_id = make_user(target_email)
    target_client = TestClient(app, follow_redirects=False)
    login(target_client, target_email)
    target_session_token = target_client.cookies.get("cc_session")
    assert target_session_token

    admin_client, _ = _super_admin("adminusrresetmfasa")
    _reauthenticate(admin_client)
    resp = admin_client.post(
        f"/admin/users/{target_id}/reset-mfa", data={"csrf_token": _csrf(admin_client)}
    )
    assert resp.status_code == 303
    assert "flash_success" in resp.headers["location"]

    with SessionLocal() as db:
        target = db.get(User, target_id)
        assert target is not None
        assert target.totp_enrolled is False
        assert target.totp_secret_ciphertext is None

        session_row = db.scalar(
            select(SessionModel).where(
                SessionModel.token_hash == hash_token(target_session_token)
            )
        )
        assert session_row is not None
        assert session_row.revoked_at is not None

        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "admin.reset_2fa", AuditEvent.target_id == target_id
            )
        )
        assert audit is not None


def test_manager_without_reset_capability_cannot_act_via_forged_post():
    """Plan 9.1/9.5: viewing the directory (a workforce-appointment capability)
    must not imply authority to act on credentials (RESET_USER_AUTH) - and a
    hidden button is not the enforcement, the server check is."""
    target_id = _make_bare_user("adminusrmgrforge")
    manager_email = f"adminusrmgrforgemgr-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(manager_email, "manager")
    client = _client_for(manager_email)
    _reauthenticate(client)

    detail = client.get(f"/admin/users/{target_id}")
    assert detail.status_code == 200
    assert "Reset password" not in detail.text

    resp = client.post(
        f"/admin/users/{target_id}/activation-code", data={"csrf_token": _csrf(client)}
    )
    assert resp.status_code == 303
    assert "flash_error" in resp.headers["location"]

    with SessionLocal() as db:
        assert (
            db.scalar(select(ActivationToken).where(ActivationToken.user_id == target_id))
            is None
        )


def test_self_credential_actions_are_blocked():
    client, actor_id = _super_admin("adminusrself")
    _reauthenticate(client)

    detail = client.get(f"/admin/users/{actor_id}")
    assert detail.status_code == 200
    assert "cannot use it on your own account" in detail.text

    resp = client.post(
        f"/admin/users/{actor_id}/reset-password", data={"csrf_token": _csrf(client)}
    )
    assert resp.status_code == 303
    assert "flash_error" in resp.headers["location"]

    with SessionLocal() as db:
        actor = db.get(User, actor_id)
        assert actor is not None
        assert actor.password_hash is not None


def test_reissuing_activation_code_invalidates_the_previous_one():
    """Plan 9.11/11.3: issuing a new code must leave exactly the newest token
    usable. Two truly concurrent requests are serialized by issue_activation_
    token's row lock down to this same sequence, so proving it here proves the
    concurrent case too."""
    target_id = _make_bare_user("adminusrconcurrent")
    _, actor_id = _super_admin("adminusrconcurrentsa")

    with SessionLocal() as db:
        target = db.get(User, target_id)
        assert target is not None
        first_token, _ = auth_service.issue_or_replace_activation_code(
            db, target, actor_id=actor_id
        )
        db.commit()

    with SessionLocal() as db:
        target = db.get(User, target_id)
        assert target is not None
        second_token, _ = auth_service.issue_or_replace_activation_code(
            db, target, actor_id=actor_id
        )
        db.commit()

    with SessionLocal() as db:
        assert auth_service.consume_activation_token(db, first_token) is None
        assert auth_service.consume_activation_token(db, second_token) == target_id
        db.commit()


def test_reset_password_and_reset_mfa_reject_a_disabled_account():
    target_id = make_user(f"adminusrdisabledreset-{uuid.uuid4().hex[:8]}@example.com")
    with SessionLocal() as db:
        target = db.get(User, target_id)
        assert target is not None
        target.active = False
        db.commit()

    client, actor_id = _super_admin("adminusrdisabledsa")
    with SessionLocal() as db:
        target = db.get(User, target_id)
        assert target is not None
        for action in (auth_service.reset_password, auth_service.reset_mfa):
            with pytest.raises(auth_service.AccountNotEligible):
                action(db, target, actor_id=actor_id)
