#!/usr/bin/env bash
set -euo pipefail

INSTALL_ROOT="/opt/tw-quant"
EXECUTION_ENV="${INSTALL_ROOT}/config/execution.env"
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
