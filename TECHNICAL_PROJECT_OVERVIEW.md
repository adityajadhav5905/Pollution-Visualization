# Mobile Air Pollution Monitoring System - Technical Overview

## 1. Architecture Summary

- ESP32 (edge device) collects sensor values and location while moving.
- Data is transmitted via MQTT (optional) and stored as CSV locally by the logger.
- A Python/Flask backend ingests data and converts it into visual heatmaps using Folium.
- Client interface served through Flask shows heatmaps and exposes files for download.

## 2. Hardware Interface and Sensor Data

### 2.1 Sensors

- DHT11: temperature, humidity
- NEO-6M GPS: latitude, longitude, timestamp
- DSM501A: PM2.5, PM10
- MQ-7: CO
- MQ-135: NH3, SO2, NOx, Benzene (aggregated gas index)

### 2.2 ESP32 Data Flow

1. Initialize peripherals (I2C/UART/GPIO/A/D).
2. Read sensors every 20 seconds.
3. Acquire GPS fix, skip or retry if no coordinates.
4. Build JSON object with fields:
   - Latitude, Longitude
   - Temperature, Humidity
   - PM2.5, PM10
   - CO
   - MQ135_Gas (and any as extras)
   - Timestamp
5. Log to local CSV file (`data.csv`) and/or publish to MQTT topic.

## 3. Data Logging and CSV format

- Each row: `Latitude,Longitude,Temperature,Humidity,PM2.5,PM10,CO,MQ135_Gas,Timestamp`
- Sensor rows appended every 20s during runtime.
- GPS-frame moving path enables heatmap density and intensity.

## 4. MQTT Ingestion

- JSON payload accepted via MQTT.
- Required fields: latitude + longitude (critical for geospatial mapping).
- Optional fields: sensor values.
- MQTT slot in Flask: subscribe to topic, parse JSON, append to CSV, update runtime state.

## 5. Python Preprocessing (Heatmap Generator)

- Read CSV into `pandas.DataFrame`.
- Data cleaning:
  - drop rows with missing coords
  - convert epoch/ISO string to `datetime` if needed
- Normalization:
  - scale 0..1 or standardized quantiles by column (e.g., max-to-min) for heatmap intensity.
- Output columns are resampled in 20s windows if required.

## 6. Folium Heatmap Generation

- For each pollutant channel, create Folium map: `folium.Map(location=[lat_mean, lon_mean], zoom_start=13)`.
- Add `folium.plugins.HeatMap` with points list of `[lat, lon, weight]`.
- Generate separate HTML files:
  - temperature
  - humidity
  - PM2.5
  - PM10
  - CO
  - MQ135 gas index
- Optionally export as PNG using headless server if supported.

## 7. Flask Web Application Details

### 7.1 Endpoints

- `/` : home page with upload and MQTT controls (GET/POST).
- `/upload` : CSV file upload handling.
- `/mqtt/connect` : connect to broker, receive stream.
- `/mqtt/stop` : disconnect session.
- `/heatmap/<type>` : render heatmap HTML or embedded map.
- `/download/<file>` : download generated asset.

### 7.2 Workflow in the app

1. User selects data source: local CSV or MQTT.
2. If MQTT selected, app reads `mqtt_credentials.json` for host/topic, connects and stores incoming messages.
3. The ingestion path unifies into a working CSV file.
4. On command, app triggers heatmap generation module and stores HTML results in `static/maps/`.
5. App displays interactive heatmaps in UI and provides download links.

## 8. Deployment Notes

- Dependencies: `Flask`, `pandas`, `folium`, `paho-mqtt`, `geopy` (if needed), `python-dotenv` (optional).
- Run with `python app.py` or Flask CLI.
- Ensure writing permissions for `uploads/` and `static/maps/`.

## 9. End-to-end Step-by-step Technical Workflow

1. Power up ESP32 system with sensors.
2. Start logging via ESP32 sketch (`Final/Final.ino`).
3. Sensor data written to CSV + optionally published MQTT.
4. Stop field run and retrieve dataset `gps_grid_500m.csv` or generated logs.
5. On local machine, execute `python app.py`.
6. Open browser at `http://127.0.0.1:5000/`.
7. Upload CSV or enable MQTT ingestion.
8. Monitor ingestion status and examine saved rows.
9. Press generate heatmaps.
10. View output maps and download `.html`/`.png`.

## 10. Diagnostics and validation

- Visual cross-check: map overlays correspond to expected location clusters.
- Value range checks: no negative PM values, CO in realistic range.
- Retry on GPS loss/hardware glitches.
- Add logging for each row in pipeline and HTTP events.
