# Running the stack (build host)

Prerequisites: a Linux LTS host with Docker and the Compose plugin (D-03, ADR-003).
Run each command on its own.

## 1. Configure

    cp .env.example .env

Edit `.env`: set `SERVER_HOST` to the server's LAN IP, and review the non-secret values.

## 2. Create secret files

    openssl rand -base64 32 > deploy/secrets/db_password

    openssl rand -base64 48 > deploy/secrets/app_secret_key

    openssl rand -base64 48 > deploy/secrets/field_encryption_key

    openssl rand -base64 48 > deploy/secrets/phone_fingerprint_hmac_key

Never commit `.env` or the real secret files. Keep the field-encryption and
phone-fingerprint keys in separate custody.

## 3. Generate the dependency lock (once, needs PyPI access)

    bash scripts/lock.sh

## 4. Build and migrate

    docker compose build

    docker compose up -d postgres redis

    docker compose run --rm web alembic upgrade head

## 5. Start the app

    docker compose up -d web caddy

## 6. Verify

    docker compose ps

From a laptop on the LAN, browse to `https://SERVER_HOST`.

## 7. Certificate trust for unmanaged laptops (ADR-003)

Caddy uses its internal CA. Export the root once:

    docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt

Distribute `caddy-root.crt` and have each user install it in the OS or browser trust
store. Without it, the first HTTPS connection shows a warning.

## Ports

Only Caddy publishes 80 and 443 on the LAN. PostgreSQL and Redis are on an internal
network with no host ports.
