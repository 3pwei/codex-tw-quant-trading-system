#!/usr/bin/env bash
set -euo pipefail

COMMIT_SHA="${1:?commit SHA is required}"
INSTALL_ROOT="/opt/tw-quant"
REPOSITORY="${INSTALL_ROOT}/repo"
COMPOSE_FILE="${REPOSITORY}/deploy/lightsail/docker-compose.yml"
COMPOSE_ENV="${INSTALL_ROOT}/config/compose.env"
EXECUTION_ENV="${INSTALL_ROOT}/config/execution.env"

if [[ ! "${COMMIT_SHA}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Invalid commit SHA" >&2
  exit 2
fi

if ! git -C "${REPOSITORY}" cat-file -e "${COMMIT_SHA}^{commit}"; then
  echo "Approved commit is not staged in the deployment repository" >&2
  exit 3
fi
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

if [[ ! -f "${EXECUTION_ENV}" || "$(stat -c '%a' "${EXECUTION_ENV}")" != "600" ]]; then
  echo "execution.env must exist as a regular file with mode 600" >&2
  exit 4
fi
if find "${INSTALL_ROOT}/secrets" -maxdepth 1 -type f -perm /077 \
  -print -quit | grep -q .; then
  echo "execution secret files must not grant group or other permissions" >&2
  exit 4
fi

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
  run --rm --no-deps -T market-api python -c \
  'from tw_quant.live.settings import LiveSettings; LiveSettings.from_env().validate()'

# Validate the isolated execution configuration. Locked/disabled is a valid
# service state; this command never logs in, activates a CA, or submits orders.
docker compose \
  --env-file "${COMPOSE_ENV}" \
  -f "${COMPOSE_FILE}" \
  run --rm --no-deps -T execution-worker \
  python -m tw_quant.execution_service validate

docker compose \
  --env-file "${COMPOSE_ENV}" \
  -f "${COMPOSE_FILE}" \
  up --no-build --detach --remove-orphans --force-recreate

for service in market-api execution-worker gateway; do
  built_image="$(docker compose \
    --env-file "${COMPOSE_ENV}" \
    -f "${COMPOSE_FILE}" images -q "${service}")"
  container_id="$(docker compose \
    --env-file "${COMPOSE_ENV}" \
    -f "${COMPOSE_FILE}" ps -q "${service}")"
  if [[ -z "${built_image}" || -z "${container_id}" ]]; then
    echo "${service} image or running container is missing" >&2
    exit 1
  fi
  expected_image="$(docker image inspect --format '{{.Id}}' "${built_image}")"
  running_image="$(docker inspect --format '{{.Image}}' "${container_id}")"
  if [[ "${running_image}" != "${expected_image}" ]]; then
    echo "${service} is not running the newly built image" >&2
    exit 1
  fi
done

execution_container="$(docker compose \
  --env-file "${COMPOSE_ENV}" \
  -f "${COMPOSE_FILE}" ps -q execution-worker)"
published_ports="$(docker inspect --format '{{json .NetworkSettings.Ports}}' \
  "${execution_container}")"
if [[ "${published_ports}" != "{}" ]]; then
  echo "execution-worker unexpectedly exposes a network port" >&2
  exit 1
fi

docker compose \
  --env-file "${COMPOSE_ENV}" \
  -f "${COMPOSE_FILE}" \
  ps
