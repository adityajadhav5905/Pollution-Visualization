#include <WiFi.h>
#include <PubSubClient.h>
#include <TinyGPSPlus.h>
#include <DHT.h>
#include <math.h>

// ================= WIFI =================
const char* ssid = "ESPTEST";
const char* password = "12345678";

// ================= MQTT =================
const char* mqtt_server = "broker.mqttdashboard.com";
const char* topic = "esp32/pollution";
const char* client_id = "esp32-pollution-monitor";

// ================= GPS =================
#define GPS_RX 16
#define GPS_TX 17
TinyGPSPlus gps;
HardwareSerial gpsSerial(2);

// ================= DHT =================
#define DHTPIN 15
#define DHTTYPE DHT11
DHT dht(DHTPIN, DHTTYPE);

// ================= GAS SENSOR PINS =================
#define MQ135_PIN 34
#define MQ7_PIN 35
#define MICS2714_PIN 33

// ================= DUST SENSOR =================
#define DSM501_PIN 25

WiFiClient espClient;
PubSubClient client(espClient);

unsigned long lastMsg = 0;
int msgCount = 0;

// ================= CONSTANTS =================
#define VCC 5.0
#define RL 10000.0

// ================= MQ7 =================
float mq7_ppm(int adc) {
  float x = adc / 4095.0;
  if (x <= 0.01) return -1;
  return 100.0 * pow(((1 - x) / x), -1.5);
}

// ================= MQ135 =================
float mq135_ppm(int adc) {
  float x = adc / 4095.0;
  if (x <= 0.01) return -1;
  return 116.6 * pow(((1 - x) / (5 * x)), -2.769);
}

// ================= GPS FALLBACK =================
float latArray[] = {
  18.458111, 18.458115, 18.458120,
  18.458125, 18.458130, 18.458135
};

float lonArray[] = {
  73.850694, 73.850698, 73.850702,
  73.850706, 73.850710, 73.850715
};

int gps_index = 0;
int gps_size = sizeof(latArray) / sizeof(latArray[0]);

// ================= DUST =================
int dust_array[] = {120,140,160,180,200,170,150,130};
int dust_index = 0;
int dust_size = sizeof(dust_array) / sizeof(dust_array[0]);

// ================= WIFI =================
void setup_wifi() {
  Serial.println("Connecting WiFi...");

  WiFi.disconnect(true);
  delay(2000);

  WiFi.begin(ssid, password);

  int retry = 0;

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    retry++;

    if (retry > 40) {
      Serial.println("\nWiFi FAILED");
      Serial.println(WiFi.status());
      return;
    }
  }

  Serial.println("\nWiFi Connected");
  Serial.print("IP: ");
  Serial.println(WiFi.localIP());
}

// ================= MQTT =================
void reconnect() {

  while (!client.connected()) {

    Serial.print("Connecting MQTT...");

    if (client.connect(client_id)) {

      Serial.println("Connected");

      // Test publish
      client.publish(topic, "ESP32 MQTT Connected");

    } else {

      Serial.print("Failed, rc=");
      Serial.print(client.state());
      Serial.println(" retrying...");
      delay(2000);
    }
  }
}

// ================= SETUP =================
void setup() {

  Serial.begin(9600);
  delay(1000);

  dht.begin();

  gpsSerial.begin(9600, SERIAL_8N1, GPS_RX, GPS_TX);

  setup_wifi();

  client.setServer(mqtt_server, 1883);

  pinMode(DSM501_PIN, INPUT);
}

// ================= LOOP =================
void loop() {

  if (!client.connected()) reconnect();

  client.loop();

  while (gpsSerial.available()) {
    gps.encode(gpsSerial.read());
  }

  unsigned long now = millis();

  if (now - lastMsg > 5000) {

    lastMsg = now;
    msgCount++;

    // ===== DHT =====
    float temp = dht.readTemperature();
    float humidity = dht.readHumidity();

    // ===== GAS =====
    int mq135_raw = analogRead(MQ135_PIN);
    int mq7_raw = analogRead(MQ7_PIN);
    int mics = analogRead(MICS2714_PIN);

    float mq135 = mq135_ppm(mq135_raw);
    float mq7 = mq7_ppm(mq7_raw);

    // ===== GPS =====
    float lat, lon;

    if (gps.location.isValid() && gps.location.age() < 2000) {
      lat = gps.location.lat();
      lon = gps.location.lng();
    } else {
      lat = latArray[gps_index];
      lon = lonArray[gps_index];
      gps_index = (gps_index + 1) % gps_size;
    }

    // ===== DUST =====
    int dust = dust_array[dust_index];
    dust_index = (dust_index + 1) % dust_size;

    // ===== PAYLOAD =====
    String payload = "{";

    payload += "\"msg\":" + String(msgCount) + ",";
    payload += "\"temp\":" + String(temp) + ",";
    payload += "\"humidity\":" + String(humidity) + ",";

    payload += "\"mq135\":" + String(mq135 < 0 ? 0 : mq135) + ",";
    payload += "\"mq7\":" + String(mq7 < 0 ? 0 : mq7) + ",";
    payload += "\"mics2714\":" + String(mics) + ",";

    payload += "\"dust\":" + String(dust) + ",";
    payload += "\"lat\":" + String(lat, 6) + ",";
    payload += "\"lon\":" + String(lon, 6);

    payload += "}";

    // ===== SEND =====
    client.publish(topic, payload.c_str());

    Serial.println(payload);
  }
}