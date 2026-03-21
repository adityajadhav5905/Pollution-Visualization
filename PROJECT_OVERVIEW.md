# Mobile Air Pollution Monitoring System - Simple Overview

## What this project does

This project turns a mobile device into an air pollution monitor and then shows pollution heat maps on a webpage. The system collects air quality and location data while moving, saves it, and turns it into visual maps.

## Main building blocks

- **ESP32 board**: small computer that reads sensors and location.
- **Sensors**: temperature, humidity, small particles (PM2.5/PM10), carbon monoxide (CO), and other gases.
- **GPS module**: gives the location (latitude/longitude).
- **Data file**: saved as CSV, one line per reading.
- **Web app**: runs locally and shows interactive maps.

## How data is gathered

1. The device takes measurements every 20 seconds.
2. Each measurement includes where you are (GPS) and how polluted the air is.
3. Data is saved in a file like:
   - `Latitude, Longitude, Temperature, Humidity, PM2.5, PM10, CO, MQ135, Timestamp`

## How data is used

1. Start the Python web app: `python app.py`.
2. Open a browser to `http://127.0.0.1:5000/`.
3. Upload your CSV data (or run over MQTT live stream if you have it).
4. Click to generate heatmaps.
5. View maps showing pollutant concentration across places.

## What you get in the dashboard

- Heatmap for temperature
- Heatmap for humidity
- Heatmap for PM2.5 and PM10
- Heatmap for CO levels
- Heatmap for harmful gas index

## Why it’s useful

- See “hot spots” of pollution as color overlays.
- Compare different types of pollution side-by-side.
- Download map files and share them.

## Workflow (Simple steps)

1. Turn on the hardware and let it move around the area to collect data.
2. Collect the stored CSV file from the ESP32.
3. Launch the Flask app on your computer.
4. Upload CSV or connect MQTT.
5. Generate heatmap visuals.
6. Explore and download the maps.

## Extra features

- Live MQTT mode (stream data instantly if you have WiFi on the ESP32).
- Automatic conversion of MQTT data to the CSV format the map generator expects.
- HTML map output that works in any browser.

## Quick start (non-technical)

- Plug in ESP32 and sensors.
- Run the script in Python.
- Upload the data file.
- Click `Generate`.
- See pollution zones with colors.
