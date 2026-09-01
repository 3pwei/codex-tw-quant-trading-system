#!/usr/bin/env bash
set -euo pipefail

REPOSITORY_URL="${1:-https://github.com/3pwei/codex-tw-quant-trading-system.git}"
DEPLOY_BRANCH="${2:-master}"
INSTALL_ROOT="/opt/tw-quant"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo bash deploy/lightsail/bootstrap.sh" >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2 git ca-certificates curl
systemctl enable --now docker

install -d -m 0755 "${INSTALL_ROOT}" "${INSTALL_ROOT}/config"
if [[ ! -d "${INSTALL_ROOT}/repo/.git" ]]; then
  git clone --branch "${DEPLOY_BRANCH}" "${REPOSITORY_URL}" "${INSTALL_ROOT}/repo"
fi

for file in market gateway compose; do
  target="${INSTALL_ROOT}/config/${file}.env"
  source="${INSTALL_ROOT}/repo/deploy/lightsail/${file}.env.example"
  if [[ ! -e "${target}" ]]; then
    install -m 0600 "${source}" "${target}"
  fi
done

echo "Bootstrap complete. Edit /opt/tw-quant/config/*.env, then run:"
echo "sudo docker compose --env-file /opt/tw-quant/config/compose.env -f /opt/tw-quant/repo/deploy/lightsail/docker-compose.yml up --build -d"
