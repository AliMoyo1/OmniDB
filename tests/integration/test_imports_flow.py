"""Integration tests for the campaign import pipeline (real Postgres + Redis).

Celery runs in eager (synchronous) mode for tests (see tests/conftest.py), so parsing
completes before the upload endpoint returns control, though the task uses its own DB
session, so status is re-fetched via GET rather than trusted from the upload response.
"""

from __future__ import annotations

import io
import uuid
import zipfile

import phonenumbers
import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models.base import utcnow
from app.models.contact import SuppressionEntry
from app.security.phone import protect
from tests.integration.conftest import csrf_headers

pytestmark = pytest.mark.integration

_DEFAULT_PROVENANCE = {
    "purpose": "Customer outreach",
    "data_source": "CRM export",
    "data_obtained_at": "2026-01-01",
    "lawful_basis_or_consent_reference": "consent-ref-123",
}


def _zw_number() -> str:
    example = phonenumbers.example_number("ZW")
    if example is None:
        pytest.skip("no example number available for region ZW")
    return str(example.national_number)


def _create_campaign(client: TestClient, headers: dict, **overrides) -> str:
    payload = {
        "name": f"Test campaign {uuid.uuid4().hex[:6]}",
        "owning_scope_type": "organization",
        "default_region": "ZW",
        "timezone": "Africa/Harare",
        **_DEFAULT_PROVENANCE,
        **overrides,
    }
    resp = client.post("/api/v1/campaigns", json=payload, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _upload(client: TestClient, headers: dict, campaign_id: str, csv_text: str, filename="c.csv"):
    files = {"file": (filename, csv_text.encode("utf-8"), "text/csv")}
    data = {"phone_column": "phone", "name_column": "name", "metadata_columns": ""}
    return client.post(
        f"/api/v1/campaigns/{campaign_id}/imports", files=files, data=data, headers=headers
    )


def test_full_import_flow_classifies_commits_and_replays_idempotently(manager_client):
    client = manager_client
    headers = csrf_headers(client)
    campaign_id = _create_campaign(client, headers)

    n1 = _zw_number()
    csv_text = f"phone,name\n{n1},Alice\n{n1},AliceDuplicate\nnot-a-number,Bad\n"
    upload_resp = _upload(client, headers, campaign_id, csv_text)
    assert upload_resp.status_code == 200, upload_resp.text
    job_id = upload_resp.json()["id"]

    status_resp = client.get(f"/api/v1/imports/{job_id}")
    job = status_resp.json()
    assert job["state"] == "parsed", job
    assert job["total_rows"] == 3
    assert job["valid_rows"] == 1
    assert job["duplicate_rows"] == 1
    assert job["invalid_rows"] == 1

    preview = client.get(f"/api/v1/imports/{job_id}/preview").json()
    assert len(preview["invalid_examples"]) == 1
    assert preview["invalid_examples"][0]["row_number"] == 3

    decide = client.patch(
        f"/api/v1/imports/{job_id}/decisions", json={"decision": "approve"}, headers=headers
    )
    assert decide.status_code == 200, decide.text
    decision_version = decide.json()["decision_version"]

    idem_key = str(uuid.uuid4())
    commit1 = client.post(
        f"/api/v1/imports/{job_id}/commit",
        json={"decision_version": decision_version, "idempotency_key": idem_key},
        headers=headers,
    )
    assert commit1.status_code == 200, commit1.text
    result1 = commit1.json()
    assert result1["inserted"] == 1
    assert result1["suppressed"] == 0

    # Repeating the same idempotency key returns the same result, not a second insert.
    commit2 = client.post(
        f"/api/v1/imports/{job_id}/commit",
        json={"decision_version": decision_version, "idempotency_key": idem_key},
        headers=headers,
    )
    assert commit2.status_code == 200
    assert commit2.json() == result1

    launch = client.post(f"/api/v1/campaigns/{campaign_id}/launch", headers=headers)
    assert launch.status_code == 200, launch.text
    assert launch.json()["status"] == "active"


def test_suppression_added_after_preview_is_excluded_at_commit(manager_client):
    client = manager_client
    headers = csrf_headers(client)
    campaign_id = _create_campaign(client, headers)

    n = _zw_number()
    upload_resp = _upload(client, headers, campaign_id, f"phone,name\n{n},Bob\n")
    job_id = upload_resp.json()["id"]

    job = client.get(f"/api/v1/imports/{job_id}").json()
    assert job["suppression_hits"] == 0  # not suppressed when parsed

    # A DNC request arrives after the preview was shown to the uploader.
    protected = protect(n, "ZW")
    with SessionLocal() as db:
        db.add(
            SuppressionEntry(
                phone_fingerprint=protected.fingerprint,
                protected_phone_value=protected.ciphertext,
                source="explicit_contact_request",
                effective_at=utcnow(),
                status="active",
            )
        )
        db.commit()

    decide = client.patch(
        f"/api/v1/imports/{job_id}/decisions", json={"decision": "approve"}, headers=headers
    )
    decision_version = decide.json()["decision_version"]
    commit = client.post(
        f"/api/v1/imports/{job_id}/commit",
        json={"decision_version": decision_version, "idempotency_key": str(uuid.uuid4())},
        headers=headers,
    )
    assert commit.status_code == 200, commit.text
    result = commit.json()
    assert result["inserted"] == 1
    assert result["suppressed"] == 1


def test_commit_without_approval_is_rejected(manager_client):
    client = manager_client
    headers = csrf_headers(client)
    campaign_id = _create_campaign(client, headers)
    n = _zw_number()
    upload_resp = _upload(client, headers, campaign_id, f"phone,name\n{n},Carl\n")
    job_id = upload_resp.json()["id"]

    commit = client.post(
        f"/api/v1/imports/{job_id}/commit",
        json={"decision_version": 0, "idempotency_key": str(uuid.uuid4())},
        headers=headers,
    )
    assert commit.status_code == 409


def test_stale_decision_version_is_rejected(manager_client):
    client = manager_client
    headers = csrf_headers(client)
    campaign_id = _create_campaign(client, headers)
    n = _zw_number()
    upload_resp = _upload(client, headers, campaign_id, f"phone,name\n{n},Dora\n")
    job_id = upload_resp.json()["id"]

    client.patch(
        f"/api/v1/imports/{job_id}/decisions", json={"decision": "approve"}, headers=headers
    )

    commit = client.post(
        f"/api/v1/imports/{job_id}/commit",
        json={"decision_version": 999, "idempotency_key": str(uuid.uuid4())},
        headers=headers,
    )
    assert commit.status_code == 409
    assert commit.json()["detail"]["code"] == "stale_version"


def test_cannot_launch_without_committed_contacts(manager_client):
    client = manager_client
    headers = csrf_headers(client)
    campaign_id = _create_campaign(client, headers)
    resp = client.post(f"/api/v1/campaigns/{campaign_id}/launch", headers=headers)
    assert resp.status_code == 409


def test_cannot_launch_without_provenance(manager_client):
    client = manager_client
    headers = csrf_headers(client)
    campaign_id = _create_campaign(
        client, headers, data_source=None, purpose=None,
        data_obtained_at=None, lawful_basis_or_consent_reference=None,
    )
    resp = client.post(f"/api/v1/campaigns/{campaign_id}/launch", headers=headers)
    assert resp.status_code == 409


def test_csv_upload_with_zip_signature_is_rejected(manager_client):
    client = manager_client
    headers = csrf_headers(client)
    campaign_id = _create_campaign(client, headers)
    files = {"file": ("fake.csv", b"PK\x03\x04not-really-a-csv", "text/csv")}
    data = {"phone_column": "phone", "metadata_columns": ""}
    resp = client.post(
        f"/api/v1/campaigns/{campaign_id}/imports", files=files, data=data, headers=headers
    )
    assert resp.status_code == 400


def test_macro_enabled_xlsx_is_rejected(manager_client):
    client = manager_client
    headers = csrf_headers(client)
    campaign_id = _create_campaign(client, headers)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("xl/workbook.xml", "<workbook/>")
        zf.writestr("xl/vbaProject.bin", b"fake-macro-bytes")
    files = {
        "file": (
            "evil.xlsx",
            buf.getvalue(),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    }
    data = {"phone_column": "phone", "metadata_columns": ""}
    resp = client.post(
        f"/api/v1/campaigns/{campaign_id}/imports", files=files, data=data, headers=headers
    )
    assert resp.status_code == 400
    assert "macro" in resp.text.lower()


def test_wrong_phone_column_fails_fast(manager_client):
    client = manager_client
    headers = csrf_headers(client)
    campaign_id = _create_campaign(client, headers)
    n = _zw_number()
    files = {"file": ("c.csv", f"mobile,name\n{n},Eve\n".encode(), "text/csv")}
    data = {"phone_column": "phone", "metadata_columns": ""}  # header has "mobile", not "phone"
    resp = client.post(
        f"/api/v1/campaigns/{campaign_id}/imports", files=files, data=data, headers=headers
    )
    assert resp.status_code == 200
    job_id = resp.json()["id"]
    job = client.get(f"/api/v1/imports/{job_id}").json()
    assert job["state"] == "failed"


def test_disposition_cannot_claim_dnc_with_unapproved_code(manager_client):
    client = manager_client
    headers = csrf_headers(client)
    campaign_id = _create_campaign(client, headers)
    resp = client.post(
        f"/api/v1/campaigns/{campaign_id}/dispositions",
        json={
            "label": "Custom do-not-call",
            "stable_semantic_code": "custom_dnc_hack",
            "causes_dnc": True,
        },
        headers=headers,
    )
    assert resp.status_code == 400


def test_disposition_approved_dnc_code_is_allowed(manager_client):
    client = manager_client
    headers = csrf_headers(client)
    campaign_id = _create_campaign(client, headers)
    resp = client.post(
        f"/api/v1/campaigns/{campaign_id}/dispositions",
        json={
            "label": "Customer requested no further contact",
            "stable_semantic_code": "explicit_dnc",
            "causes_dnc": True,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["causes_dnc"] is True


def test_protected_dnc_code_cannot_disable_suppression(manager_client):
    client = manager_client
    headers = csrf_headers(client)
    campaign_id = _create_campaign(client, headers)
    resp = client.post(
        f"/api/v1/campaigns/{campaign_id}/dispositions",
        json={
            "label": "Misconfigured DNC",
            "stable_semantic_code": "explicit_dnc",
            "causes_dnc": False,
        },
        headers=headers,
    )
    assert resp.status_code == 400


def test_agent_cannot_create_campaign(agent_client):
    client = agent_client
    resp = client.post(
        "/api/v1/campaigns",
        json={"name": "x", "default_region": "ZW", "timezone": "Africa/Harare"},
        headers=csrf_headers(client),
    )
    assert resp.status_code == 403
