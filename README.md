# hearing-aid-dsp

A complete end-to-end hearing aid system: RTL/DSP core verified in simulation, an ESP32 firmware bridge, and a Flutter mobile fitting application for Android.

---

## Repository Structure

```
hearing-aid-dsp/
├── Mobile_App/          # Flutter/Dart Android application (WDRC fitting + BLE)
├── esp32/               # ESP32 firmware (BLE ↔ audio bridge)
├── rtl/                 # RTL hearing-aid DSP implementation
└── verification/        # cocotb simulation, paper-signoff flow & evidence
```

---

## Quick Start

### RTL / Verification

From repo root:

```bash
cd verification
source bootstrap_env.sh
python3 -m pip install -r requirements.txt
make -C sim SIM=verilator paper-signoff
```

### Mobile App

Requirements: Flutter SDK, Android Studio, Android device (Android 12+)

```bash
cd Mobile_App
flutter pub get
flutter run
```

> **Note:** The mobile application is designed and optimized specifically for **Android** devices.

---

## Mobile Application

The Flutter application serves as a complete fitting interface for the Audio Enhancement Unit (AEU). It performs in-app audiometry, calculates personalized WDRC profiles, and transmits them to the ESP32 hardware over Bluetooth Low Energy.

### Features

**WDRC Algorithm**
Implements the full Wide Dynamic Range Compression fitting engine in Dart:
- Compression Ratios (CR) and Threshold Kneepoints (TK) for 10 frequency bands
- Gain Look-Up Tables (LUT) tailored to the user's specific hearing loss
- Attack and Release times derived from HL/UCL measurements

**Bluetooth Low Energy (BLE)**
High-speed, reliable communication with the ESP32:
- MTU negotiation for optimized throughput
- Packet fragmentation for large LUT data transfers
- Custom flow control to ensure data integrity

**In-App Audiometry**
- Pure-tone hearing tests from 250 Hz to 8 kHz
- Measures Hearing Level (HL) and Uncomfortable Level (UCL)
- Interactive audiogram chart visualization

### Key Files

| File | Purpose |
|---|---|
| `Mobile_App/lib/main.dart` | WDRC algorithm, UI, BLE state management |
| `Mobile_App/pubspec.yaml` | Package dependencies (`flutter_blue_plus`, `flutter_sound`, `permission_handler`) |
| `Mobile_App/android/app/src/main/AndroidManifest.xml` | Bluetooth, location & audio permissions (Android 12+) |

### Technical Stack

| | |
|---|---|
| Framework | Flutter |
| Language | Dart |
| Platform | Android |
| Connectivity | Bluetooth Low Energy (`flutter_blue_plus`) |
| Audio | PCM16 Tone Synthesis (`flutter_sound`) |

### Screenshots

<p align="center">
  <img src="https://github.com/user-attachments/assets/e2c75d09-eaa8-4648-beda-ab966a32196d" alt="Mobile App Screenshot" width="850">
</p>

---

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

---

## Contributors

- İbrahim Umut Doruk
- Emirhan Garip
