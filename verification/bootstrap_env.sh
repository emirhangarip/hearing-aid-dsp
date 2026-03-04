#!/usr/bin/env bash
# Bootstrap verification Python environment.
# Usage:
#   cd verification
#   source bootstrap_env.sh
# Offline mode:
#   BOOTSTRAP_OFFLINE=1 source bootstrap_env.sh

set -e

VENV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)/.venv"
REQ_FILE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)/requirements.txt"
OFFLINE_MODE="${BOOTSTRAP_OFFLINE:-0}"

_validate_existing_env() {
python3 - <<'PY'
import importlib
import sys

required = [
    "cocotb",
    "numpy",
    "scipy",
    "matplotlib",
    "yaml",  # PyYAML
    "soundfile",
    "clarity",  # provided by pyclarity distribution
    "pyroomacoustics",
]
missing = []
for mod in required:
    try:
        importlib.import_module(mod)
    except Exception:
        missing.append(mod)

if missing:
    print("ERROR: Missing required modules:", ", ".join(missing))
    sys.exit(1)

print("Using existing installed packages in .venv.")
PY
}

# Python version check
PYTHON_BIN=$(command -v python3.11 || command -v python3.10 || \
             command -v python3.9  || command -v python3   || true)

if [[ -z "$PYTHON_BIN" ]]; then
    echo "ERROR: Python 3.9+ not found. Install it first." >&2
    exit 1
fi

PY_VER=$("$PYTHON_BIN" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)

if (( PY_MAJOR < 3 || (PY_MAJOR == 3 && PY_MINOR < 9) )); then
    echo "ERROR: Python 3.9+ required, found $PY_VER" >&2
    exit 1
fi

echo "✓ Using Python $PY_VER  ($PYTHON_BIN)"

# Create venv
if [[ ! -d "$VENV_DIR" ]]; then
    echo "Creating venv at $VENV_DIR ..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
else
    echo "✓ venv already exists at $VENV_DIR"
fi

# Activate
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# Upgrade packaging tools
if [[ "$OFFLINE_MODE" == "1" ]]; then
    echo "Offline mode enabled: skipping pip network operations."
    if ! _validate_existing_env; then
        echo "ERROR: Existing .venv is incomplete for offline mode."
        exit 1
    fi
else
    echo "Upgrading pip / setuptools / wheel ..."
    if ! pip install --quiet --upgrade pip setuptools wheel; then
        echo "WARNING: Could not refresh pip/setuptools/wheel (offline?). Using existing versions."
    fi

    # Install dependencies
    echo "Installing packages from requirements.txt ..."
    if ! pip install --quiet -r "$REQ_FILE"; then
        echo "WARNING: requirements install failed. Checking whether the current venv is already usable..."
        if ! _validate_existing_env; then
            echo "ERROR: Environment is incomplete and dependency download failed."
            exit 1
        fi
    fi
fi

echo ""
echo "Installed package versions:"
pip show cocotb scipy numpy matplotlib 2>/dev/null | grep -E "^Name|^Version"

# Simulator check
echo ""
echo "Simulator check:"
SIM_FOUND=""

if command -v iverilog &>/dev/null; then
    echo "  ✓ Icarus Verilog  (iverilog)  $(iverilog -V 2>&1 | head -1)"
    SIM_FOUND="icarus"
fi

if command -v verilator &>/dev/null; then
    echo "  ✓ Verilator        $(verilator --version 2>&1 | head -1)"
    SIM_FOUND="${SIM_FOUND} verilator"
fi

if command -v xsim &>/dev/null; then
    echo "  ✓ Vivado xsim      $(xsim --version 2>&1 | head -1 | cut -c1-60)"
    SIM_FOUND="${SIM_FOUND} xsim"
fi

if [[ -z "$SIM_FOUND" ]]; then
    echo "  ⚠ No simulator found on PATH."
    echo "    For free local simulation, install Icarus Verilog:"
    echo "      Ubuntu/Debian : sudo apt install iverilog"
    echo "      macOS         : brew install icarus-verilog"
    echo "    Then run:  make sim SIM=icarus"
fi

# Quick DSP engine sanity check
echo ""
echo "Running dsp_engine self-test ..."
PYTHONPATH="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)/tb" \
    python3 -c "
from dsp_engine import VirtualAnalogAnalyzer
a = VirtualAnalogAnalyzer(fs=100e6, invert_output=True)
print('  ✓ VirtualAnalogAnalyzer imported and instantiated OK')
print('  ✓ Filter wn =', round(a.lpf_cutoff_hz / (a.fs/2), 8))
"

echo ""
echo "Environment ready."
echo "Run full verification: make -C sim SIM=verilator paper-signoff"
echo "Deactivate later with: deactivate"
