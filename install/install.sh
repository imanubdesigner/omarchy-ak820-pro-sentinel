#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=install-vars
source "${SCRIPT_DIR}/install-vars"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi

for cmd in python3 systemctl install; do
  command -v "${cmd}" >/dev/null || {
    echo "Missing required command: ${cmd}" >&2
    exit 1
  }
done

[[ -x /usr/bin/python3 ]] || {
  echo "Python 3 was not found at /usr/bin/python3." >&2
  exit 1
}

[[ -f "${PROJECT_ROOT}/backend/sentinel/main.py" ]] || {
  echo "Backend not found. Run the installer from the extracted project directory." >&2
  exit 1
}

echo "Installing ${PROJECT} ${VERSION}..."

if systemctl is-active --quiet "${SERVICE}"; then
  systemctl stop "${SERVICE}"
fi

install -d -m 0755 "${PREFIX}/sentinel" "${PREFIX}/docs"
install -m 0644 "${PROJECT_ROOT}/backend/sentinel/"*.py "${PREFIX}/sentinel/"
install -m 0644 "${PROJECT_ROOT}/README.md" "${PREFIX}/README.md"
install -m 0644 "${PROJECT_ROOT}/CHANGELOG.md" "${PREFIX}/CHANGELOG.md"
install -m 0644 "${PROJECT_ROOT}/LICENSE" "${PREFIX}/LICENSE"
install -m 0644 "${PROJECT_ROOT}/NOTICE" "${PREFIX}/NOTICE"
install -m 0644 "${PROJECT_ROOT}/docs/"*.md "${PREFIX}/docs/"
install -m 0644 "${PROJECT_ROOT}/systemd/${SERVICE}" "${SERVICE_PATH}"

cat > "${BIN}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="${PREFIX}\${PYTHONPATH:+:\${PYTHONPATH}}"
exec /usr/bin/python3 -m sentinel.main "\$@"
EOF
chmod 0755 "${BIN}"

# The uninstaller lives outside /opt, so removing the backend cannot pull the
# script out from under itself. It stays available after `omarchy plugin
# remove`, which deletes the plugin folder and nothing else.
install -d -m 0755 "${LIB}"
install -m 0755 "${PROJECT_ROOT}/install/uninstall.sh" "${LIB}/uninstall.sh"
install -m 0644 "${PROJECT_ROOT}/install/install-vars" "${LIB}/install-vars"

cat > "${REMOVE_BIN}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec "${LIB}/uninstall.sh" "\$@"
EOF
chmod 0755 "${REMOVE_BIN}"

if [[ -f "${CONFIG}" ]]; then
  echo "Keeping the existing configuration at ${CONFIG}."
else
  PYTHONPATH="${PROJECT_ROOT}/backend" /usr/bin/python3 -B -c \
    'import sys; from sentinel.config import EXAMPLE_CONFIG; sys.stdout.write(EXAMPLE_CONFIG)' \
    > "${CONFIG}"
  chmod 0644 "${CONFIG}"
  echo "Installed the default configuration at ${CONFIG}."
fi

systemctl daemon-reload
systemctl enable --now "${SERVICE}"

echo
echo "Installed and started ${SERVICE}."
echo "Status: systemctl status ${SERVICE}"
echo "Logs:   journalctl -u ${SERVICE} -f"
echo "Check:  ${BIN} --check"
echo "Remove: sudo ${PROJECT}-remove"
echo

if systemctl is-active --quiet ak820-linux-lifeline.service 2>/dev/null; then
  echo "Note: ak820-linux-lifeline.service is still active."
  echo "Sentinel did not touch it. Stop it yourself once Sentinel is validated."
  echo
fi
