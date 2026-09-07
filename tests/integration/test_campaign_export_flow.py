"""Integration tests for the completed-campaign Excel export (ADR-020,
increment B). Real Postgres.
"""

from __future__ import annotations

import io
import uuid
from datetime import UTC, datetime

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.campaigns import retention
from app.db import SessionLocal
from app.models.audit import AuditEvent
from app.models.campaign import Campaign, CampaignDispositionDefinition
from app.models.contact import CampaignContact, Contact
from app.models.identity import User
from app.security.encryption import encrypt
from app.security.passwords import hash_password
from tests.integration.conftest import TEST_PASSWORD, login, make_user_with_role

pytestmark = pytest.mark.integration

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _completed_campaign(*, phone: str, disposition_label: str) -> tuple[uuid.UUID, str, str]:
    """A completed, org-scoped campaign with one fully-dispositioned contact
    (real encrypted phone, agent, disposition). Returns
    (campaign_id, agent_name, phone)."""
    now = datetime.now(UTC)
    with SessionLocal() as db:
        agent = User(
            workforce_id=f"exp-agent-{uuid.uuid4().hex[:8]}",
            email=f"exp-agent-{uuid.uuid4().hex[:8]}@example.com",
            display_name="Export Agent", password_hash=hash_password("x"),
        )
        db.add(agent)
        db.flush()
        campaign = Campaign(
            owning_scope_type="organization", owning_scope_id=None,
            external_code=f"exp-{uuid.uuid4().hex[:10]}", name="Export Campaign",
            default_region="ZW", timezone="Africa/Harare", status="active",
            created_by=agent.id, launched_at=now,
        )
        db.add(campaign)
        db.flush()
        db.add(
            CampaignDispositionDefinition(
                campaign_id=campaign.id, label=disposition_label,
                stable_semantic_code="sale_closed", display_order=0, active=True,
            )
        )
        contact = Contact(phone_ciphertext=encrypt(phone), phone_fingerprint=uuid.uuid4().hex)
        db.add(contact)
        db.flush()
        db.add(
            CampaignContact(
                campaign_id=campaign.id, contact_id=contact.id, status="completed",
                imported_at=now, completed_at=now, completed_by_agent_id=agent.id,
                final_disposition_code="sale_closed",
            )
        )
        db.flush()
        retention.mark_campaign_completed(db, campaign)
        db.commit()
        return campaign.id, agent.display_name, phone


def test_completed_campaign_exports_xlsx_with_contact_rows(manager_client):
    phone = "+263771234567"
    campaign_id, agent_name, _ = _completed_campaign(
        phone=phone, disposition_label="Sale closed"
    )

    resp = manager_client.get(f"/campaigns/{campaign_id}/export")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith(_XLSX)
    assert "attachment" in resp.headers["content-disposition"]

    workbook = load_workbook(io.BytesIO(resp.content))
    sheet = workbook.active
    rows = list(sheet.iter_rows(values_only=True))
    assert rows[0] == ("Phone", "Final disposition", "Agent", "Completed at", "Imported at")
    assert len(rows) == 2
    phone_cell, disposition_cell, agent_cell = rows[1][0], rows[1][1], rows[1][2]
    # Raw number, decrypted - the audited exception, left exactly as-is (a validated
    # E.164 number is never a formula, so it is not neutralized; see the injection
    # test for the operator-controlled fields that are).
    assert phone_cell == phone
    assert disposition_cell == "Sale closed"  # the label, not the raw code
    assert agent_cell == agent_name

    # ADR-020 requires an audit record of who took raw data out of the system.
    with SessionLocal() as db:
        events = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "campaign.export",
                    AuditEvent.target_id == campaign_id,
                )
            )
        )
    assert len(events) == 1
    assert events[0].event_metadata["rows"] == 1


def test_export_neutralizes_spreadsheet_formula_injection():
    """Operator-controlled text (a disposition label, an agent display name) must
    never become a live Excel formula in this raw-PII export. openpyxl stores a
    value like "=1+1" as a FORMULA, which would run when the Team Captain opens the
    file, so every exported cell is neutralized (prefixed with "'") before writing."""
    from app.campaigns import export

    now = datetime.now(UTC)
    dangerous = ["=1+2", "+1+2", "-1+2", "@A1", "\tx", "\ry"]
    with SessionLocal() as db:
        agent = User(
            workforce_id=f"inj-agent-{uuid.uuid4().hex[:8]}",
            email=f"inj-agent-{uuid.uuid4().hex[:8]}@example.com",
            display_name="=DANGER()", password_hash=hash_password("x"),
        )
        db.add(agent)
        db.flush()
        campaign = Campaign(
            owning_scope_type="organization", owning_scope_id=None,
            external_code=f"inj-{uuid.uuid4().hex[:10]}", name="Injection Campaign",
            default_region="ZW", timezone="Africa/Harare", status="active",
            created_by=agent.id, launched_at=now,
        )
        db.add(campaign)
        db.flush()
        for i, label in enumerate(dangerous):
            db.add(
                CampaignDispositionDefinition(
                    campaign_id=campaign.id, label=label,
                    stable_semantic_code=f"code_{i}", display_order=i, active=True,
                )
            )
            contact = Contact(
                phone_ciphertext=encrypt(f"+26377{i:07d}"), phone_fingerprint=uuid.uuid4().hex
            )
            db.add(contact)
            db.flush()
            db.add(
                CampaignContact(
                    campaign_id=campaign.id, contact_id=contact.id, status="completed",
                    imported_at=now, completed_at=now, completed_by_agent_id=agent.id,
                    final_disposition_code=f"code_{i}",
                )
            )
        db.flush()
        retention.mark_campaign_completed(db, campaign)
        db.commit()
        campaign_id = campaign.id

    with SessionLocal() as db:
        loaded = db.get(Campaign, campaign_id)
        assert loaded is not None
        data = export.build_export_workbook(db, loaded)

    sheet = load_workbook(io.BytesIO(data)).active
    # Not one exported cell is stored as a live formula.
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            assert cell.data_type != "f", f"cell stored as a formula: {cell.value!r}"

    # Every dangerous disposition label is neutralized with a leading "'", so the
    # original prefix now sits at index 1. (The xlsx round-trip rewrites a carriage
    # return as a newline, so accept either for that one.)
    disposition_cells = [row[1].value for row in sheet.iter_rows(min_row=2)]
    assert all(v.startswith("'") for v in disposition_cells), disposition_cells
    guarded_prefixes = {v[1] for v in disposition_cells}
    assert {"=", "+", "-", "@", "\t"}.issubset(guarded_prefixes)
    assert "\r" in guarded_prefixes or "\n" in guarded_prefixes
    # The malicious agent display name is neutralized too.
    assert {row[2].value for row in sheet.iter_rows(min_row=2)} == {"'=DANGER()"}
    # The validated E.164 phone is deliberately left bare (it is never a formula),
    # so the numbers stay clean and reusable rather than carrying a leading "'".
    phone_cells = [row[0].value for row in sheet.iter_rows(min_row=2)]
    assert all(v.startswith("+") for v in phone_cells), phone_cells


def test_active_campaign_export_is_refused(manager_client):
    """Only a completed campaign's data may be exported; an active one still
    holds live data no raw-export path may touch."""
    import re

    # An active org-scoped campaign the manager can see, created via the form.
    from tests.integration.test_web_campaign_operations import _create_via_form

    campaign_id = _create_via_form(manager_client)
    resp = manager_client.get(f"/campaigns/{campaign_id}/export", follow_redirects=False)
    assert resp.status_code == 303
    assert re.search(r"/campaigns/[0-9a-f-]+\?flash_error=", resp.headers["location"])


def test_export_requires_the_export_capability():
    """A user without the export capability (an agent) cannot export, even a
    completed campaign."""
    from fastapi.testclient import TestClient

    from app.main import app

    campaign_id, _, _ = _completed_campaign(phone="+263772222222", disposition_label="X")
    agent_email = f"exp-noauth-{uuid.uuid4().hex[:8]}@example.com"
    make_user_with_role(agent_email, "agent")
    client = TestClient(app, follow_redirects=False)
    login(client, agent_email, TEST_PASSWORD)

    resp = client.get(f"/campaigns/{campaign_id}/export")
    # Redirected away (to the campaigns index) rather than served the file.
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/campaigns?flash_error=")
