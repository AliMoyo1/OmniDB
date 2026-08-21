# Deployment secrets

Real secret files live here at deploy time and are git-ignored. Only the `*.example`
templates are committed. Compose mounts each file to `/run/secrets/<name>`, and the
app reads it by field name (see `app/config.py`).

## Create the real secrets

For each `*.example` file, create a file with the same name minus `.example`, holding a
strong random value. For example:

    openssl rand -base64 48 > deploy/secrets/app_secret_key
    openssl rand -base64 48 > deploy/secrets/field_encryption_key
    openssl rand -base64 48 > deploy/secrets/phone_fingerprint_hmac_key
    openssl rand -base64 48 > deploy/secrets/health_token
    openssl rand -base64 32 > deploy/secrets/db_password

Never commit the real files. Keep the field-encryption and phone-fingerprint keys in
separate custody, and back them up separately from the data (plan 9.5).
