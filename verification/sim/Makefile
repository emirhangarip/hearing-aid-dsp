# Verification Makefile
# Default simulator: Verilator
# Core flows:
#   make                L1 paper-core (tests 1/3/4/5)
#   make filterbank     L2 filterbank checks
#   make hearing-aid    L3 paper-core HA checks
#   make paper-signoff  full paper flow (prepare + signoff + figures + manifest)

SIM           ?= verilator
TOPLEVEL      ?= ds_modulator
COCOTB_TEST_MODULES ?= test_hifi_suite

# Clock / implementation context (exported to Python testbenches)
FS_SYS        ?= 100e6
CLOCK_CONTEXT ?= simulation-reference
export FS_SYS
export CLOCK_CONTEXT

ROOT_DIR     := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)
RTL_ROOT_DIR := $(abspath $(ROOT_DIR)/../rtl)
TB_DIR       := $(ROOT_DIR)/tb
REPORTS_DIR  := $(ROOT_DIR)/reports
SIM_DIR      := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
FILTERBANK_SIM_BUILD  := $(SIM_DIR)/sim_build_filterbank
HA_SIM_BUILD          := $(SIM_DIR)/sim_build_hearing_aid
WDRC_INTR_SIM_BUILD   := $(SIM_DIR)/sim_build_wdrc_intrinsic
HA_LIT_SIM_BUILD      := $(SIM_DIR)/sim_build_ha_literature
HIFI_50_SIM_BUILD     := $(SIM_DIR)/sim_build_hifi_50mhz
HA_PDM_50_SIM_BUILD   := $(SIM_DIR)/sim_build_ha_pdm_50mhz
PROXY_CAPTURE_SIM_BUILD := $(SIM_DIR)/sim_build_proxy_capture
VENV_BIN              := $(ROOT_DIR)/.venv/bin
VENV_PYTHON           := $(VENV_BIN)/python3
WDRC_PROFILE_GEN      := $(RTL_ROOT_DIR)/mem/gen_wdrc_lut_profile.py
PAPER_FETCH_SCRIPT    := $(ROOT_DIR)/scripts/fetch_librispeech_subset.py
PAPER_RIR_SCRIPT      := $(ROOT_DIR)/scripts/gen_rirs.py
PAPER_LOCK_TH_SCRIPT  := $(ROOT_DIR)/scripts/lock_paper_thresholds.py
PAPER_BASELINE_SCRIPT := $(ROOT_DIR)/scripts/build_baseline_comparison.py
PAPER_MANIFEST_SCRIPT := $(ROOT_DIR)/scripts/write_paper_manifest.py
PAPER_FIG_SCRIPT      := $(ROOT_DIR)/scripts/build_paper_figures.py
PAPER_CLOCK_SCRIPT    := $(ROOT_DIR)/scripts/clock_control.py
PAPER_FPGA_INGEST_SCRIPT := $(ROOT_DIR)/scripts/ingest_fpga_synthesis.py
PAPER_PROXY_VALIDATE_SCRIPT := $(ROOT_DIR)/scripts/validate_proxy_vs_rtl.py
PAPER_PROXY_RTL_MANIFEST := $(ROOT_DIR)/reports/paper/proxy_rtl_capture_manifest.json
PAPER_BASELINE_JSON   := $(ROOT_DIR)/reports/paper/baseline_comparison.json
PAPER_REFRESH_THRESHOLDS ?= 0
PAPER_PROXY_VALIDATE_ENFORCE ?= 0
# Proxy capture uses an accelerated but valid sample strobe ratio:
# 12.288 MHz / 48 kHz = 256 cycles/sample (enough TDM slack, much faster than 100e6/48e3).
PROXY_CAPTURE_CLK_FREQ ?= 12288000
# Use integer-ps clock period to avoid cocotb precision errors with fractional ns.
# 81_380 ps is close to 12.288 MHz and keeps deterministic cycle-based behavior.
PROXY_CAPTURE_CLK_PS ?= 81380
PROXY_CAPTURE_AUDIO_FS ?= 48000
PROXY_CAPTURE_FRACTIONAL_STROBE ?= 0
PROXY_CAPTURE_MAX_SECONDS ?= 0.4
PROXY_CAPTURE_START_SECONDS ?= 0.0
PROXY_CAPTURE_AUTO_SPEECH ?= 1
PROXY_CAPTURE_CASES ?= 1-18
PROXY_VALIDATE_ALLOW_PARTIAL ?= 0
FPGA_TEMPLATE ?= 0
FPGA_SYN_RPT ?=
FPGA_PNR_RPT ?=
FPGA_PWR_RPT ?=
FPGA_NOTES ?=
FPGA_DEVICE ?=
FPGA_TOOL ?= Gowin EDA
WRAP_BASE_ARGS        := -Wno-WIDTHTRUNC -Wno-WIDTHEXPAND
WRAP_STROBE_ARGS      := -GWARN_ON_TRUNCATION=0

VERILOG_SOURCES := $(RTL_ROOT_DIR)/ds_modulator.sv

TDM_VERILOG_SOURCES := \
	$(RTL_ROOT_DIR)/hearing_tdm_pdm_wrap.sv \
	$(RTL_ROOT_DIR)/top_hearing_tdm.sv \
	$(RTL_ROOT_DIR)/tdm_filterbank_10band.sv \
	$(RTL_ROOT_DIR)/biquad_core_tdm.sv \
	$(RTL_ROOT_DIR)/coeffs_rom.sv \
	$(RTL_ROOT_DIR)/tdm_wdrc_10band.sv \
	$(RTL_ROOT_DIR)/wdrc_gain_ram.sv \
	$(RTL_ROOT_DIR)/ds_modulator.sv

WDRC_INTR_VERILOG_SOURCES := \
	$(RTL_ROOT_DIR)/wdrc_intrinsic_wrap.sv \
	$(RTL_ROOT_DIR)/tdm_wdrc_10band.sv \
	$(RTL_ROOT_DIR)/wdrc_gain_ram.sv

COEFFS_MEM_SRC := $(RTL_ROOT_DIR)/mem/fpga_coeff.mem
COEFFS_MEM_DST := $(SIM_DIR)/fpga_coeff.mem
WDRC_LUT_MEM_SRCS := $(wildcard $(RTL_ROOT_DIR)/mem/intop_wdrc_gain_lut_b*.mem)

# Compile args: xsim needs --sv --nolog, Icarus uses -g2012 (added by cocotb)
ifeq ($(SIM),xsim)
    COMPILE_ARGS += --sv --nolog
endif

# Verilator: expose all internal signals for cocotb hierarchy access in
# Layer 3 hearing-aid tests (dut.u_tdm_core.audio_out / out_valid).
# Applied globally so the same SIM_BUILD can serve all TDM targets.
ifeq ($(SIM),verilator)
    COMPILE_ARGS += --public-flat-rw
endif

PYTHONPATH   := $(TB_DIR)
export PYTHONPATH
export COMPILE_ARGS

# Force cocotb tooling to resolve from this repo's venv first.
export PATH  := $(VENV_BIN):$(PATH)
PYTHON       := $(if $(wildcard $(VENV_PYTHON)),$(VENV_PYTHON),python3)
# cocotb's Makefile.sim auto-detect can resolve to another repo's venv if
# cocotb-config shebangs are stale; pin it to this workspace venv.
override PYTHON_BIN := $(PYTHON)

# ── cocotb ───────────────────────────────────────────────────────────────────
# cocotb 2.0+ moved Makefiles from cocotb/share to cocotb_tools/makefiles
COCOTB_SHARE    := $(shell $(PYTHON) -c \
    "import cocotb_tools, os; print(os.path.dirname(cocotb_tools.__file__))" 2>/dev/null)
COCOTB_MAKEFILE := $(COCOTB_SHARE)/makefiles/Makefile.sim

ifeq ($(wildcard $(COCOTB_MAKEFILE)),)
$(error cocotb not found. Run: source ../bootstrap_env.sh)
endif

include $(COCOTB_MAKEFILE)

$(REPORTS_DIR):
	@mkdir -p $@

# ── main target ───────────────────────────────────────────────────────────────

.PHONY: all
all: $(REPORTS_DIR) sim

.PHONY: hifi-full
hifi-full: $(REPORTS_DIR)
	@$(MAKE) HIFI_RUN_LEGACY=1 sim

# 50 MHz silicon-correlated L1 run (ds_modulator PDM quality at impl clock)
.PHONY: hifi-50mhz
hifi-50mhz: $(REPORTS_DIR)
	@$(MAKE) SIM_BUILD=$(HIFI_50_SIM_BUILD) \
		FS_SYS=50e6 CLOCK_CONTEXT=silicon-correlated \
		HIFI_SPEC_MODE=1 HIFI_LONG_CAPTURE=1 HIFI_RUN_LEGACY=0 sim

# 50 MHz silicon-correlated HA PDM-path run (HA-4 + HA-3 at impl clock)
# DUT params: CLK_FREQ=50_000_000 enables correct RTL strobe; FRACTIONAL_STROBE=1
# enables Bresenham sample cadence in the RTL wrapper (requires RTL support).
.PHONY: hearing-aid-pdm-50mhz
hearing-aid-pdm-50mhz: $(REPORTS_DIR) coeffs
	@$(MAKE) SIM_BUILD=$(HA_PDM_50_SIM_BUILD) \
		FS_SYS=50e6 CLOCK_CONTEXT=silicon-correlated \
		TOPLEVEL=hearing_tdm_pdm_wrap \
		COCOTB_TEST_MODULES=test_hearing_aid_suite \
		EXTRA_ARGS="$(WRAP_BASE_ARGS) $(WRAP_STROBE_ARGS) -GCLK_FREQ=50000000 -GFRACTIONAL_STROBE=1" \
		VERILOG_SOURCES="$(TDM_VERILOG_SOURCES)" sim

# ── Layer 2: Filterbank reconstruction (unity WDRC) ──────────────────────────

.PHONY: gen-unity-lut
gen-unity-lut:
	@$(PYTHON) $(ROOT_DIR)/scripts/gen_unity_lut.py $(SIM_DIR)

.PHONY: gen-ha-lut
gen-ha-lut:
	@$(PYTHON) $(WDRC_PROFILE_GEN) \
		--out-dir $(RTL_ROOT_DIR)/mem \
		--ratio $(if $(WDRC_CR),$(WDRC_CR),3.0) \
		--knee-dbfs -40 \
		--max-gain-db 18

.PHONY: filterbank
filterbank: $(REPORTS_DIR) coeffs gen-unity-lut
	@$(MAKE) SIM_BUILD=$(FILTERBANK_SIM_BUILD) \
		TOPLEVEL=hearing_tdm_pdm_wrap \
		COCOTB_TEST_MODULES=test_filterbank_suite \
		EXTRA_ARGS="$(WRAP_BASE_ARGS) $(WRAP_STROBE_ARGS)" \
		VERILOG_SOURCES="$(TDM_VERILOG_SOURCES)" sim

.PHONY: filterbank-one
filterbank-one: $(REPORTS_DIR) coeffs gen-unity-lut
	@$(MAKE) SIM_BUILD=$(FILTERBANK_SIM_BUILD) \
		TOPLEVEL=hearing_tdm_pdm_wrap \
		COCOTB_TEST_MODULES=test_filterbank_suite \
		COCOTB_TEST_FILTER="$(TESTCASE)" \
		TESTCASE= \
		EXTRA_ARGS="$(WRAP_BASE_ARGS) $(WRAP_STROBE_ARGS)" \
		VERILOG_SOURCES="$(TDM_VERILOG_SOURCES)" sim

# ── Layer 3: Hearing-aid WDRC characterization (real LUT) ────────────────────

.PHONY: hearing-aid
hearing-aid: $(REPORTS_DIR) coeffs
	@$(MAKE) SIM_BUILD=$(HA_SIM_BUILD) \
		TOPLEVEL=hearing_tdm_pdm_wrap \
		COCOTB_TEST_MODULES=test_hearing_aid_suite \
		EXTRA_ARGS="$(WRAP_BASE_ARGS) $(WRAP_STROBE_ARGS)" \
		VERILOG_SOURCES="$(TDM_VERILOG_SOURCES)" sim

.PHONY: hearing-aid-full
hearing-aid-full: $(REPORTS_DIR) coeffs
	@$(MAKE) SIM_BUILD=$(HA_SIM_BUILD) \
		TOPLEVEL=hearing_tdm_pdm_wrap \
		COCOTB_TEST_MODULES=test_hearing_aid_suite \
		HA_RUN_IDLE_EIN=1 \
		EXTRA_ARGS="$(WRAP_BASE_ARGS) $(WRAP_STROBE_ARGS)" \
		VERILOG_SOURCES="$(TDM_VERILOG_SOURCES)" sim

.PHONY: hearing-aid-one
hearing-aid-one: $(REPORTS_DIR) coeffs
	@$(MAKE) SIM_BUILD=$(HA_SIM_BUILD) \
		TOPLEVEL=hearing_tdm_pdm_wrap \
		COCOTB_TEST_MODULES=test_hearing_aid_suite \
		COCOTB_TEST_FILTER="$(TESTCASE)" \
		TESTCASE= \
		EXTRA_ARGS="$(WRAP_BASE_ARGS) $(WRAP_STROBE_ARGS)" \
		VERILOG_SOURCES="$(TDM_VERILOG_SOURCES)" sim

.PHONY: hearing-aid-signoff
hearing-aid-signoff: $(REPORTS_DIR) coeffs
	@$(MAKE) hearing-aid SIM=$(SIM) HA_SPEC_MODE=1
	@$(MAKE) wdrc-intrinsic SIM=$(SIM) HA_SPEC_MODE=1

.PHONY: hearing-aid-signoff-full
hearing-aid-signoff-full: $(REPORTS_DIR) coeffs
	@$(MAKE) hearing-aid-full SIM=$(SIM) HA_SPEC_MODE=1
	@$(MAKE) wdrc-intrinsic SIM=$(SIM) HA_SPEC_MODE=1

.PHONY: ha-literature
ha-literature: $(REPORTS_DIR) coeffs
	@$(MAKE) SIM_BUILD=$(HA_LIT_SIM_BUILD) \
		TOPLEVEL=hearing_tdm_pdm_wrap \
		COCOTB_TEST_MODULES=test_ha_literature_suite \
		EXTRA_ARGS="$(WRAP_BASE_ARGS) $(WRAP_STROBE_ARGS)" \
		VERILOG_SOURCES="$(TDM_VERILOG_SOURCES)" sim

.PHONY: ha-literature-one
ha-literature-one: $(REPORTS_DIR) coeffs
	@$(MAKE) SIM_BUILD=$(HA_LIT_SIM_BUILD) \
		TOPLEVEL=hearing_tdm_pdm_wrap \
		COCOTB_TEST_MODULES=test_ha_literature_suite \
		COCOTB_TEST_FILTER="$(TESTCASE)" \
		TESTCASE= \
		EXTRA_ARGS="$(WRAP_BASE_ARGS) $(WRAP_STROBE_ARGS)" \
		VERILOG_SOURCES="$(TDM_VERILOG_SOURCES)" sim

.PHONY: paper-fetch-data
paper-fetch-data:
	@echo "[paper] Fetch/verify speech subset"
	@PYTHONUNBUFFERED=1 $(PYTHON) $(PAPER_FETCH_SCRIPT)

.PHONY: paper-gen-rirs
paper-gen-rirs:
	@echo "[paper] Generate deterministic RIRs"
	@PYTHONUNBUFFERED=1 $(PYTHON) $(PAPER_RIR_SCRIPT)

.PHONY: paper-lock-thresholds
paper-lock-thresholds:
	@echo "[paper] Lock thresholds from collect metrics"
	@PYTHONUNBUFFERED=1 $(PYTHON) $(PAPER_LOCK_TH_SCRIPT)

.PHONY: paper-baselines
paper-baselines:
	@echo "[paper] Build same-dataset baseline comparison"
	@if [ -f "$(PAPER_BASELINE_JSON)" ] && [ "$(PAPER_BASELINES_FORCE)" != "1" ]; then \
		echo "[paper] Baseline artifact exists -> $(PAPER_BASELINE_JSON) (skip; set PAPER_BASELINES_FORCE=1 to rebuild)"; \
	else \
		PYTHONUNBUFFERED=1 $(PYTHON) $(PAPER_BASELINE_SCRIPT); \
	fi

.PHONY: paper-clock-note
paper-clock-note:
	@echo "[paper] Write clock-correlation note"
	@PYTHONUNBUFFERED=1 $(PYTHON) $(PAPER_CLOCK_SCRIPT) write-note

.PHONY: paper-clock-check
paper-clock-check:
	@echo "[paper] Check clock metadata consistency"
	@PYTHONUNBUFFERED=1 $(PYTHON) $(PAPER_CLOCK_SCRIPT) check

.PHONY: paper-clock
paper-clock:
	@echo "[paper] Write and validate clock-correlation artifacts"
	@PYTHONUNBUFFERED=1 $(PYTHON) $(PAPER_CLOCK_SCRIPT) all

# Optional standalone proxy/RTL package.
# Kept out of paper-signoff; run manually when you want RTL speech captures
# and proxy-vs-RTL correlation diagnostics.
.PHONY: paper-proxy-capture
paper-proxy-capture: $(REPORTS_DIR) coeffs
	@echo "[paper] Capture RTL outputs for proxy-vs-RTL package (18-case matrix)"
	@mkdir -p $(PROXY_CAPTURE_SIM_BUILD)
	@$(MAKE) SIM_BUILD=$(PROXY_CAPTURE_SIM_BUILD) \
		FS_SYS=$(PROXY_CAPTURE_CLK_FREQ) CLOCK_CONTEXT=proxy-capture \
		PROXY_RTL_CLK_PS=$(PROXY_CAPTURE_CLK_PS) \
		PROXY_RTL_AUDIO_FS=$(PROXY_CAPTURE_AUDIO_FS) \
		PROXY_RTL_MAX_SECONDS=$(PROXY_CAPTURE_MAX_SECONDS) \
		PROXY_RTL_START_SECONDS=$(PROXY_CAPTURE_START_SECONDS) \
		PROXY_RTL_AUTO_SPEECH=$(PROXY_CAPTURE_AUTO_SPEECH) \
		PROXY_RTL_CASES="$(PROXY_CAPTURE_CASES)" \
		TOPLEVEL=hearing_tdm_pdm_wrap \
		COCOTB_TEST_MODULES=test_proxy_rtl_capture_suite \
		EXTRA_ARGS="$(WRAP_BASE_ARGS) $(WRAP_STROBE_ARGS) -GCLK_FREQ=$(PROXY_CAPTURE_CLK_FREQ) -GAUDIO_FS=$(PROXY_CAPTURE_AUDIO_FS) -GFRACTIONAL_STROBE=$(PROXY_CAPTURE_FRACTIONAL_STROBE)" \
		VERILOG_SOURCES="$(TDM_VERILOG_SOURCES)" sim

.PHONY: paper-proxy-validate
paper-proxy-validate:
	@echo "[paper] Validate proxy-vs-RTL correlation (18-case package)"
	@if [ ! -f "$(PAPER_PROXY_RTL_MANIFEST)" ]; then \
		echo "[paper] INFO: proxy-vs-RTL validation skipped (missing manifest: $(PAPER_PROXY_RTL_MANIFEST); run 'make -C verification/sim paper-proxy-capture' first)"; \
	elif PYTHONUNBUFFERED=1 PROXY_RTL_MAX_SECONDS=$(PROXY_CAPTURE_MAX_SECONDS) PROXY_RTL_START_SECONDS=$(PROXY_CAPTURE_START_SECONDS) PROXY_RTL_AUTO_SPEECH=$(PROXY_CAPTURE_AUTO_SPEECH) PROXY_VALIDATE_ALLOW_PARTIAL=$(PROXY_VALIDATE_ALLOW_PARTIAL) $(PYTHON) $(PAPER_PROXY_VALIDATE_SCRIPT); then \
		echo "[paper] Proxy-vs-RTL validation completed"; \
	elif [ "$(PAPER_PROXY_VALIDATE_ENFORCE)" = "1" ]; then \
		echo "[paper] Proxy-vs-RTL validation failed (enforced mode)." >&2; \
		exit 1; \
	else \
		echo "[paper] INFO: proxy-vs-RTL validation failed (non-blocking policy; set PAPER_PROXY_VALIDATE_ENFORCE=1 to enforce)." >&2; \
	fi

.PHONY: paper-manifest
paper-manifest:
	@echo "[paper] Write artifact manifest"
	@PYTHONUNBUFFERED=1 $(PYTHON) $(PAPER_MANIFEST_SCRIPT)

.PHONY: paper-ingest-fpga
paper-ingest-fpga:
	@echo "[paper] Ingest FPGA synthesis/timing/power reports"
	@if [ "$(FPGA_TEMPLATE)" = "1" ]; then \
		PYTHONUNBUFFERED=1 $(PYTHON) $(PAPER_FPGA_INGEST_SCRIPT) --template \
			--tool "$(FPGA_TOOL)" \
			$(if $(FPGA_DEVICE),--device "$(FPGA_DEVICE)") \
			$(if $(FPGA_NOTES),--notes "$(FPGA_NOTES)"); \
	elif [ -z "$(FPGA_SYN_RPT)" ] && [ -z "$(FPGA_PNR_RPT)" ] && [ -z "$(FPGA_PWR_RPT)" ]; then \
		echo "[paper] ERROR: set at least one of FPGA_SYN_RPT / FPGA_PNR_RPT / FPGA_PWR_RPT, or set FPGA_TEMPLATE=1." >&2; \
		exit 2; \
	else \
		PYTHONUNBUFFERED=1 $(PYTHON) $(PAPER_FPGA_INGEST_SCRIPT) \
			$(if $(FPGA_SYN_RPT),--syn "$(FPGA_SYN_RPT)") \
			$(if $(FPGA_PNR_RPT),--pnr "$(FPGA_PNR_RPT)") \
			$(if $(FPGA_PWR_RPT),--power "$(FPGA_PWR_RPT)") \
			--tool "$(FPGA_TOOL)" \
			$(if $(FPGA_DEVICE),--device "$(FPGA_DEVICE)") \
			$(if $(FPGA_NOTES),--notes "$(FPGA_NOTES)"); \
	fi

.PHONY: paper-figures
paper-figures:
	@echo "[paper] Build paper figures"
	@PYTHONUNBUFFERED=1 $(PYTHON) $(PAPER_FIG_SCRIPT)

.PHONY: paper-prepare
paper-prepare: $(REPORTS_DIR) coeffs
	@echo "[paper] === PREP START ==="
	@$(MAKE) paper-fetch-data
	@$(MAKE) paper-gen-rirs
	@$(MAKE) ha-literature SIM=$(SIM) PAPER_MODE=collect
	@$(MAKE) paper-baselines
	@if [ "$(PAPER_REFRESH_THRESHOLDS)" = "1" ]; then \
		echo "[paper] Refresh locked thresholds (PAPER_REFRESH_THRESHOLDS=1)"; \
		$(MAKE) paper-lock-thresholds; \
	else \
		echo "[paper] Skip threshold refresh (using committed locked thresholds; set PAPER_REFRESH_THRESHOLDS=1 to regenerate)"; \
	fi
	@echo "[paper] === PREP DONE ==="

.PHONY: paper-signoff
paper-signoff: $(REPORTS_DIR) coeffs
	@echo "[paper] === SIGNOFF START ==="
	@$(MAKE) paper-prepare SIM=$(SIM)
	@echo "[paper] [L1] HiFi core"
	@$(MAKE) all SIM=$(SIM) HIFI_SPEC_MODE=1 HIFI_LONG_CAPTURE=1 HIFI_RUN_LEGACY=0
	@echo "[paper] [L2] Filterbank"
	@$(MAKE) filterbank SIM=$(SIM)
	@echo "[paper] [L3] Hearing aid core"
	@$(MAKE) hearing-aid SIM=$(SIM) HA_SPEC_MODE=1 HA_RUN_IDLE_EIN=0
	@echo "[paper] [L4] Intrinsic tau"
	@$(MAKE) wdrc-intrinsic SIM=$(SIM) HA_SPEC_MODE=1
	@echo "[paper] [L5] Literature signoff"
	@$(MAKE) ha-literature SIM=$(SIM) PAPER_MODE=signoff
	@$(MAKE) paper-figures
	@$(MAKE) paper-clock-note
	@$(MAKE) paper-clock-check
	@$(MAKE) paper-manifest
	@echo "[paper] === SIGNOFF DONE ==="

.PHONY: wdrc-intrinsic
wdrc-intrinsic: $(REPORTS_DIR) coeffs
	@$(MAKE) SIM_BUILD=$(WDRC_INTR_SIM_BUILD) \
		TOPLEVEL=wdrc_intrinsic_wrap \
		COCOTB_TEST_MODULES=test_wdrc_intrinsic_suite \
		EXTRA_ARGS="-Wno-WIDTHTRUNC -Wno-WIDTHEXPAND" \
		VERILOG_SOURCES="$(WDRC_INTR_VERILOG_SOURCES)" sim

.PHONY: wdrc-intrinsic-one
wdrc-intrinsic-one: $(REPORTS_DIR) coeffs
	@$(MAKE) SIM_BUILD=$(WDRC_INTR_SIM_BUILD) \
		TOPLEVEL=wdrc_intrinsic_wrap \
		COCOTB_TEST_MODULES=test_wdrc_intrinsic_suite \
		COCOTB_TEST_FILTER="$(TESTCASE)" \
		TESTCASE= \
		EXTRA_ARGS="-Wno-WIDTHTRUNC -Wno-WIDTHEXPAND" \
		VERILOG_SOURCES="$(WDRC_INTR_VERILOG_SOURCES)" sim

# ── single test (Layer 1) ─────────────────────────────────────────────────────

.PHONY: one
one: $(REPORTS_DIR)
	@$(MAKE) COCOTB_TEST_FILTER="$(TESTCASE)" TESTCASE= sim

# ── utilities ─────────────────────────────────────────────────────────────────

.PHONY: selftest
selftest:
	PYTHONPATH=$(TB_DIR) $(PYTHON) $(TB_DIR)/dsp_engine.py

.PHONY: validate-suite
validate-suite:
	@echo "[meta] Run verification methodology self-validation"
	@PYTHONUNBUFFERED=1 PYTHONPATH=$(TB_DIR) $(PYTHON) $(TB_DIR)/validate_suite.py

.PHONY: reports
reports:
	@ls -lh $(REPORTS_DIR)/ 2>/dev/null || echo "(no reports yet)"

.PHONY: coeffs
coeffs:
	@cp -f $(COEFFS_MEM_SRC) $(COEFFS_MEM_DST)
	@for f in $(WDRC_LUT_MEM_SRCS); do cp -f $$f $(SIM_DIR)/; done

.PHONY: deps
deps:
	pip install -r $(ROOT_DIR)/requirements.txt

.PHONY: clean
clean::
	@rm -rf __pycache__ .cocotb build xsim.dir \
		sim_build* \
		intop_wdrc_gain_lut_b*.mem \
		*.wdb *.jou *.log *.pb dump.vcd
	@echo "Clean complete. Reports preserved."

.PHONY: cleanall
cleanall: clean
	@rm -rf $(REPORTS_DIR)

.PHONY: help-verification
help-verification:
	@echo ""
	@echo "  Layer 1 (DSM / AES17)"
	@echo "  make [SIM=verilator]              L1 paper-core tests (1/3/4/5)"
	@echo "  make hifi-full                    Full L1 (adds legacy 2/6/7)"
	@echo "  make hifi-50mhz                   L1 at 50 MHz silicon-correlated context"
	@echo "  make one TESTCASE=...             Run one L1 test"
	@echo ""
	@echo "  Layer 2 (Filterbank)"
	@echo "  make filterbank                   L2-1 + L2-2"
	@echo "  make filterbank-one TESTCASE=...  Run one L2 test"
	@echo "  make gen-unity-lut                Write unity-gain LUT files"
	@echo "  make gen-ha-lut WDRC_CR=3.0       Write hearing-aid LUT profile"
	@echo ""
	@echo "  Layer 3/4 (Hearing aid + intrinsic tau)"
	@echo "  make hearing-aid                  HA paper-core tests"
	@echo "  make hearing-aid-full             Full HA set (adds HA-3)"
	@echo "  make hearing-aid-pdm-50mhz        PDM-path HA tests at 50 MHz"
	@echo "  make wdrc-intrinsic               Intrinsic WDRC tau validation"
	@echo "  make ha-literature                HA-7..HA-10 objective scenarios"
	@echo "  make paper-prepare                Fetch + RIR + collect + optional threshold refresh"
	@echo "  make paper-signoff                Full paper flow"
	@echo ""
	@echo "  Artifacts and utilities"
	@echo "  make paper-baselines              Build unprocessed/NAL-R/WDRC comparison"
	@echo "  make paper-figures                Build Fig.1..Fig.8"
	@echo "  make paper-clock-note             Write clock-correlation claim note"
	@echo "  make paper-clock-check            Validate clock metadata/claims"
	@echo "  make paper-clock                 Write + validate clock-correlation artifacts"
	@echo "  make paper-ingest-fpga            Ingest Gowin synthesis/timing/power reports"
	@echo "      (vars: FPGA_SYN_RPT=... FPGA_PNR_RPT=... [FPGA_PWR_RPT=...] [FPGA_NOTES=...] [FPGA_DEVICE=...] [FPGA_TOOL=...])"
	@echo "      (template: FPGA_TEMPLATE=1)"
	@echo "  make paper-proxy-capture          Generate 18-case RTL captures + manifest"
	@echo "      (options: PROXY_CAPTURE_CASES=1-3, PROXY_CAPTURE_START_SECONDS=2.0, PROXY_CAPTURE_MAX_SECONDS=2.0, PROXY_CAPTURE_AUTO_SPEECH=1)"
	@echo "  make paper-proxy-validate         Run 18-case proxy-vs-RTL validation package"
	@echo "      (optional quick mode: PROXY_VALIDATE_ALLOW_PARTIAL=1 for subset manifests)"
	@echo "  make validate-suite               Run methodology self-validation checks"
	@echo "  make paper-manifest               Write reports/paper/manifest.json"
	@echo "  make reports                      List reports"
	@echo "  make deps                         Install Python deps"
	@echo "  make clean                        Remove build artifacts"
	@echo "  make cleanall                     Remove build artifacts + reports"
	@echo ""

.DEFAULT_GOAL := all
