#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="${INSTALL_ROOT:-/opt/tw-quant}"
EXECUTION_ENV="${INSTALL_ROOT}/config/execution.env"
MARKET_ENV="${INSTALL_ROOT}/config/market.env"
SWAP_FILE="${INSTALL_ROOT}/build.swap"
SWAP_SIZE="2G"
MIN_MEMORY_KIB=$((3 * 1024 * 1024))

memory_kib="$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo)"

install -d -m 0700 "${INSTALL_ROOT}/secrets"
if [[ ! -e "${EXECUTION_ENV}" ]]; then
  install -m 0600 \
    "${INSTALL_ROOT}/repo/deploy/lightsail/execution.env.example" \
    "${EXECUTION_ENV}"
fi

# PR #103 separated quote credentials from live-execution credentials. Migrate
# existing hosts in place without printing values. A new scoped key wins when
# both forms exist, making this safe to run before every deployment.
migrate_market_key() {
  local legacy_key="$1"
  local scoped_key="$2"

  if ! grep -q "^${legacy_key}=" "${MARKET_ENV}"; then
    return
  fi
  if grep -q "^${scoped_key}=" "${MARKET_ENV}"; then
    sed -i "/^${legacy_key}=/d" "${MARKET_ENV}"
  else
    sed -i "s/^${legacy_key}=/${scoped_key}=/" "${MARKET_ENV}"
  fi
}

if [[ -f "${MARKET_ENV}" ]]; then
  migrate_market_key "SJ_API_KEY" "MARKET_SJ_API_KEY"
  migrate_market_key "SJ_SEC_KEY" "MARKET_SJ_SECRET_KEY"
  migrate_market_key "SJ_PRODUCTION" "MARKET_SJ_PRODUCTION"
  chmod 0600 "${MARKET_ENV}"
fi

# Larger hosts do not need deployment swap. Small Lightsail plans can otherwise
# become unreachable while Next.js and Docker are building images.
if (( memory_kib >= MIN_MEMORY_KIB )); then
  exit 0
fi

mkdir -p "${INSTALL_ROOT}"

if [[ ! -f "${SWAP_FILE}" ]]; then
  fallocate -l "${SWAP_SIZE}" "${SWAP_FILE}"
  chmod 600 "${SWAP_FILE}"
  mkswap "${SWAP_FILE}"
fi

if ! swapon --show=NAME --noheadings | awk '{$1=$1};1' | grep -Fxq "${SWAP_FILE}"; then
  swapon "${SWAP_FILE}"
fi

if ! grep -Fq "${SWAP_FILE} none swap sw 0 0" /etc/fstab; then
  printf '%s\n' "${SWAP_FILE} none swap sw 0 0" >> /etc/fstab
fi

echo "Deployment swap is ready:"
free -h
