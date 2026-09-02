#!/usr/bin/env bash
set -euo pipefail

COMMIT_SHA="${1:?commit SHA is required}"
INSTALL_ROOT="/opt/tw-quant"
REPOSITORY="${INSTALL_ROOT}/repo"

if [[ ! "${COMMIT_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Invalid commit SHA" >&2
  exit 2
fi

git -C "${REPOSITORY}" fetch origin --prune
git -C "${REPOSITORY}" checkout --detach "${COMMIT_SHA}"

docker compose \
  --env-file "${INSTALL_ROOT}/config/compose.env" \
  -f "${REPOSITORY}/deploy/lightsail/docker-compose.yml" \
  up --build --detach --remove-orphans

docker compose \
  --env-file "${INSTALL_ROOT}/config/compose.env" \
  -f "${REPOSITORY}/deploy/lightsail/docker-compose.yml" \
  ps
