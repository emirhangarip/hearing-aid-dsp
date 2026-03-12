# hearing-aid-dsp

A complete end-to-end hearing aid system: RTL/DSP core verified in simulation, an ESP32 firmware bridge, and a Flutter mobile fitting application for Android.

---

## Repository Structure

```text
hearing-aid-dsp/
├── Mobile_App/          # Flutter/Dart Android application (WDRC fitting + BLE)
├── esp32/               # ESP32 firmware (BLE ↔ audio bridge)
├── rtl/                 # RTL hearing-aid DSP implementation
└── verification/        # cocotb simulation, paper-signoff flow & evidence
```

---

## Quick Start

### Mobile App

Requirements: Flutter SDK, Android Studio, Android device (Android 12+)

```bash
cd Mobile_App
flutter pub get
flutter run
```

> **Note:** The mobile application is designed and optimized specifically for **Android** devices.

---

### ESP32 Firmware

The maintained ESP32 workflow is documented in [`esp32/README.md`](esp32/README.md). It uses a pinned PlatformIO setup, a repo-local virtualenv, and automatic LittleFS provisioning for the default WDRC LUTs.

From repo root:

```bash
cd esp32
./setup.sh
./flash.sh /dev/ttyUSB0
./monitor.sh /dev/ttyUSB0
```

Arduino IDE remains a fallback path, but the PlatformIO flow is the repo-supported setup.

### RTL / Verification

From repo root:

```bash
cd verification
source bootstrap_env.sh
python3 -m pip install -r requirements.txt
make -C sim SIM=verilator paper-signoff
```

## Mobile Application

The Flutter application serves as a fitting interface for the Audio Enhancement Unit (AEU). It performs in-app audiometry, calculates personalized WDRC (Wide Dynamic Range Compression) profiles, and transmits them to the ESP32 hardware over Bluetooth Low Energy.

### Technical Stack

| | |
|---|---|
| Framework | Flutter |
| Language | Dart |
| Platform | Android |
| Connectivity | Bluetooth Low Energy (`flutter_blue_plus`) |
| Audio | PCM16 Tone Synthesis (`flutter_sound`) |

## ESP32 Firmware

The ESP32 bridge firmware lives under [`esp32/`](esp32/). The default LUT image is generated from the RTL `.mem` files during flashing, so first-time users do not need to provision LUTs manually.

### Documentation

| Document | Description |
|---|---|
| [esp32/README.md](esp32/README.md) | Setup, build, flash, serial monitor, Linux notes, and LittleFS LUT provisioning |

## RTL / Verification

### Documentation Map

| Document | Description |
|---|---|
| [verification/README.md](verification/README.md) | Operational runbook — prerequisites, commands, pass/fail interpretation, troubleshooting |
| [verification/docs/paper_signoff.md](verification/docs/paper_signoff.md) | Lock/push signoff procedure and artifact checks |
| [verification/docs/test_matrix.md](verification/docs/test_matrix.md) | Complete verification coverage map and evidence boundaries |

### Fast Commands

```bash
# L1 paper-core (DSM / HiFi)
make -C verification/sim SIM=verilator

# L3 paper-core hearing-aid tests
make -C verification/sim SIM=verilator hearing-aid

# Full paper flow
make -C verification/sim SIM=verilator paper-signoff
```

### Screenshots

<p align="center">
  <img src="https://github.com/user-attachments/assets/e2c75d09-eaa8-4648-beda-ab966a32196d" alt="Mobile App Screenshot" width="700">
</p>

---

## Contributors

- İbrahim Umut Doruk
- Emirhan Garip
- Dooyoung Hah
