/*
 * Arena Replay - Botão físico (ESP32 DevKit)
 * ------------------------------------------------------------
 * O ESP32 só detecta o botão e avisa o notebook por Wi-Fi.
 * Nenhum vídeo é processado aqui.
 *
 * Ligações:
 *   Botão: um terminal no GPIO 27, outro no GND (usa pull-up interno).
 *   LED:   LED azul da própria placa (GPIO 2).
 *
 * Protocolo: POST http://SERVER_HOST:SERVER_PORT/api/events
 *   Cabeçalho: X-Arena-Token: DEVICE_TOKEN
 *   Corpo:     {"event_id":"quadra1-1a2b3c4d-7","device_id":"quadra1","age_ms":120}
 *   Resposta:  201 (novo), 200 (duplicado/agrupado) = confirmado.
 *
 * Garantias:
 *   - Debounce por interrupção + bloqueio de 800 ms entre toques.
 *   - Identificador único por evento: DEVICE_ID-BOOT_ID-CONTADOR.
 *   - Fila de até 8 eventos: se o Wi-Fi cair, os toques não são perdidos.
 *   - Reenvio com espera progressiva até a confirmação (o servidor ignora duplicados).
 *   - age_ms informa há quanto tempo o botão foi apertado, então o replay
 *     pega o momento certo mesmo se o envio atrasar.
 *
 * LED:
 *   Piscando devagar ........ sem Wi-Fi
 *   Piscadinha a cada 3 s ... pronto
 *   Piscando rápido ......... enviando
 *   Aceso 0,5 s ............. replay confirmado
 *   3 piscadas .............. falha (evento descartado)
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include "secrets.h"  // copie secrets.example.h para secrets.h e preencha

// ----------------------------------------------------------------- ajustes
const int BUTTON_PIN = 27;
const int LED_PIN = 2;
const uint32_t LOCKOUT_MS = 800;           // ignora novos toques por este tempo
const uint32_t HTTP_TIMEOUT_MS = 2500;
const uint32_t MAX_EVENT_AGE_MS = 25000;   // servidor aceita até 30 s
const uint32_t WIFI_RETRY_MS = 5000;
const int QUEUE_SIZE = 8;

// ------------------------------------------------------------------ estado
struct PendingEvent {
  char id[65];
  uint32_t pressedAt;
  uint32_t nextAttemptAt;
  uint8_t attempts;
  bool used;
};

PendingEvent queueEvents[QUEUE_SIZE];
volatile uint32_t isrPressAt = 0;
volatile bool isrPressed = false;
volatile uint32_t isrLastAccepted = 0;

uint32_t bootId = 0;
uint32_t eventCounter = 0;
uint32_t lastWifiAttempt = 0;
uint32_t ledOverrideUntil = 0;
int ledOverrideMode = 0;  // 1 = confirmado, 2 = erro
uint32_t ledOverrideStart = 0;

// ---------------------------------------------------------------- botão
void IRAM_ATTR onButton() {
  uint32_t now = millis();
  if (now - isrLastAccepted < LOCKOUT_MS) return;  // debounce / toque duplo
  isrLastAccepted = now;
  isrPressAt = now;
  isrPressed = true;
}

int pendingCount() {
  int n = 0;
  for (int i = 0; i < QUEUE_SIZE; i++) if (queueEvents[i].used) n++;
  return n;
}

void enqueuePress(uint32_t pressedAt) {
  int slot = -1;
  for (int i = 0; i < QUEUE_SIZE; i++) {
    if (!queueEvents[i].used) { slot = i; break; }
  }
  if (slot < 0) {
    // Fila cheia: descarta o mais antigo (já estaria velho demais para o replay)
    uint32_t oldest = UINT32_MAX;
    for (int i = 0; i < QUEUE_SIZE; i++) {
      if (queueEvents[i].pressedAt < oldest) { oldest = queueEvents[i].pressedAt; slot = i; }
    }
    Serial.printf("[fila] cheia, descartando %s\n", queueEvents[slot].id);
  }
  eventCounter++;
  PendingEvent &ev = queueEvents[slot];
  snprintf(ev.id, sizeof(ev.id), "%s-%08lx-%lu", DEVICE_ID, (unsigned long)bootId,
           (unsigned long)eventCounter);
  ev.pressedAt = pressedAt;
  ev.nextAttemptAt = 0;
  ev.attempts = 0;
  ev.used = true;
  Serial.printf("[botao] lance %s\n", ev.id);
}

// ----------------------------------------------------------------- Wi-Fi
void ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  uint32_t now = millis();
  if (lastWifiAttempt != 0 && now - lastWifiAttempt < WIFI_RETRY_MS) return;
  lastWifiAttempt = now;
  Serial.println("[wifi] conectando...");
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

// ------------------------------------------------------------------ envio
// Retorna: 1 = confirmado, 0 = tentar de novo, -1 = erro permanente
int sendEvent(PendingEvent &ev) {
  HTTPClient http;
  char url[128];
  snprintf(url, sizeof(url), "http://%s:%d/api/events", SERVER_HOST, SERVER_PORT);
  if (!http.begin(url)) return 0;
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.setConnectTimeout(HTTP_TIMEOUT_MS);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Arena-Token", DEVICE_TOKEN);

  char body[160];
  uint32_t age = millis() - ev.pressedAt;
  snprintf(body, sizeof(body), "{\"event_id\":\"%s\",\"device_id\":\"%s\",\"age_ms\":%lu}",
           ev.id, DEVICE_ID, (unsigned long)age);
  int code = http.POST((uint8_t *)body, strlen(body));
  http.end();

  Serial.printf("[envio] %s tentativa %d -> HTTP %d\n", ev.id, ev.attempts + 1, code);
  if (code == 200 || code == 201) return 1;
  if (code == 401 || code == 403 || code == 422) return -1;  // token/dados errados: não adianta repetir
  return 0;  // timeout, servidor fora do ar, 5xx...
}

void processQueue() {
  if (WiFi.status() != WL_CONNECTED) return;
  uint32_t now = millis();
  for (int i = 0; i < QUEUE_SIZE; i++) {
    PendingEvent &ev = queueEvents[i];
    if (!ev.used || now < ev.nextAttemptAt) continue;

    if (now - ev.pressedAt > MAX_EVENT_AGE_MS) {
      Serial.printf("[envio] %s expirou sem confirmação\n", ev.id);
      ev.used = false;
      ledOverrideMode = 2; ledOverrideStart = now; ledOverrideUntil = now + 1500;
      continue;
    }
    int result = sendEvent(ev);
    now = millis();
    if (result == 1) {
      ev.used = false;
      ledOverrideMode = 1; ledOverrideStart = now; ledOverrideUntil = now + 500;
    } else if (result == -1) {
      Serial.println("[envio] rejeitado pelo servidor: confira DEVICE_TOKEN e DEVICE_ID");
      ev.used = false;
      ledOverrideMode = 2; ledOverrideStart = now; ledOverrideUntil = now + 1500;
    } else {
      ev.attempts++;
      uint32_t backoff = min((uint32_t)5000, (uint32_t)(250UL << min((int)ev.attempts, 5)));
      ev.nextAttemptAt = now + backoff;
    }
    return;  // um envio por volta do loop, para continuar atendendo o botão
  }
}

// -------------------------------------------------------------------- LED
void updateLed() {
  uint32_t now = millis();
  bool on;
  if (now < ledOverrideUntil) {
    if (ledOverrideMode == 1) on = true;
    else on = ((now - ledOverrideStart) / 250) % 2 == 0;  // 3 piscadas
  } else if (WiFi.status() != WL_CONNECTED) {
    on = (now / 500) % 2 == 0;
  } else if (pendingCount() > 0) {
    on = (now / 80) % 2 == 0;
  } else {
    on = (now % 3000) < 60;
  }
  digitalWrite(LED_PIN, on ? HIGH : LOW);
}

// ------------------------------------------------------------------ setup
void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  bootId = esp_random();
  memset(queueEvents, 0, sizeof(queueEvents));

  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.setSleep(false);  // menor latência no envio
  ensureWifi();

  attachInterrupt(digitalPinToInterrupt(BUTTON_PIN), onButton, FALLING);
  Serial.printf("\nArena Replay botão | dispositivo=%s boot=%08lx servidor=%s:%d\n",
                DEVICE_ID, (unsigned long)bootId, SERVER_HOST, SERVER_PORT);
}

void loop() {
  if (isrPressed) {
    noInterrupts();
    uint32_t pressedAt = isrPressAt;
    isrPressed = false;
    interrupts();
    // Confirma que não foi ruído: se notamos o toque logo, o botão ainda deve estar apertado
    bool recent = millis() - pressedAt < 100;
    if (recent) delay(15);
    if (!recent || digitalRead(BUTTON_PIN) == LOW) enqueuePress(pressedAt);
  }

  static wl_status_t lastStatus = WL_IDLE_STATUS;
  wl_status_t st = WiFi.status();
  if (st != lastStatus) {
    if (st == WL_CONNECTED) Serial.printf("[wifi] conectado, IP %s\n", WiFi.localIP().toString().c_str());
    else if (lastStatus == WL_CONNECTED) Serial.println("[wifi] conexão perdida");
    lastStatus = st;
  }

  ensureWifi();
  processQueue();
  updateLed();
  delay(5);
}
