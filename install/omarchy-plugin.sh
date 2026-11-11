#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=install-vars
source "${SCRIPT_DIR}/install-vars"

CONFIG_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/omarchy"
TARGET="${CONFIG_DIR}/plugins/${PLUGIN_ID}"

# The plugin lives at the repository root, the way the Omarchy marketplace
# expects it, so omarchy plugin add can validate and install this checkout.
PLUGIN_FILES=(manifest.json Panel.qml Model.js)

# omarchy plugin list reports .enabled for a bar widget when it is on the bar.
# A reinstall must leave that placement alone instead of moving the widget.
widget_in_bar() {
  command -v jq >/dev/null 2>&1 || return 1
  omarchy plugin list --json 2>/dev/null |
    jq -e --arg id "${PLUGIN_ID}" 'any(.[]; .id == $id and .enabled)' >/dev/null 2>&1
}

# Ask for a bar section only on the first placement, with the same prompt and
# the same preselected answer as omarchy plugin add. Without a terminal, or
# without gum, the widget simply follows barWidget.defaultSection.
select_bar_section() {
  local default_section
  local section

  [[ -t 0 && -t 1 ]] || return 0
  command -v gum >/dev/null 2>&1 || return 0
  command -v jq >/dev/null 2>&1 || return 0

  default_section="$(jq -r '.barWidget.defaultSection // "center"' "${TARGET}/manifest.json")"
  section="$(printf '%s\n' left center right |
    gum choose --header="Place ${PLUGIN_ID} in which bar section?" --selected "${default_section}")" || return 0
  [[ -n "${section}" ]] || return 0
  PLACEMENT=(--section "${section}")
}

usage() {
  cat <<'EOF'
Usage: omarchy-plugin.sh [--no-backend]
       omarchy-plugin.sh remove [--no-backend]

Installs the AK820 Pro Sentinel backend, its systemd service and the Omarchy
widget for the current user. The backend needs root, so the script asks for
your password through sudo.

Options:
  --no-backend  install, repair or remove only the widget
  --help        show this help

On a first install the script asks which bar section to place the widget in,
with the same prompt as the Omarchy marketplace. A widget that is already in
the bar keeps its position.

Do not run this script with sudo. Run it as your desktop user.
EOF
}

print_help=false
action="install"
backend=true

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      print_help=true
      ;;
    --no-backend)
      backend=false
      ;;
    remove)
      action="remove"
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [[ "${print_help}" == true ]]; then
  usage
  exit 0
fi

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run this script as your desktop user, without sudo." >&2
  exit 1
fi

if [[ "${backend}" == true ]] && ! command -v sudo >/dev/null 2>&1; then
  echo "The sudo command was not found. Install sudo or use --no-backend." >&2
  exit 1
fi

if [[ "${action}" == "remove" ]]; then
  if command -v omarchy >/dev/null 2>&1; then
    omarchy plugin disable "${PLUGIN_ID}" 2>/dev/null || true
  fi
  rm -rf "${TARGET}"
  echo "Removed ${PLUGIN_ID}."

  if [[ "${backend}" == true ]]; then
    echo
    echo "Removing the backend and the systemd service. sudo will ask for your password."
    sudo "${SCRIPT_DIR}/uninstall.sh"
  else
    echo "The backend and the service were not touched."
  fi

  echo
  echo "Run 'omarchy restart shell' to refresh the bar."
  exit 0
fi

if ! command -v omarchy >/dev/null 2>&1; then
  echo "The omarchy command was not found. Is this an Omarchy machine?" >&2
  exit 1
fi

if [[ ! -d "${CONFIG_DIR}" ]]; then
  echo "Omarchy configuration not found at ${CONFIG_DIR}." >&2
  exit 1
fi

for plugin_file in "${PLUGIN_FILES[@]}"; do
  [[ -f "${PROJECT_ROOT}/${plugin_file}" ]] || {
    echo "Plugin file not found: ${PROJECT_ROOT}/${plugin_file}" >&2
    exit 1
  }
done

[[ -f "${PROJECT_ROOT}/backend/sentinel/main.py" ]] || {
  echo "Backend not found. Run the installer from the extracted project directory." >&2
  exit 1
}

if [[ "${backend}" == true ]]; then
  echo "Installing the backend and the systemd service. sudo will ask for your password."
  sudo "${SCRIPT_DIR}/install.sh"
  echo
fi

was_in_bar=false
if widget_in_bar; then
  was_in_bar=true
fi

rm -rf "${TARGET}"
install -d -m 0755 "${TARGET}"
for plugin_file in "${PLUGIN_FILES[@]}"; do
  install -m 0644 "${PROJECT_ROOT}/${plugin_file}" "${TARGET}/${plugin_file}"
done

omarchy plugin validate "${TARGET}"

PLACEMENT=()
if [[ "${was_in_bar}" == false ]]; then
  select_bar_section
fi

if [[ ${#PLACEMENT[@]} -gt 0 ]]; then
  omarchy plugin enable "${PLUGIN_ID}" "${PLACEMENT[@]}"
else
  omarchy plugin enable "${PLUGIN_ID}"
fi

echo
if [[ "${backend}" == true ]]; then
  echo "Installed the backend, the service and the widget."
  echo "Check the service with: systemctl status ${SERVICE}"
else
  echo "Installed the widget only."
fi
echo "If the widget is not in the bar yet, run: omarchy restart shell"
