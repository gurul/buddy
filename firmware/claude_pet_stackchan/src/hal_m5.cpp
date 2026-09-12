// CoreS3 core peripherals. This TU and body.cpp are the only ones that see
// M5Unified's global `M5`; see hal_m5.h for why.
#include "hal_m5.h"
#include <M5StackChan.h>
#include <sys/time.h>
#include <time.h>

void halBegin() {
  // Enlarge the HWCDC rings before the first begin(): the bridge's
  // heartbeats arrive as one USB-speed burst, and the default 256-byte RX
  // ring was the leading suspect for the one-way wedge on the Freenove
  // build. HWCDC::begin() only allocates when a ring is NULL, so the second
  // Serial.begin() inside M5.begin() keeps these sizes.
  Serial.setRxBufferSize(4096);
  Serial.setTxBufferSize(4096);
  Serial.setTxTimeoutMs(20);
  Serial.begin(115200);
  // M5StackChan.begin() calls M5.begin() itself (default config: display,
  // touch, speaker, RTC, power), then brings up the PY32 expander (servo
  // rail on, 12 LEDs), the Feetech bus + Motion task, and the INA226.
  M5StackChan.begin();
  // The pet renders a 320x240 landscape frame. M5GFX picks the panel's
  // native orientation for CoreS3; only correct it if that came up portrait.
  if (M5.Display.width() < M5.Display.height()) {
    M5.Display.setRotation(M5.Display.getRotation() ^ 1);
  }
  M5.Display.fillScreen(TFT_BLACK);
  M5.Speaker.setVolume(96);
}

void halUpdate() { M5StackChan.update(); }

M5GFX& halDisplay() { return M5.Display; }

bool halTouch(int* x, int* y) {
  if (M5.Touch.getCount() == 0) return false;
  auto& d = M5.Touch.getDetail();
  if (!d.isPressed()) return false;
  *x = d.x; *y = d.y;
  return true;
}

void halBrightness(uint8_t level) { M5.Display.setBrightness(level); }

int  halBatteryMv() { return M5.Power.getBatteryVoltage(); }
int  halBatteryMa() { return M5.Power.getBatteryCurrent(); }
int  halVbusMv()    { return M5.Power.getVBUSVoltage(); }
bool halPowerClicked() { return M5.BtnPWR.wasClicked(); }
void halDeepSleep() {
  M5.Display.setBrightness(0);
  M5.Power.deepSleep(0, true);
}

// data.h pushes *local* time components (epoch already tz-adjusted). Store
// them as-is in the RTC and mirror them into the system clock as "UTC" so
// time()/gmtime_r agree with what the RTC reads back.
void halRtcSet(int h, int m, int s, int wday, int mon, int mday, int year) {
  struct tm t = {};
  t.tm_hour = h; t.tm_min = m; t.tm_sec = s;
  t.tm_mday = mday; t.tm_mon = mon - 1; t.tm_year = year - 1900; t.tm_wday = wday;
  if (M5.Rtc.isEnabled()) {
    m5::rtc_date_t d; d.year = year; d.month = mon; d.date = mday; d.weekDay = wday;
    m5::rtc_time_t tt(h, m, s);
    M5.Rtc.setDateTime(&d, &tt);
  }
  time_t epoch = mktime(&t);   // TZ unset: interpreted as UTC
  struct timeval tv = { .tv_sec = epoch, .tv_usec = 0 };
  settimeofday(&tv, nullptr);
}

void halRtcGet(int* h, int* m, int* s, int* wday, int* mon, int* mday, int* year) {
  if (M5.Rtc.isEnabled()) {
    m5::rtc_date_t d; m5::rtc_time_t t;
    if (M5.Rtc.getDateTime(&d, &t)) {
      *h = t.hours; *m = t.minutes; *s = t.seconds;
      *wday = d.weekDay; *mon = d.month; *mday = d.date; *year = d.year;
      return;
    }
  }
  time_t now = time(nullptr);
  struct tm lt; gmtime_r(&now, &lt);
  *h = lt.tm_hour; *m = lt.tm_min; *s = lt.tm_sec;
  *wday = lt.tm_wday; *mon = lt.tm_mon + 1; *mday = lt.tm_mday; *year = lt.tm_year + 1900;
}
