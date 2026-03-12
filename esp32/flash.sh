#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_NAME="${PIO_ENV:-esp32dev}"
PIO_CMD=()
PYTHON_CMD=()
UPLOAD_FS=1

usage() {
  cat <<'EOF'
Usage: ./flash.sh [--no-fs] [PORT]

  PORT      Serial device, for example /dev/ttyUSB0
  --no-fs   Flash firmware only and preserve the current LittleFS contents
EOF
}

find_port() {
  local ports=()
  shopt -s nullglob
  ports=(/dev/ttyUSB* /dev/ttyACM* /dev/cu.usbserial* /dev/cu.SLAB_USBtoUART*)
  shopt -u nullglob

  if [[ "${#ports[@]}" -eq 1 ]]; then
    printf '%s\n' "${ports[0]}"
    return 0
  fi

  if [[ "${#ports[@]}" -eq 0 ]]; then
    echo "No serial port detected. Pass one explicitly, for example: ./flash.sh /dev/ttyUSB0" >&2
    return 1
  fi

  echo "Multiple serial ports detected. Pass one explicitly:" >&2
  printf '  %s\n' "${ports[@]}" >&2
  return 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-fs)
      UPLOAD_FS=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      break
      ;;
  esac
done

if [[ -x "${SCRIPT_DIR}/.venv/bin/pio" ]]; then
  PIO_CMD=("${SCRIPT_DIR}/.venv/bin/pio")
elif command -v pio >/dev/null 2>&1; then
  PIO_CMD=(pio)
elif command -v platformio >/dev/null 2>&1; then
  PIO_CMD=(platformio)
elif [[ -x "${HOME}/.local/bin/pio" ]]; then
  PIO_CMD=("${HOME}/.local/bin/pio")
elif [[ -x "${HOME}/.local/bin/platformio" ]]; then
  PIO_CMD=("${HOME}/.local/bin/platformio")
else
  echo "PlatformIO is not installed. Run ./setup.sh in ${SCRIPT_DIR} first." >&2
  exit 1
fi

if [[ -x "${SCRIPT_DIR}/.venv/bin/python" ]]; then
  PYTHON_CMD=("${SCRIPT_DIR}/.venv/bin/python")
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=(python3)
else
  echo "python3 is required to prepare the default LUT filesystem image." >&2
  exit 1
fi

PORT="${1:-${PORT:-}}"
if [[ -z "${PORT}" ]]; then
  PORT="$(find_port)"
fi

cd "${SCRIPT_DIR}"

if [[ "${UPLOAD_FS}" -eq 1 ]]; then
  "${PYTHON_CMD[@]}" "${SCRIPT_DIR}/prepare_fs.py"
fi

echo "Uploading firmware to ${PORT}"
"${PIO_CMD[@]}" run -e "${ENV_NAME}" -t upload --upload-port "${PORT}"

if [[ "${UPLOAD_FS}" -eq 1 ]]; then
  echo "Uploading default LittleFS image to ${PORT}"
  exec "${PIO_CMD[@]}" run -e "${ENV_NAME}" -t uploadfs --upload-port "${PORT}"
fi

exit 0
