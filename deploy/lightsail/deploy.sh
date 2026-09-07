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
    exec -T -e DEPLOY_COMMIT_SHA="${COMMIT_SHA}" market-api python - <<'PY'
import os
import sqlite3
from pathlib import Path

source_path = Path(os.environ.get("MARKET_DB_PATH", "/data/live_market.sqlite3"))
if source_path.exists():
    revision = os.environ["DEPLOY_COMMIT_SHA"][:12]
    backup_path = source_path.with_name(
        f"{source_path.stem}.backup-{revision}{source_path.suffix}"
    )
    source = sqlite3.connect(source_path)
    backup = sqlite3.connect(backup_path)
    try:
        source.backup(backup)
        result = backup.execute("PRAGMA integrity_check").fetchone()
        if result != ("ok",):
            raise RuntimeError(f"SQLite backup integrity check failed: {result!r}")
    finally:
        backup.close()
        source.close()
    print(f"SQLite backup created and verified: {backup_path}")
PY
fi

"${REPOSITORY}/deploy/lightsail/prepare-host.sh"

docker compose \
  --env-file "${COMPOSE_ENV}" \
  -f "${COMPOSE_FILE}" \
  build

# Validate the target image with the server's real environment before replacing
# the currently healthy containers. A missing production auth setting must stop
# the deployment instead of silently enabling the local-development bypass.
docker compose \
  --env-file "${COMPOSE_ENV}" \
  -f "${COMPOSE_FILE}" \
  run --rm --no-deps market-api python -c \
  'from tw_quant.live.settings import LiveSettings; LiveSettings.from_env().validate()'

docker compose \
  --env-file "${COMPOSE_ENV}" \
  -f "${COMPOSE_FILE}" \
  up --no-build --detach --remove-orphans

docker compose \
  --env-file "${COMPOSE_ENV}" \
  -f "${COMPOSE_FILE}" \
  ps
