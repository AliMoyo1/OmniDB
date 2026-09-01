"""Request and response models for the auth API."""

from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    email: str
    password: str
    totp_code: str | None = None


class ReauthRequest(BaseModel):
    password: str
    totp_code: str | None = None


class ActivateRequest(BaseModel):
    token: str
    new_password: str


class TotpVerifyRequest(BaseModel):
    code: str


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    workforce_id: str
    mfa_enrollment_required: bool = False


class TotpEnrollOut(BaseModel):
    secret: str
    provisioning_uri: str
