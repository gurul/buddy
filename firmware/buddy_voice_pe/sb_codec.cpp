#include "sb_codec.h"

#include "sb_config.h"

#if SB_HAS_AIC3204

#include <Wire.h>

namespace {

constexpr uint8_t kAic3204Address = 0x18;  // TI datasheet: I2C address 0011000.

struct RegWrite {
  uint8_t reg;
  uint8_t value;
};

// Register sequence from the ESPHome aic3204 driver (AIC3204::setup).
// scripts/check-aic3204.mjs compares both tables against that source.
// The clocks assume MCLK = 24.576 MHz from the XMOS and 48 kHz audio.
// AIC3204_STAGE1_BEGIN
constexpr RegWrite kStage1[] = {
    {0x00, 0x00},  // Select page 0
    {0x01, 0x01},  // Software reset
    {0x0B, 0x82},  // NDAC on, divide by 2
    {0x0C, 0x82},  // MDAC on, divide by 2
    {0x0E, 0x80},  // DOSR = 128
    {0x1B, 0x30},  // I2S, 32 bits
    {0x38, 0x02},  // SCLK/MFP3 is the audio data input
    {0x1F, 0x01},  // Audio interface 4
    {0x20, 0x01},  // Audio interface 5
    {0x3C, 0x01},  // DAC processing block PRB_P1
    {0x00, 0x01},  // Select page 1
    {0x02, 0x09},  // Enable the internal AVDD LDO
    {0x01, 0x08},  // Disable the crude AVDD supply
    {0x02, 0x01},  // Master analog power on
    {0x0A, 0x40},  // Common mode 0.75 V
    {0x03, 0x00},  // Left DAC PowerTune, class AB
    {0x04, 0x00},  // Right DAC PowerTune, class AB
    {0x7B, 0x01},  // Reference charge time 40 ms
    {0x14, 0x25},  // Headphone soft-step start
    {0x0C, 0x08},  // Left DAC to HPL
    {0x0D, 0x08},  // Right DAC to HPR
    {0x0E, 0x08},  // Left DAC to LOL
    {0x0F, 0x08},  // Right DAC to LOR
    {0x10, 0x3E},  // HPL unmute, -2 dB
    {0x11, 0x3E},  // HPR unmute, -2 dB
    {0x12, 0x00},  // LOL unmute, 0 dB
    {0x13, 0x00},  // LOR unmute, 0 dB
    {0x09, 0x3C},  // Power on HPL, HPR, LOL, LOR
};
// AIC3204_STAGE1_END

// The driver waits 2.5 s for the soft-step before it powers the DAC.
// AIC3204_STAGE2_BEGIN
constexpr RegWrite kStage2[] = {
    {0x00, 0x00},  // Select page 0
    {0x3F, 0xD4},  // Power on left and right DAC
};
// AIC3204_STAGE2_END

bool writeReg(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(kAic3204Address);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

template <size_t N>
bool writeAll(const RegWrite (&table)[N]) {
  for (const RegWrite &w : table) {
    if (!writeReg(w.reg, w.value)) {
      Serial.printf("AIC3204 write failed: reg 0x%02X.\n", w.reg);
      return false;
    }
  }
  return true;
}

}  // namespace

bool sbCodecBegin() {
  pinMode(SB_PIN_MUTE, INPUT);

  // Keep the amplifier off during DAC setup to prevent a pop.
  pinMode(SB_PIN_AMP_ENABLE, OUTPUT);
  digitalWrite(SB_PIN_AMP_ENABLE, LOW);

  // Reset the XMOS. It starts from its own flash and then drives the I2S
  // clocks. This firmware does not update the XMOS image.
  pinMode(SB_PIN_XMOS_RESET, OUTPUT);
  digitalWrite(SB_PIN_XMOS_RESET, HIGH);
  delay(1);
  digitalWrite(SB_PIN_XMOS_RESET, LOW);

  Wire.begin(SB_PIN_I2C_SDA, SB_PIN_I2C_SCL);
  if (!writeAll(kStage1)) {
    return false;
  }
  delay(2500);
  if (!writeAll(kStage2)) {
    return false;
  }

  int8_t volume = static_cast<int8_t>(constrain(SB_CODEC_VOLUME_STEPS, -127, 48));
  bool ok = writeReg(0x41, static_cast<uint8_t>(volume))  // Left DAC volume
            && writeReg(0x42, static_cast<uint8_t>(volume))  // Right DAC volume
            && writeReg(0x40, 0x00);                         // Unmute both DAC channels
  if (!ok) {
    Serial.println("AIC3204 volume setup failed.");
    return false;
  }

  digitalWrite(SB_PIN_AMP_ENABLE, HIGH);
  Serial.println("AIC3204 ready. Speaker amplifier on.");
  return true;
}

bool sbCodecMicMuted() {
  return digitalRead(SB_PIN_MUTE) == HIGH;
}

#else

bool sbCodecBegin() {
  return true;
}

bool sbCodecMicMuted() {
  return false;
}

#endif
