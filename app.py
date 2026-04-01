"""
Main Flask Application for Pollution Monitoring

This module serves a web interface for generating heatmaps from pollution data.
It supports two input modes:
  1. CSV Upload: Users upload a CSV containing Latitude, Longitude, and Pollutant data.
  2. Live MQTT: The app subscribes to an MQTT broker, receives live JSON/CSV payloads,
     and plots them in real-time.
     
The application logic focuses purely on receiving data, sanitizing coordinate and
pollutant values, scaling them, and generating in-memory HTML strings of Folium Heatmaps
which are returned to the frontend.
"""
from flask import Flask, request, jsonify, send_file, render_template
import pandas as pd
import folium
from folium.plugins import HeatMap
import os
import time
import json
import csv
import ast
import logging
import threading
from datetime import datetime
from sklearn.preprocessing import MinMaxScaler

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None


app = Flask(__name__)

logging.basicConfig(level=logging.DEBUG)
app.logger.setLevel(logging.DEBUG)

UPLOAD_FOLDER = "/tmp"
ALLOWED_EXTENSIONS = {"csv"}
MQTT_CSV_FILENAME = "mqtt_stream_data.csv"
MQTT_CSV_PATH = os.path.join(UPLOAD_FOLDER, MQTT_CSV_FILENAME)
MQTT_DEFAULTS_FILE = "mqtt_credentials.json"

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

mqtt_lock = threading.Lock()
mqtt_client = None
mqtt_rows = []
mqtt_state = {
    "connected": False,
    "broker": "",
    "port": 1883,
    "topic": "",
    "client_id": "",
    "username": "",
    "keepalive": 60,
    "qos": 0,
    "messages_received": 0,
    "last_message_at": None,
    "last_payload_preview": "",
    "last_error": "",
    "started_at": None,
}


def compute_column_variation(rows, max_recent=15):
    """
    Compute the last `max_recent` records and a flag per column indicating
    whether that column has ever varied (more than one distinct non-null value)
    across all rows seen so far.
    """
    if not rows:
        return [], {}

    df_all = pd.DataFrame(rows)
    df_recent = df_all.tail(max_recent)

    variation = {}
    for col in df_all.columns:
        series = df_all[col].dropna()
        variation[col] = series.nunique(dropna=True) > 1

    recent_rows = df_recent.to_dict(orient="records")
    return recent_rows, variation


def load_mqtt_defaults():
    """ Load default MQTT broker settings from mqtt_credentials.json, if available. """
    defaults = {
        "broker": "",
        "port": 1883,
        "topic": "",
        "client_id": "",
        "username": "",
        "password": "",
        "keepalive": 60,
        "qos": 0,
    }
    if not os.path.exists(MQTT_DEFAULTS_FILE):
        return defaults
    try:
        with open(MQTT_DEFAULTS_FILE, "r", encoding="utf-8") as f:
            file_data = json.load(f)
        if isinstance(file_data, dict):
            defaults.update(file_data)
    except Exception:
        pass
    return defaults


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def sanitize_column_name(column_name):
    return "".join(ch if ch.isalnum() or ch in ("_", "-", "(", ")") else "_" for ch in column_name).strip("_")


def save_mqtt_rows_to_csv():
    """ Safely save the in-memory MQTT rows to a temporary CSV file for processing or download. """
    with mqtt_lock:
        if not mqtt_rows:
            return
        df = pd.DataFrame(mqtt_rows)
    df.to_csv(MQTT_CSV_PATH, index=False)


def canonicalize_record(raw_record):
    if not isinstance(raw_record, dict):
        return None, "Payload must be a JSON object"

    gps_candidate = raw_record.get("gps") or raw_record.get("GPS")
    if isinstance(gps_candidate, dict):
        if "latitude" in gps_candidate and "latitude" not in raw_record and "Latitude" not in raw_record:
            raw_record["latitude"] = gps_candidate.get("latitude")
        if "lat" in gps_candidate and "latitude" not in raw_record and "Latitude" not in raw_record:
            raw_record["latitude"] = gps_candidate.get("lat")
        if "longitude" in gps_candidate and "longitude" not in raw_record and "Longitude" not in raw_record:
            raw_record["longitude"] = gps_candidate.get("longitude")
        if "lon" in gps_candidate and "longitude" not in raw_record and "Longitude" not in raw_record:
            raw_record["longitude"] = gps_candidate.get("lon")

    key_aliases = {
        "latitude": "Latitude",
        "lat": "Latitude",
        "longitude": "Longitude",
        "lon": "Longitude",
        "lng": "Longitude",
        "temperature": "Temperature",
        "temp": "Temperature",
        "humidity": "Humidity",
        "co": "CO",
        "mq7": "CO",
        "mq_7": "CO",
        "mq7_sensor_1": "CO",
        "mq7_sensor_2": "CO_Secondary",
        "pm25": "PM2.5",
        "pm2.5": "PM2.5",
        "dust_simulated": "PM2.5",   # legacy alias
        "dust": "PM2.5",
        "pm10": "PM10",
        "timestamp": "Timestamp",
        "time": "Timestamp",
    }
    normalized = {}
    for key, value in raw_record.items():
        if key is None:
            continue
        if isinstance(value, (dict, list, tuple, set)):
            # Skip nested/raw structures in saved rows; keep flattened numeric fields only.
            continue
        cleaned_key = str(key).strip()
        canonical_key = key_aliases.get(cleaned_key.lower(), cleaned_key)
        normalized[canonical_key] = value

    if "Latitude" not in normalized or "Longitude" not in normalized:
        return None, "Payload must include Latitude and Longitude"

    try:
        normalized["Latitude"] = float(normalized["Latitude"])
        normalized["Longitude"] = float(normalized["Longitude"])
    except (TypeError, ValueError):
        return None, "Latitude/Longitude must be numeric"

    for key, value in list(normalized.items()):
        if key in ("Latitude", "Longitude", "Timestamp"):
            continue
        try:
            normalized[key] = float(value)
        except (TypeError, ValueError):
            normalized[key] = value

    if "Timestamp" not in normalized or not str(normalized["Timestamp"]).strip():
        normalized["Timestamp"] = datetime.utcnow().isoformat() + "Z"

    return normalized, None


def parse_mqtt_payload(payload):
    payload = payload.strip()
    if not payload:
        return None, "Empty payload"

    try:
        parsed = json.loads(payload)
        if isinstance(parsed, dict):
            return canonicalize_record(parsed)
        return None, "JSON payload must be an object"
    except json.JSONDecodeError:
        pass

    try:
        reader = csv.DictReader([payload])
        row = next(reader, None)
        if row:
            return canonicalize_record(row)
    except Exception:
        pass

    return None, "Unsupported payload format. Use JSON object or CSV header-based record."


def on_mqtt_connect(client, userdata, flags, rc):
    if rc == 0:
        with mqtt_lock:
            mqtt_state["connected"] = True
            mqtt_state["last_error"] = ""
            topic = mqtt_state["topic"]
            qos = mqtt_state["qos"]
        client.subscribe(topic, qos=qos)
        app.logger.info("MQTT connected to %s:%s and subscribed to %s", mqtt_state["broker"], mqtt_state["port"], topic)
    else:
        with mqtt_lock:
            mqtt_state["connected"] = False
            mqtt_state["last_error"] = f"MQTT connection failed with rc={rc}"
        app.logger.error(mqtt_state["last_error"])


# def on_mqtt_disconnect(client, userdata, rc):
#     with mqtt_lock:
#         mqtt_state["connected"] = False
#         if rc != 0:
#             mqtt_state["last_error"] = f"Unexpected disconnect rc={rc}"
#     app.logger.warning("MQTT disconnected with rc=%s", rc)

def on_mqtt_disconnect(client, userdata, rc):
    with mqtt_lock:
        mqtt_state["connected"] = False
        if rc != 0:
            mqtt_state["last_error"] = f"Unexpected disconnect rc={rc}"
    app.logger.warning("MQTT disconnected with rc=%s", rc)
    if rc != 0:
        time.sleep(2)
        try:
            client.reconnect()
            app.logger.info("MQTT reconnected successfully")
        except Exception as e:
            app.logger.error("MQTT reconnect failed: %s", e)


def on_mqtt_message(client, userdata, msg):
    payload_text = msg.payload.decode("utf-8", errors="ignore")
    app.logger.debug("RAW MQTT PAYLOAD: %s", payload_text)
    record, error = parse_mqtt_payload(payload_text)
    app.logger.debug("PARSED RECORD: %s | ERROR: %s", record, error)
    with mqtt_lock:
        mqtt_state["last_payload_preview"] = payload_text[:250]
        mqtt_state["last_message_at"] = datetime.utcnow().isoformat() + "Z"
        current_msgs = mqtt_state.get("messages_received")
        mqtt_state["messages_received"] = (current_msgs if isinstance(current_msgs, int) else 0) + 1
    if error:
        with mqtt_lock:
            mqtt_state["last_error"] = f"Message parse error: {error}"
        app.logger.warning("MQTT parse error: %s", error)
        return

    with mqtt_lock:
        mqtt_rows.append(record)
        if len(mqtt_rows) > 10000:
            mqtt_rows.pop(0)
    save_mqtt_rows_to_csv()


POLLUTANT_THRESHOLDS = {
    # Values are approximate guide ranges for 24h exposure.
    # Units:
    #   PM2.5 / PM10 -> µg/m³
    #   CO           -> ppm
    "PM2.5": {
        "unit": "µg/m³",
        "hazardous_threshold": 120,
        "max_recorded": 500,
        "bands": [
            (0, 30, "Good"),
            (30, 60, "Acceptable"),
            (60, 90, "Poor"),
            (90, 120, "Very Poor"),
            (120, float("inf"), "Hazardous"),
        ],
    },
    "PM10": {
        "unit": "µg/m³",
        "hazardous_threshold": 350,
        "max_recorded": 1000,
        "bands": [
            (0, 50, "Good"),
            (50, 100, "Acceptable"),
            (100, 250, "Poor"),
            (250, 350, "Very Poor"),
            (350, float("inf"), "Hazardous"),
        ],
    },
    "CO": {
        "unit": "ppm",
        "hazardous_threshold": 30,
        "max_recorded": 1000,
        "bands": [
            (0, 2, "Good"),
            (2, 9, "Acceptable"),
            (9, 15, "Poor"),
            (15, 30, "Very Poor"),
            (30, float("inf"), "Hazardous"),
        ],
    },
    "CO2": {
        "unit": "ppm",
        "hazardous_threshold": 5000,
        "max_recorded": 10000,
        "bands": [
            (0, 600, "Good"),
            (600, 1000, "Acceptable"),
            (1000, 2000, "Poor"),
            (2000, 5000, "Very Poor"),
            (5000, float("inf"), "Hazardous"),
        ],
    },
    "Temperature": {
        "unit": "°C",
        "hazardous_threshold": 50,
        "max_recorded": 56.7,
        "bands": [
            (-float("inf"), 24, "Good"),
            (24, 30, "Acceptable"),
            (30, 40, "Poor"),
            (40, 50, "Very Poor"),
            (50, float("inf"), "Hazardous"),
        ],
    },
    "Temprature": {
        "unit": "°C",
        "hazardous_threshold": 50,
        "max_recorded": 56.7,
        "bands": [
            (-float("inf"), 24, "Good"),
            (24, 30, "Acceptable"),
            (30, 40, "Poor"),
            (40, 50, "Very Poor"),
            (50, float("inf"), "Hazardous"),
        ],
    },
    "Humidity": {
        "unit": "%",
        "hazardous_threshold": 90,
        "max_recorded": 100,
        "bands": [
            (-float("inf"), 50, "Good"),
            (50, 60, "Acceptable"),
            (60, 75, "Poor"),
            (75, 90, "Very Poor"),
            (90, float("inf"), "Hazardous"),
        ],
    },
    "NO2": {
        "unit": "ppb",
        "hazardous_threshold": 400,
        "max_recorded": 1000,
        "bands": [
            (0, 50, "Good"),
            (50, 100, "Acceptable"),
            (100, 200, "Poor"),
            (200, 400, "Very Poor"),
            (400, float("inf"), "Hazardous"),
        ],
    },
    "SO2": {
        "unit": "ppb",
        "hazardous_threshold": 185,
        "max_recorded": 500,
        "bands": [
            (0, 35, "Good"),
            (35, 75, "Acceptable"),
            (75, 185, "Poor"),
            (185, 304, "Very Poor"),
            (304, float("inf"), "Hazardous"),
        ],
    },
    "O3": {
        "unit": "ppb",
        "hazardous_threshold": 200,
        "max_recorded": 500,
        "bands": [
            (0, 54, "Good"),
            (54, 70, "Acceptable"),
            (70, 85, "Poor"),
            (85, 105, "Very Poor"),
            (105, float("inf"), "Hazardous"),
        ],
    },
}


def categorize_pollutant(name, value):
    """
    Determine qualitative category for the latest pollutant value.
    """
    if value is None:
        return {
            "category": "Unknown",
            "hazardous_threshold": None,
            "unit": None,
            "details": "No numeric data available.",
        }

    cfg = POLLUTANT_THRESHOLDS.get(name)
    if not cfg:
        upper_name = str(name).strip().upper()
        # Direct exact match
        for k, v in POLLUTANT_THRESHOLDS.items():
            if k.upper() == upper_name:
                cfg = v
                break
        
        # Fuzzy match (e.g., "CO (Carbon Monoxide)" -> matches "CO")
        if not cfg:
            import re
            words = set(re.findall(r'[A-Z0-9.]+', upper_name))
            for k, v in POLLUTANT_THRESHOLDS.items():
                if k.upper() in words:
                    cfg = v
                    break

    if not cfg:
        return {
            "category": "Unknown",
            "hazardous_threshold": None,
            "unit": None,
            "details": "No reference thresholds defined for this pollutant.",
        }

    category = "Unknown"
    for low, high, label in cfg["bands"]:
        if low <= value < high:
            category = label
            break

    color_map = {
        "Good": "rgb(0, 228, 0)",
        "Acceptable": "rgb(255, 255, 0)",
        "Moderate": "rgb(255, 255, 0)",
        "Poor": "rgb(255, 126, 0)",
        "Very Poor": "rgb(255, 0, 0)",
        "Hazardous": "rgb(126, 0, 35)"
    }

    bands_payload = []
    for b in cfg["bands"]:
        raw_low = b[0]
        raw_high = b[1]
        bands_payload.append({
            "low": -9999 if raw_low == -float('inf') else raw_low,
            "high": 999999 if raw_high == float('inf') else raw_high,
            "label": b[2]
        })

    return {
        "category": category,
        "hazardous_threshold": cfg["hazardous_threshold"],
        "max_safe_level": cfg["bands"][1][1] if len(cfg["bands"]) > 1 else cfg["bands"][0][1],
        "maximum_recorded": cfg.get("max_recorded", cfg["hazardous_threshold"] * 2),
        "color": color_map.get(category, "rgb(128, 128, 128)"),
        "unit": cfg["unit"],
        "bands_data": bands_payload,
        "details": f"Hazardous when ≥ {cfg['hazardous_threshold']} {cfg['unit']}.",
    }


def process_dataframe(df, settings=None):
    """
    Core function to process tabular data containing GPS coordinates and pollutant values.
    
    1. Normalizes GPS column names.
    2. Drops rows with invalid or missing coordinates.
    3. Identifies and loops over available pollutant columns.
    4. Calls `create_heatmap` for each valid pollutant.
    
    Returns:
        tuple (dict|None, str|None, int): Payload dict or None, Error message or None, HTTP Status Code.
    """
    if settings is None:
        settings = {}
        
    # Normalize incoming column names to handle case/whitespace variations.
    rename_map = {}
    for col in df.columns:
        normalized = str(col).strip().lower()
        if normalized in ("latitude", "lat"):
            rename_map[col] = "Latitude"
        elif normalized in ("longitude", "lon", "lng"):
            rename_map[col] = "Longitude"
        elif normalized in ("timestamp", "time"):
            rename_map[col] = "Timestamp"
    if rename_map:
        df = df.rename(columns=rename_map)

    # If GPS columns are missing, try extracting them from a `gps` object-like column.
    if ("Latitude" not in df.columns or "Longitude" not in df.columns) and "gps" in df.columns:
        extracted_lat = []
        extracted_lon = []
        for raw in df["gps"].tolist():
            lat_val = None
            lon_val = None
            if isinstance(raw, dict):
                lat_val = raw.get("latitude", raw.get("lat"))
                lon_val = raw.get("longitude", raw.get("lon"))
            elif isinstance(raw, str) and raw.strip():
                text = raw.strip()
                parsed = None
                try:
                    parsed = json.loads(text)
                except Exception:
                    try:
                        parsed = ast.literal_eval(text)
                    except Exception:
                        parsed = None
                if isinstance(parsed, dict):
                    lat_val = parsed.get("latitude", parsed.get("lat"))
                    lon_val = parsed.get("longitude", parsed.get("lon"))
            extracted_lat.append(lat_val)
            extracted_lon.append(lon_val)
        if "Latitude" not in df.columns:
            df["Latitude"] = extracted_lat
        if "Longitude" not in df.columns:
            df["Longitude"] = extracted_lon

    required_columns = ["Latitude", "Longitude"]
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        return None, f"Missing required columns: {', '.join(missing_columns)}", 400

    df = df.dropna(subset=required_columns).copy()
    if df.empty:
        return None, "No valid data after removing missing GPS values", 400

    for col in required_columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=required_columns)
    if df.empty:
        return None, "Latitude/Longitude values are invalid", 400

    pollutant_columns = [
        col for col in df.columns
        if col not in ("Latitude", "Longitude", "Timestamp", "gps", "GPS")
    ]
    if not pollutant_columns:
        return None, "No pollutant data columns found (third column onward)", 400

    results = []
    for col in pollutant_columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")
        clean_series = df[col].dropna()
        if clean_series.empty:
            results.append({"column": col, "error": f"No valid numeric data for {col}"})
            continue

        latest_value = float(clean_series.iloc[-1])
        stats = categorize_pollutant(col, latest_value)
        stats["latest_value"] = latest_value
        stats["min"] = float(clean_series.min())
        stats["max"] = float(clean_series.max())
        stats["mean"] = float(clean_series.mean())

        heatmap_html, error = create_heatmap(df.dropna(subset=[col]).copy(), col, settings)

        if heatmap_html is None:
            results.append({"column": col, "error": error})
        else:
            results.append(
                {
                    "column": col,
                    "map_html": heatmap_html,
                    "stats": stats,
                }
            )

    successful = [r for r in results if "map_html" in r]
    if not successful:
        return {"error": "Failed to create any heatmaps", "details": results}, None, 500
    return {"success": True, "results": results}, None, 200

def create_heatmap(df, value_column, settings=None):
    """
    Creates an interactive HTML heatmap string using Folium.
    
    Expects a DataFrame with validated 'Latitude', 'Longitude', and a pollutant column.
    The pollutant column is min-max scaled before generating the heat layer.
    """
    if settings is None:
        settings = {}
        
    try:
        app.logger.debug("Creating heatmap for column: %s", value_column)

        if not all(df["Latitude"].apply(lambda x: isinstance(x, (int, float)))):
            app.logger.error("Invalid Latitude data for %s", value_column)
            return None, "Latitude contains non-numeric values"
        if not all(df["Longitude"].apply(lambda x: isinstance(x, (int, float)))):
            app.logger.error("Invalid Longitude data for %s", value_column)
            return None, "Longitude contains non-numeric values"
        if not all(df[value_column].apply(lambda x: isinstance(x, (int, float)))):
            app.logger.error("Invalid data in column %s: non-numeric values", value_column)
            return None, f"{value_column} contains non-numeric values"

        try:
            scaler = MinMaxScaler()
            df[f"{value_column}_normalized"] = scaler.fit_transform(df[[value_column]])
        except Exception as e:
            app.logger.error("Normalization failed for %s: %s", value_column, str(e))
            return None, f"Normalization failed for {value_column}: {str(e)}"

        lat = df["Latitude"]
        lon = df["Longitude"]

        try:
            min_lat, max_lat = lat.min(), lat.max()
            min_lon, max_lon = lon.min(), lon.max()
            lat_center = lat.mean()
            lon_center = lon.mean()
        except Exception as e:
            app.logger.error("Bounds calculation failed for %s: %s", value_column, str(e))
            return None, f"Bounds calculation failed: {str(e)}"

        # Allow single-axis movement (e.g., mostly same latitude on a straight path).
        # Only fail when both latitude and longitude are effectively identical.
        eps = 1e-4
        if min_lat == max_lat and min_lon == max_lon:
            app.logger.error("Invalid bounds for %s: identical coordinates", value_column)
            return None, "All points have identical coordinates"
        if min_lat == max_lat:
            min_lat -= eps
            max_lat += eps
        if min_lon == max_lon:
            min_lon -= eps
            max_lon += eps

        # Extract settings with defaults
        zoom_start = float(settings.get("zoom", 5))
        radius = float(settings.get("radius", 36))
        blur = float(settings.get("blur", 55))
        min_op = float(settings.get("min_opacity", 0.01))
        max_op = float(settings.get("max_opacity", 0.05))

        heatmap = folium.Map(
            location=[lat_center, lon_center],
            tiles="CartoDB positron",
            zoom_start=zoom_start,
            control_scale=False,
            zoom_control=False,
            scrollWheelZoom=False,
        )

        try:
            heatmap.fit_bounds([[min_lat, min_lon], [max_lat, max_lon]], padding=(50, 50))
        except Exception as e:
            app.logger.error("Fit bounds failed for %s: %s", value_column, str(e))
            return None, f"Fit bounds failed: {str(e)}"

        heat_data = [[row["Latitude"], row["Longitude"], row[f"{value_column}_normalized"]] for _, row in df.iterrows()]

        gradient = {
            0.0: "#00e400",
            0.2: "#ffff00",
            0.4: "#ff7e00",
            0.6: "#ff0000",
            0.8: "#8f3f97",
            1.0: "#7e0023",
        }

        try:
            HeatMap(heat_data, radius=radius, blur=blur, min_opacity=min_op, max_opacity=max_op, gradient=gradient).add_to(heatmap)
            
            # Extract HTML string directly
            map_html = heatmap.get_root().render()
            return map_html, None
        except Exception as e:
            app.logger.error("Heatmap creation failed for %s: %s", value_column, str(e))
            return None, f"Heatmap creation failed for {value_column}: {str(e)}"

    except Exception as e:
        app.logger.error("Unexpected error in heatmap creation for %s: %s", value_column, str(e))
        return None, f"Unexpected error in heatmap creation for {value_column}: {str(e)}"


@app.route("/")
def home():
    global mqtt_client
    try:
        if mqtt_client is not None:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
            mqtt_client = None
    except Exception:
        pass

    with mqtt_lock:
        mqtt_rows.clear()
        mqtt_state["connected"] = False
        mqtt_state["messages_received"] = 0
        mqtt_state["last_message_at"] = None
        mqtt_state["last_payload_preview"] = ""
        mqtt_state["last_error"] = ""
        if os.path.exists(MQTT_CSV_PATH):
            try:
                os.remove(MQTT_CSV_PATH)
            except Exception:
                pass
    return render_template("index.html", mqtt_defaults=load_mqtt_defaults())


@app.route("/upload", methods=["POST"])
def upload_file():
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file uploaded"}), 400

        file = request.files["file"]

        if file.filename == "":
            return jsonify({"error": "No file selected"}), 400

        if not allowed_file(file.filename):
            return jsonify({"error": "Invalid file type. Please upload a CSV file"}), 400

        try:
            df = pd.read_csv(file)
            app.logger.debug("CSV columns: %s", df.columns.tolist())
        except Exception as e:
            app.logger.error("Error reading CSV file: %s", str(e))
            return jsonify({"error": f"Error reading CSV file: {str(e)}"}), 400

        settings_str = request.form.get("settings", "{}")
        try:
            settings = json.loads(settings_str)
        except Exception:
            settings = {}

        payload, error, status = process_dataframe(df, settings)
        if error:
            return jsonify({"error": error}), status
        return jsonify(payload), status
    except Exception as e:
        app.logger.error("Unexpected error in upload: %s", str(e))
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500


@app.route("/mqtt/connect", methods=["POST"])
def mqtt_connect():
    global mqtt_client
    if mqtt is None:
        return jsonify({"error": "paho-mqtt is not installed. Run: pip install paho-mqtt"}), 500

    data = request.get_json(silent=True) or {}
    broker = str(data.get("broker", "")).strip()
    topic = str(data.get("topic", "")).strip()
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    client_id = str(data.get("client_id", "")).strip() or f"pollution-monitor-{int(time.time())}"

    try:
        port = int(data.get("port", 1883))
        keepalive = int(data.get("keepalive", 60))
        qos = int(data.get("qos", 0))
    except ValueError:
        return jsonify({"error": "Port, Keepalive and QoS must be numeric"}), 400

    if not broker or not topic:
        return jsonify({"error": "Broker and Topic are required"}), 400
    if qos not in (0, 1, 2):
        return jsonify({"error": "QoS must be 0, 1, or 2"}), 400

    try:
        if mqtt_client is not None:
            try:
                mqtt_client.loop_stop()
                mqtt_client.disconnect()
            except Exception:
                pass

        mqtt_client = mqtt.Client(client_id=client_id, clean_session=True)
        if username:
            mqtt_client.username_pw_set(username=username, password=password)
        mqtt_client.on_connect = on_mqtt_connect
        mqtt_client.on_disconnect = on_mqtt_disconnect
        mqtt_client.on_message = on_mqtt_message

        with mqtt_lock:
            mqtt_state["broker"] = broker
            mqtt_state["port"] = port
            mqtt_state["topic"] = topic
            mqtt_state["client_id"] = client_id
            mqtt_state["username"] = username
            mqtt_state["keepalive"] = keepalive
            mqtt_state["qos"] = qos
            mqtt_state["messages_received"] = 0
            mqtt_state["last_message_at"] = None
            mqtt_state["last_payload_preview"] = ""
            mqtt_state["last_error"] = ""
            mqtt_state["started_at"] = datetime.utcnow().isoformat() + "Z"
        with mqtt_lock:
            mqtt_rows.clear()
        if os.path.exists(MQTT_CSV_PATH):
            os.remove(MQTT_CSV_PATH)

        mqtt_client.connect(broker, port, keepalive=keepalive)
        mqtt_client.loop_start()
        return jsonify({"success": True, "message": "MQTT connection initiated", "csv_path": f"/{MQTT_CSV_PATH.replace(os.sep, '/')}"})
    except Exception as e:
        with mqtt_lock:
            mqtt_state["connected"] = False
            mqtt_state["last_error"] = str(e)
        app.logger.error("MQTT connect error: %s", str(e))
        return jsonify({"error": f"Failed to connect MQTT: {str(e)}"}), 500


@app.route("/mqtt/disconnect", methods=["POST"])
def mqtt_disconnect():
    global mqtt_client
    try:
        if mqtt_client is not None:
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
            mqtt_client = None
        with mqtt_lock:
            mqtt_state["connected"] = False
        return jsonify({"success": True, "message": "MQTT disconnected"})
    except Exception as e:
        app.logger.error("MQTT disconnect error: %s", str(e))
        return jsonify({"error": f"Failed to disconnect MQTT: {str(e)}"}), 500


@app.route("/mqtt/status", methods=["GET"])
def mqtt_status():
    with mqtt_lock:
        payload = dict(mqtt_state)
        payload["rows_buffered"] = len(mqtt_rows)
        recent_rows, variation = compute_column_variation(mqtt_rows, max_recent=15)
        payload["recent_rows"] = recent_rows
        payload["column_variation"] = variation
    payload["csv_exists"] = os.path.exists(MQTT_CSV_PATH)
    payload["csv_file"] = f"/{MQTT_CSV_PATH.replace(os.sep, '/')}" if payload["csv_exists"] else ""
    return jsonify(payload)


@app.route("/mqtt/process", methods=["POST"])
def mqtt_process():
    with mqtt_lock:
        rows_snapshot = list(mqtt_rows)

    if rows_snapshot:
        df = pd.DataFrame(rows_snapshot)
    elif os.path.exists(MQTT_CSV_PATH):
        try:
            df = pd.read_csv(MQTT_CSV_PATH)
        except Exception as e:
            return jsonify({"error": f"Failed to read MQTT CSV: {str(e)}"}), 500
    else:
        return jsonify({"error": "No MQTT data received yet"}), 400

    data = request.get_json(silent=True) or {}
    settings = data.get("settings", {})

    payload, error, status = process_dataframe(df, settings)
    if error or not isinstance(payload, dict):
        return jsonify({"error": error or "Unknown processing error"}), status
    assert isinstance(payload, dict)
    payload["source"] = "mqtt"
    payload["csv_path"] = f"/{MQTT_CSV_PATH.replace(os.sep, '/')}" if os.path.exists(MQTT_CSV_PATH) else ""
    return jsonify(payload), status


@app.route("/download/<path:filename>")
def download_file(filename):
    try:
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        app.logger.debug("Attempting to download: %s", file_path)
        if not os.path.exists(file_path):
            app.logger.error("File not found: %s", file_path)
            return jsonify({"error": f"File {filename} not found"}), 404
        app.logger.info("Serving file: %s", file_path)
        return send_file(file_path, as_attachment=True, download_name=filename)
    except Exception as e:
        app.logger.error("Download error for %s: %s", filename, str(e))
        return jsonify({"error": f"Error serving file: {str(e)}"}), 500


@app.route("/clear", methods=["POST"])
def clear_files():
    try:
        deleted_files = []
        if os.path.exists(MQTT_CSV_PATH):
            os.remove(MQTT_CSV_PATH)
            deleted_files.append(MQTT_CSV_PATH)
        with mqtt_lock:
            mqtt_rows.clear()
            mqtt_state["messages_received"] = 0
            mqtt_state["last_message_at"] = None
            mqtt_state["last_payload_preview"] = ""
            mqtt_state["last_error"] = ""
        app.logger.info("Cleared files: %s", deleted_files)
        return jsonify({"success": True, "deleted": deleted_files})
    except Exception as e:
        app.logger.error("Clear files error: %s", str(e))
        return jsonify({"error": f"Failed to clear files: {str(e)}"}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True ,)
