#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=install-vars
source "${SCRIPT_DIR}/install-vars"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi

systemctl disable --now "${SERVICE}" 2>/dev/null || true
rm -f "${SERVICE_PATH}" "${BIN}" "${CONFIG}" "${REMOVE_BIN}"
rm -rf "${PREFIX}" "${LIB}"
systemctl daemon-reload
systemctl reset-failed "${SERVICE}" 2>/dev/null || true

echo "${PROJECT} removed."
echo

USER_HOME=""
if [[ -n "${SUDO_USER:-}" ]]; then
  USER_HOME="$(getent passwd "${SUDO_USER}" 2>/dev/null | cut -d: -f6)"
fi

if [[ -n "${USER_HOME}" ]] && [[ -d "${USER_HOME}/.config/omarchy/plugins/${PLUGIN_ID}" ]]; then
  echo "The Omarchy widget is still installed. Remove it with:"
  echo "  omarchy plugin remove ${PLUGIN_ID}"
  echo
fi

echo "The previous lifeline service, if present, was not modified."
