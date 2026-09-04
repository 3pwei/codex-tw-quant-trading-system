#!/usr/bin/env bash
set -euo pipefail

COMMIT_SHA="${1:?commit SHA is required}"
INSTALL_ROOT="/opt/tw-quant"
REPOSITORY="${INSTALL_ROOT}/repo"
COMPOSE_FILE="${REPOSITORY}/deploy/lightsail/docker-compose.yml"
COMPOSE_ENV="${INSTALL_ROOT}/config/compose.env"

if [[ ! "${COMMIT_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Invalid commit SHA" >&2
  exit 2
fi

git -C "${REPOSITORY}" fetch origin --prune
git -C "${REPOSITORY}" checkout --detach "${COMMIT_SHA}"

if docker compose --env-file "${COMPOSE_ENV}" -f "${COMPOSE_FILE}" \
  ps --status running --services | grep -qx market-api; then
  docker compose --env-file "${COMPOSE_ENV}" -f "${COMPOSE_FILE}" \
    exec -T -e DEPLOY_COMMIT_SHA="${COMMIT_SHA}" market-api python -c '
import os
import sqlite3
from pathlib import Path

source_path = Path(os.environ.get("MARKET_DB_PATH", "/data/live_market.sqlite3"))
if source_path.exists():
    backup_path = source_path.with_name(
        f"{source_path.stem}.backup-{os.environ[\"DEPLOY_COMMIT_SHA\"][:12]}"
        f"{source_path.suffix}"
    )
    source = sqlite3.connect(source_path)
    backup = sqlite3.connect(backup_path)
    try:
        source.backup(backup)
    finally:
        backup.close()
        source.close()
'
fi

"${REPOSITORY}/deploy/lightsail/prepare-host.sh"

docker compose \
  --env-file "${COMPOSE_ENV}" \
  -f "${COMPOSE_FILE}" \
  up --build --detach --remove-orphans

docker compose \
  --env-file "${COMPOSE_ENV}" \
  -f "${COMPOSE_FILE}" \
  ps
