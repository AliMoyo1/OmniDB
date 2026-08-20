#!/usr/bin/env bash
set -euo pipefail
# Emit a release manifest for traceability (plan 14.3): source commit, migrations,
# image digest (if provided), and build time. Redirect to a file to record a release.

commit="$(git rev-parse HEAD)"
short="$(git rev-parse --short HEAD)"
migrations="$(ls migrations/versions/*.py 2>/dev/null | xargs -n1 basename 2>/dev/null | sed 's/\.py$//' | paste -sd, -)"
built_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
image="${IMAGE_DIGEST:-unset}"

cat <<EOF
{
  "product": "CipherContact",
  "commit": "$commit",
  "short_commit": "$short",
  "migrations": "$migrations",
  "image_digest": "$image",
  "built_at": "$built_at"
}
EOF
