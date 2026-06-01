#include <Arduino.h>
#include <U8x8lib.h>

// 4SPI wiring for UNO
static const uint8_t OLED_CLK = 13; // D0/SCK
static const uint8_t OLED_MOSI = 11; // D1/MOSI
static const uint8_t OLED_CS = 10;
static const uint8_t OLED_DC = 12;
static const uint8_t OLED_RST = 6;

#define TEST_SH1106 1

#if TEST_SH1106
U8X8_SH1106_128X64_NONAME_4W_SW_SPI oled(OLED_CLK, OLED_MOSI, OLED_CS, OLED_DC, OLED_RST);
#else
U8X8_SSD1306_128X64_NONAME_4W_SW_SPI oled(OLED_CLK, OLED_MOSI, OLED_CS, OLED_DC, OLED_RST);
#endif

static void hardReset() {
  pinMode(OLED_RST, OUTPUT);
  digitalWrite(OLED_RST, LOW);
  delay(20);
  digitalWrite(OLED_RST, HIGH);
  delay(20);
}

void setup() {
  Serial.begin(115200);
  hardReset();
  oled.begin();
  oled.setPowerSave(0);
  oled.setContrast(255);
  oled.setFont(u8x8_font_chroma48medium8_r);
  oled.clearDisplay();

  oled.drawString(0, 0, "4SPI UNO TEST");
#if TEST_SH1106
  oled.drawString(0, 1, "DRV: SH1106");
#else
  oled.drawString(0, 1, "DRV: SSD1306");
#endif
  oled.drawString(0, 2, "D0->13 D1->11");
  oled.drawString(0, 3, "CS10 DC12 RST6");
  oled.drawString(0, 4, "JMP: R3+R4");
}

void loop() {
  static uint32_t c = 0;
  char line[17];
  snprintf(line, sizeof(line), "CNT:%lu", (unsigned long)c++);
  oled.drawString(0, 7, "                ");
  oled.drawString(0, 7, line);
  delay(400);
}
