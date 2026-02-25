#include <Arduino.h>
#include <SPI.h>
#include <LittleFS.h>
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
// =====================  SPI pins (VSPI) =====================
static constexpr int PIN_SPI_SCK  = 18;   // GPIO18 - V_SPI_CLK
static constexpr int PIN_SPI_MOSI = 23;   // GPIO23 - V_SPI_D (MOSI)
static constexpr int PIN_SPI_MISO = 19;   // GPIO19 - V_SPI_Q (MISO) - not used but defined
static constexpr int PIN_SPI_CS   = 5;    // GPIO5  - V_SPI_CS0

// =====================  I2S pins =====================
static constexpr int PIN_I2S_BCLK = 26;   // GPIO26 - I2S Bit Clock
static constexpr int PIN_I2S_WS   = 25;   // GPIO25 - I2S Word Select (LRCK)
static constexpr int PIN_I2S_DOUT = 22;   // GPIO22 - I2S Data Out

// ===================== ESP-IDF I2S driver =====================
#include "driver/i2s.h"

// ===================== SPI protocol =====================
static constexpr uint8_t SPI_CMD_WRITE_LUT = 0x01;

// ===================== BLE UUIDs =====================
#define SERVICE_UUID        "12345678-1234-5678-1234-56789abcdef0"
#define CHARACTERISTIC_UUID "abcdef01-1234-5678-1234-56789abcdef0"

// ===================== BLE Command IDs =====================
static constexpr uint8_t CMD_START     = 0x01;
static constexpr uint8_t CMD_BAND_DATA = 0x02;
static constexpr uint8_t CMD_END       = 0x03;

// ===================== BLE Optimization Constants =====================
static constexpr uint16_t BLE_MTU_SIZE = 512;

// ===================== WDRC Constants =====================
static constexpr int NUM_BANDS = 10;
static constexpr int LUT_SIZE = 1024;
static constexpr int GAIN_SCALE = 1 << 20;

static const int CENTER_FREQS[NUM_BANDS] = {250, 500, 750, 1000, 1500, 2000, 3000, 4000, 6000, 8000};

// ===================== LUT Storage =====================
static uint32_t g_gain_luts[NUM_BANDS][LUT_SIZE];
static bool g_lut_written[NUM_BANDS][LUT_SIZE];
static uint16_t g_lut_valid_count[NUM_BANDS] = {0};
static bool g_receiving_data = false;
static bool g_data_valid = false;
static int g_current_band = -1;
static int g_total_packets = 0;

// ===================== Transfer Timing =====================
static unsigned long g_transfer_start_time = 0;

// ===================== BLE Variables =====================
static BLEServer *pServer = nullptr;
static BLECharacteristic *pCharacteristic = nullptr;
static bool deviceConnected = false;
static bool oldDeviceConnected = false;

// ===================== Flash File Path =====================
static const char* LUT_FILE = "/wdrc_luts.bin";
static const char* FLAG_FILE = "/wdrc_valid.txt";

// ===================== Forward Declarations =====================
static void send_all_luts_spi();

// ===================== I2S Setup (ESP32-S Legacy API) =====================
// ** UNTOUCHED - Per requirements **
static void i2s_keep_clocks_task(void *param) {
  (void)param;
  static int32_t zeros[256] = {0};
  size_t bytes_written = 0;
  while (true) {
    i2s_write(I2S_NUM_0, zeros, sizeof(zeros), &bytes_written, portMAX_DELAY);
  }
}

static void setup_i2s_48k() {
  i2s_config_t i2s_config = {
    .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_TX),
    .sample_rate = 48000,
    .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
    .channel_format = I2S_CHANNEL_FMT_RIGHT_LEFT,
    .communication_format = I2S_COMM_FORMAT_STAND_I2S,
    .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
    .dma_buf_count = 8,
    .dma_buf_len = 64,
    .use_apll = false,
    .tx_desc_auto_clear = true,
    .fixed_mclk = 0
  };

  i2s_pin_config_t pin_config = {
    .bck_io_num = PIN_I2S_BCLK,
    .ws_io_num = PIN_I2S_WS,
    .data_out_num = PIN_I2S_DOUT,
    .data_in_num = I2S_PIN_NO_CHANGE
  };

  esp_err_t err = i2s_driver_install(I2S_NUM_0, &i2s_config, 0, NULL);
  if (err != ESP_OK) {
    Serial.printf("ERROR: i2s_driver_install failed (%d)\n", (int)err);
    return;
  }

  err = i2s_set_pin(I2S_NUM_0, &pin_config);
  if (err != ESP_OK) {
    Serial.printf("ERROR: i2s_set_pin failed (%d)\n", (int)err);
    return;
  }

  xTaskCreatePinnedToCore(i2s_keep_clocks_task, "i2s_clk", 4096, nullptr, 2, nullptr, 0);
  Serial.println("✅ I2S OK: 48kHz stereo (GPIO26=BCLK, GPIO25=WS, GPIO22=DOUT)");
}

// ===================== SPI Setup =====================
// ** UNTOUCHED - Per requirements **
static void setup_spi() {
  pinMode(PIN_SPI_CS, OUTPUT);
  digitalWrite(PIN_SPI_CS, HIGH);
  
  // ESP32-S VSPI: SCK=18, MISO=19, MOSI=23, SS=5
  SPI.begin(PIN_SPI_SCK, PIN_SPI_MISO, PIN_SPI_MOSI, PIN_SPI_CS);
  
  Serial.println("✅ SPI OK (GPIO18=SCK, GPIO23=MOSI, GPIO5=CS)");
}

// ** UNTOUCHED - Per requirements **
static void spi_send_frame(uint8_t band, uint16_t addr, uint32_t data24) {
  digitalWrite(PIN_SPI_CS, LOW);
  delayMicroseconds(2);
  
  SPI.transfer(SPI_CMD_WRITE_LUT);
  SPI.transfer(band);
  SPI.transfer((addr >> 8) & 0x03);
  SPI.transfer(addr & 0xFF);
  SPI.transfer((data24 >> 16) & 0xFF);
  SPI.transfer((data24 >> 8) & 0xFF);
  SPI.transfer(data24 & 0xFF);
  
  delayMicroseconds(2);
  digitalWrite(PIN_SPI_CS, HIGH);
  delayMicroseconds(5);
}

// ===================== Flash Storage =====================
static bool save_luts_to_flash() {
  Serial.println("💾 Saving LUTs to flash...");
  
  File f = LittleFS.open(LUT_FILE, "w");
  if (!f) {
    Serial.println("❌ Failed to open file for writing!");
    return false;
  }
  
  for (int b = 0; b < NUM_BANDS; b++) {
    size_t written = f.write((uint8_t*)g_gain_luts[b], LUT_SIZE * sizeof(uint32_t));
    if (written != LUT_SIZE * sizeof(uint32_t)) {
      Serial.printf("❌ Band %d write failed!\n", b);
      f.close();
      return false;
    }
    Serial.printf("  Band %d saved ✅\n", b);
  }
  f.close();
  
  File flag = LittleFS.open(FLAG_FILE, "w");
  if (flag) {
    flag.println("VALID");
    flag.close();
  }
  
  Serial.println("💾 All LUTs saved to flash!");
  return true;
}

static bool load_luts_from_flash() {
  Serial.println("📂 Loading LUTs from flash...");
  
  if (!LittleFS.exists(FLAG_FILE)) {
    Serial.println("⚠️ No valid data flag found");
    return false;
  }
  
  if (!LittleFS.exists(LUT_FILE)) {
    Serial.println("⚠️ No LUT file found");
    return false;
  }
  
  File f = LittleFS.open(LUT_FILE, "r");
  if (!f) {
    Serial.println("❌ Failed to open LUT file!");
    return false;
  }
  
  for (int b = 0; b < NUM_BANDS; b++) {
    size_t read = f.read((uint8_t*)g_gain_luts[b], LUT_SIZE * sizeof(uint32_t));
    if (read != LUT_SIZE * sizeof(uint32_t)) {
      Serial.printf("❌ Band %d read failed!\n", b);
      f.close();
      return false;
    }
    g_lut_valid_count[b] = LUT_SIZE;
    Serial.printf("  Band %d loaded ✅\n", b);
  }
  f.close();
  
  Serial.println("📂 All LUTs loaded from flash!");
  return true;
}

// ===================== Validation =====================
static float q420_to_float(uint32_t q420) {
  int32_t signed_val = (q420 & 0x800000) ? (int32_t)(q420 | 0xFF000000) : (int32_t)q420;
  return (float)signed_val / (float)GAIN_SCALE;
}

static float gain_to_db(float g) {
  return (g <= 0) ? -120.0f : 20.0f * log10f(g);
}

static void print_gain_curve(uint8_t band) {
  Serial.printf("\n═══ BAND %d (%d Hz) ═══\n", band, CENTER_FREQS[band]);
  Serial.println("Idx\tHex\t\tGain(dB)");
  
  int keys[] = {0, 128, 256, 512, 768, 1023};
  for (int i = 0; i < 6; i++) {
    int idx = keys[i];
    uint32_t val = g_gain_luts[band][idx];
    float db = gain_to_db(q420_to_float(val));
    Serial.printf("%d\t0x%06X\t%.2f dB\n", idx, val, db);
  }
}

static void print_all_curves() {
  Serial.println("\n╔═══════════════════════════════════════╗");
  Serial.println("║      WDRC LUT DATA                    ║");
  Serial.println("╚═══════════════════════════════════════╝");
  
  for (int b = 0; b < NUM_BANDS; b++) {
    print_gain_curve(b);
  }
}

// ===================== Clear RAM LUTs =====================
static void clear_ram_luts() {
  for (int b = 0; b < NUM_BANDS; b++) {
    memset(g_gain_luts[b], 0, sizeof(g_gain_luts[b]));
    memset(g_lut_written[b], 0, sizeof(g_lut_written[b]));
    g_lut_valid_count[b] = 0;
  }
  g_total_packets = 0;
  g_current_band = -1;
}

// ===================== Send ALL LUTs via SPI (ONE TIME) =====================
// ** SPI transfer logic UNTOUCHED - Per requirements **
static void send_all_luts_spi() {
  if (!g_data_valid) {
    Serial.println("❌ No valid data to send!");
    return;
  }
  
  Serial.println("\n📤 ═══════════════════════════════════════");
  Serial.println("   SENDING ALL LUTs VIA SPI (AUTO)");
  Serial.println("═══════════════════════════════════════════");
  
  unsigned long start = millis();
  
  SPI.beginTransaction(SPISettings(1000000, MSBFIRST, SPI_MODE0));
  
  for (uint8_t b = 0; b < NUM_BANDS; b++) {
    Serial.printf("  Band %d (%d Hz): ", b, CENTER_FREQS[b]);
    
    for (uint16_t a = 0; a < LUT_SIZE; a++) {
      spi_send_frame(b, a, g_gain_luts[b][a]);
      
      if ((a % 256) == 255) {
        Serial.printf("%d ", a + 1);
      }
    }
    Serial.println("✅");
  }
  
  SPI.endTransaction();
  
  unsigned long elapsed = millis() - start;
  Serial.printf("\n✅ SPI transfer complete! Time: %lu ms\n", elapsed);
  Serial.printf("   Frames: %d, Bytes: %d\n", NUM_BANDS * LUT_SIZE, NUM_BANDS * LUT_SIZE * 7);
  Serial.println("═══════════════════════════════════════════\n");
}

// ===================== BLE Callbacks =====================
class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer *pServer) override {
    deviceConnected = true;
    Serial.println("📱 BLE Connected!");
    Serial.println("⚡ High-speed mode ready (MTU 512 + WriteNoResponse)");
  }
  
  void onDisconnect(BLEServer *pServer) override {
    deviceConnected = false;
    Serial.println("📱 BLE Disconnected!");
  }
};

class CharacteristicCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic *pChar) override {
    // *** OPTIMIZATION: Use direct data access instead of String ***
    uint8_t* data = pChar->getData();
    size_t len = pChar->getLength();
    
    if (len < 1) return;
    
    uint8_t cmd = data[0];
    
    // ===================== CMD_START =====================
    if (cmd == CMD_START) {
      Serial.println("\n🚀 START - High-speed transfer initiated...");
      clear_ram_luts();
      g_receiving_data = true;
      g_transfer_start_time = millis();  // Track transfer time
      return;
    }
    
    // ===================== CMD_END =====================
    if (cmd == CMD_END) {
      unsigned long transfer_time = millis() - g_transfer_start_time;
      
      Serial.println("\n🏁 END - Transfer complete!");
      Serial.printf("⚡ BLE Transfer Time: %lu ms\n", transfer_time);
      
      g_receiving_data = false;
      
      // Validation summary (print after transfer, not during)
      Serial.printf("\n📊 Summary (%d packets):\n", g_total_packets);
      bool ok = true;
      for (int b = 0; b < NUM_BANDS; b++) {
        Serial.printf("  Band %d: %d/%d %s\n", b, g_lut_valid_count[b], LUT_SIZE,
                      g_lut_valid_count[b] == LUT_SIZE ? "✅" : "❌");
        if (g_lut_valid_count[b] != LUT_SIZE) ok = false;
      }
      
      if (ok) {
        // Step 1: Save to Flash
        if (save_luts_to_flash()) {
          g_data_valid = true;
          print_all_curves();
          
          Serial.println("\n════════════════════════════════════════");
          Serial.println("  ✅ DATA SAVED TO FLASH!");
          Serial.println("════════════════════════════════════════");
          
          // Step 2: AUTO - Send to FPGA via SPI immediately
          Serial.println("\n🔄 AUTO: Updating FPGA via SPI...");
          send_all_luts_spi();
          
          Serial.println("\n════════════════════════════════════════");
          Serial.println("  ✅ FPGA UPDATED SUCCESSFULLY!");
          Serial.println("  🎧 New hearing profile is now ACTIVE");
          Serial.println("════════════════════════════════════════\n");
        } else {
          Serial.println("❌ Flash save failed - FPGA not updated");
        }
      } else {
        Serial.println("⚠️ Incomplete data - not saving to flash or updating FPGA");
        Serial.println("   Please retry from the App");
      }
      return;
    }
    
    // ===================== CMD_BAND_DATA =====================
    // *** OPTIMIZATION: Minimal processing, NO Serial prints during streaming ***
    if (cmd == CMD_BAND_DATA) {
      if (!g_receiving_data) return;
      if (len < 7) return;
      
      uint8_t band = data[1];
      uint16_t offset = ((uint16_t)data[2] << 8) | data[3];
      
      if (band >= NUM_BANDS || offset >= LUT_SIZE) return;
      
      // Track band changes (minimal logging)
      if (g_current_band != band) {
        g_current_band = band;
      }
      
      // *** OPTIMIZATION: Process all entries without blocking ***
      int num = (len - 4) / 3;
      for (int i = 0; i < num && (offset + i) < LUT_SIZE; i++) {
        int pos = 4 + i * 3;
        uint32_t val = ((uint32_t)data[pos] << 16) |
                       ((uint32_t)data[pos + 1] << 8) |
                       ((uint32_t)data[pos + 2]);
        
        int idx = offset + i;
        if (!g_lut_written[band][idx]) {
          g_gain_luts[band][idx] = val;
          g_lut_written[band][idx] = true;
          g_lut_valid_count[band]++;
        }
      }
      
      g_total_packets++;
      // *** REMOVED: All Serial.print calls during streaming ***
      return;
    }
    
    // Ignore printable ASCII (stray characters)
    if (cmd >= 0x20 && cmd <= 0x7E) return;
  }
};

// ===================== BLE Setup =====================
static void setup_ble() {
  BLEDevice::init("ESP32_HEARING_BLE");
  
  // *** OPTIMIZATION: Set MTU size before creating server ***
  BLEDevice::setMTU(BLE_MTU_SIZE);
  Serial.printf("⚡ BLE MTU set to %d bytes\n", BLE_MTU_SIZE);
  
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new ServerCallbacks());
  
  BLEService *svc = pServer->createService(SERVICE_UUID);
  
  // *** OPTIMIZATION: Added PROPERTY_WRITE_NR for Write Without Response ***
  pCharacteristic = svc->createCharacteristic(
    CHARACTERISTIC_UUID,
    BLECharacteristic::PROPERTY_READ |
    BLECharacteristic::PROPERTY_WRITE |
    BLECharacteristic::PROPERTY_WRITE_NR |  // Write Without Response
    BLECharacteristic::PROPERTY_NOTIFY
  );
  pCharacteristic->addDescriptor(new BLE2902());
  pCharacteristic->setCallbacks(new CharacteristicCallbacks());
  svc->start();
  
  BLEAdvertising *adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(SERVICE_UUID);
  adv->setScanResponse(true);
  BLEDevice::startAdvertising();
  
  Serial.println("✅ BLE: ESP32_HEARING_BLE (High-Speed Mode)");
}

// ===================== Setup =====================
void setup() {
  Serial.begin(115200);
  delay(1000);
  
  Serial.println("\n");
  Serial.println("╔════════════════════════════════════════════════════╗");
  Serial.println("║  ESP32-S WDRC Bridge v7 (HIGH-SPEED BLE)           ║");
  Serial.println("║  MTU: 512 | WriteNoResponse | Automated           ║");
  Serial.println("║                                                    ║");
  Serial.println("║  PIN CONFIGURATION:                                ║");
  Serial.println("║  SPI:  SCK=GPIO18, MOSI=GPIO23, CS=GPIO5          ║");
  Serial.println("║  I2S:  BCLK=GPIO26, WS=GPIO25, DOUT=GPIO22        ║");
  Serial.println("╚════════════════════════════════════════════════════╝\n");
  
  if (!LittleFS.begin(true)) {
    Serial.println("❌ LittleFS mount failed!");
  } else {
    Serial.println("✅ LittleFS OK");
  }
  
  setup_spi();
  setup_i2s_48k();
  setup_ble();
  
  // Load previous LUT data from flash (if exists)
  if (load_luts_from_flash()) {
    g_data_valid = true;
    Serial.println("\n✅ Previous LUT data loaded from flash!");
    print_all_curves();
    
    // Auto-send to FPGA on boot if valid data exists
    Serial.println("\n🔄 AUTO: Sending saved LUTs to FPGA on boot...");
    send_all_luts_spi();
  } else {
    g_data_valid = false;
    Serial.println("\n⚠️ No saved LUT data found - waiting for App data");
  }
  
  Serial.println("\n════════════════════════════════════════");
  Serial.println("  🎧 HIGH-SPEED AUTOMATED MODE ACTIVE");
  Serial.println("  • MTU: 512 bytes");
  Serial.println("  • Write Without Response: Enabled");
  Serial.println("  • Expected transfer time: < 2 seconds");
  Serial.println("════════════════════════════════════════\n");
}

// ===================== Loop =====================
void loop() {
  // Handle BLE reconnection
  if (!deviceConnected && oldDeviceConnected) {
    delay(500);
    BLEDevice::startAdvertising();
    Serial.println("🔄 BLE Re-advertising...");
  }
  oldDeviceConnected = deviceConnected;
  
  // Periodic status heartbeat (debug only, less frequent during transfers)
  static unsigned long last = 0;
  if (!g_receiving_data && millis() - last > 10000) {
    last = millis();
    if (deviceConnected) {
      Serial.println("💓 BLE connected - ready for high-speed data");
    } else {
      Serial.println("💓 Waiting for BLE connection...");
    }
  }
  
  delay(10);
}
// İbrahimUmutDoruk,EmirhanGarip