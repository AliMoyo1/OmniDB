from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_mfa_template_keeps_secrets_out_of_urls_and_browser_storage():
    template = (ROOT / "app" / "templates" / "security_mfa.html").read_text(
        encoding="utf-8"
    )

    assert 'action="/security/mfa/start"' in template
    assert 'action="/security/mfa/verify"' in template
    assert 'name="csrf_token"' in template
    assert "enrollment_secret_display" in template
    assert "provisioning_uri" not in template
    assert "otpauth://" not in template
    assert "localStorage" not in template
    assert "sessionStorage" not in template
    assert "innerHTML" not in template


def test_mfa_page_is_desktop_first_with_a_responsive_fallback():
    css = (ROOT / "app" / "static" / "css" / "base.css").read_text(encoding="utf-8")

    assert ".security-grid" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css
    assert "max-width: 1180px" in css
    assert "@media (max-width: 900px)" in css
    assert ".security-verify-card { grid-column: 1 / -1; }" in css


def test_base_navigation_exposes_account_security_only_after_enrollment():
    template = (ROOT / "app" / "templates" / "base.html").read_text(encoding="utf-8")

    assert "{% if user and not mfa_enrollment_required %}" in template
    assert 'href="/security/mfa"' in template
    assert "Account security" in template
