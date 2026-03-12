#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "python3 is required but was not found." >&2
  exit 1
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  if ! "${PYTHON_BIN}" -m venv "${VENV_DIR}" >/dev/null 2>&1; then
    echo "Failed to create ${VENV_DIR}." >&2
    echo "On Debian/Ubuntu, install venv support with: sudo apt install python3-venv" >&2
    exit 1
  fi
fi

"${VENV_DIR}/bin/python" -m pip install --upgrade pip >/dev/null
"${VENV_DIR}/bin/python" -m pip install "platformio>=6,<7"

echo
echo "PlatformIO is ready in ${VENV_DIR}"
echo "Next steps:"
echo "  cd ${SCRIPT_DIR}"
echo "  ./flash.sh /dev/ttyUSB0"
echo "  ./monitor.sh /dev/ttyUSB0"
