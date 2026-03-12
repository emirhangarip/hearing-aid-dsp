#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BAUD="${BAUD:-115200}"
PIO_CMD=()

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
    echo "No serial port detected. Pass one explicitly, for example: ./monitor.sh /dev/ttyUSB0" >&2
    return 1
  fi

  echo "Multiple serial ports detected. Pass one explicitly:" >&2
  printf '  %s\n' "${ports[@]}" >&2
  return 1
}

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

PORT="${1:-${PORT:-}}"
if [[ -z "${PORT}" ]]; then
  PORT="$(find_port)"
fi

cd "${SCRIPT_DIR}"
exec "${PIO_CMD[@]}" device monitor --port "${PORT}" --baud "${BAUD}"
