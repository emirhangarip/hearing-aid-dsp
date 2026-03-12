# ESP32 Firmware

This directory contains the ESP32 bridge firmware used by the hearing-aid DSP project.

## Why this setup exists

The sketch uses the legacy ESP32 Arduino I2S API and classic BLE headers in [`app2aeu/app2aeu.ino`](app2aeu/app2aeu.ino), so the repo ships a pinned PlatformIO configuration instead of asking every user to recreate Arduino IDE settings by hand.

The default WDRC LUTs come from the RTL-side files in [`../rtl/mem/intop_wdrc_gain_lut_b*.mem`](../rtl/mem/) and are packed into the ESP32 LittleFS image during flashing.

## Quick Start

1. Create the repo-local PlatformIO environment:

   ```bash
   cd esp32
   ./setup.sh
   ```

2. Connect an original ESP32 dev board.

3. Flash the firmware:

   ```bash
   ./flash.sh /dev/ttyUSB0
   ```

   This uploads both the firmware and the default LittleFS image containing `wdrc_luts.bin` and `wdrc_valid.txt`.

4. Open the serial monitor:

   ```bash
   ./monitor.sh /dev/ttyUSB0
   ```

If only one serial device is connected, both scripts can usually auto-detect the port.

## Supported Board

- Target board: original ESP32 / ESP32-WROOM style dev boards
- PlatformIO board ID: `esp32dev`
- Serial baud: `115200`

The sketch configures these pins:

- SPI: `GPIO18` SCK, `GPIO23` MOSI, `GPIO19` MISO, `GPIO5` CS
- I2S: `GPIO26` BCLK, `GPIO25` WS, `GPIO22` DOUT

## Useful Commands

Build only:

```bash
cd esp32
./.venv/bin/pio run -e esp32dev
```

Flash:

```bash
cd esp32
./flash.sh /dev/ttyUSB0
```

Flash firmware only and preserve the current on-device LUTs:

```bash
cd esp32
./flash.sh --no-fs /dev/ttyUSB0
```

Serial monitor:

```bash
cd esp32
./monitor.sh /dev/ttyUSB0
```

## Linux Notes

Debian and Ubuntu block `pip install --user` for system-managed Python environments. That is why this repo uses `./setup.sh`, which installs PlatformIO into `esp32/.venv` instead of touching the system Python installation.

If you previously flashed only the firmware and saw `No valid data flag found`, rerun:

```bash
cd esp32
./flash.sh /dev/ttyUSB0
```

That command now uploads the default LUT filesystem image as well.

If the port opens with `Permission denied`, add your user to the `dialout` group and sign in again:

```bash
sudo usermod -a -G dialout "$USER"
```

If upload does not start automatically, hold the `BOOT` button on the ESP32 while the upload begins.

## Arduino IDE Fallback

Users who prefer Arduino IDE can still open [`app2aeu/app2aeu.ino`](app2aeu/app2aeu.ino) directly, select `ESP32 Dev Module`, and use the same serial settings. The PlatformIO flow is the maintained path for this repo because it is deterministic and CI-tested.
