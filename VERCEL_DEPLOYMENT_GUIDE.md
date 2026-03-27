# Vercel Deployment Plan & Configuration Guide

This document details exactly how this Flask application has been configured for Vercel deployment, and what steps you need to take on the Vercel Dashboard to deploy it successfully.

---

## 1. Codebase Configurations (What we changed)

Vercel is a **Serverless** platform. This means it doesn't run a continuous server; instead, it spins up an isolated "Function" container every time an HTTP request comes in, processes the request, and goes to sleep. Because of this, standard Python server practices had to be optimized.

### A. The Setup File (`vercel.json`)
Vercel requires continuous instruction on how to treat a Python application. We've added `vercel.json` to the root directory:
```json
{
  "version": 2,
  "builds": [
    {
      "src": "app.py",
      "use": "@vercel/python"
    }
  ],
  "routes": [
    {
      "src": "/(.*)",
      "dest": "app.py"
    }
  ]
}
```
* **`builds`**: Tells Vercel to use its native Python builder (`@vercel/python`) and marks `app.py` as the entry point.
* **`routes`**: Forces all incoming web traffic (URLs, endpoints) to be mapped down to the `app.py` function.

### B. The Application Object (`app.py`)
Vercel expects to find a variable named `app` exposed in the entry file. 
```python
app = Flask(__name__)
```
This acts as the bridge. Vercel automatically translates incoming traffic into a format your standard `app` handles.

### C. File System Restrictions (`/tmp` override)
**Vercel's file system is essentially read-only.**
To prevent the application from crashing when generating files:
* We removed backend image saving entirely (`folium` maps are now served in-memory to the frontend).
* We updated the `UPLOAD_FOLDER` from `"static"` to `"/tmp"`. Vercel allows temporary, volatile storage up to 500MB only in the `/tmp` path directory. This guarantees your MQTT logging function won't trigger a 500 Server Error Permission Denied.

### D. Memory Optimization (`requirements.txt`)
Vercel serverless functions have an absolute maximum memory footprint (usually 250MB for free tiers). 
* `selenium` and `webdriver` were completely removed, minimizing the build size to keep the lambda fast and lightweight.

---

## 2. Vercel Dashboard Configurations

Deploying the prepped codebase is straightforward:

1. **Commit to GitHub:** Ensure all recent changes (including `vercel.json` and the updated `app.py`) are pushed to your GitHub Repository.
2. **Import Project:** Go to the [Vercel Dashboard](https://vercel.com/dashboard) and click **"Add New" > "Project"**. Import your GitHub repository.
3. **Configure Settings:**
   * **Framework Preset:** Leave as `"Other"`. Vercel will automatically detect `vercel.json`.
   * **Root Directory:** `./` (If the code is in the root folder).
   * **Build Command:** Leave empty (Vercel installs from `requirements.txt` via `@vercel/python` automatically).
   * **Install Command:** Leave empty.
   * **Environment Variables:** Currently, no `.env` variables are required for standard operation.
4. **Deploy:** Click the **Deploy** button. Vercel will take under ~2 minutes to assign packages and deploy your URL.

---

## 3. Crucial Note on MQTT & Serverless Limits

Vercel Functions are **Stateless** and go to "sleep" the millisecond they have finished returning an HTTP response to the browser.
* **The issue:** The background multi-threading loop `mqtt_client.loop_start()` inside `app.py` will experience frozen intervals. It will only capture MQTT sensor data while the Vercel function stays "warm" during active API polling.
* **The Workaround:** The JavaScript frontend actively pings `/mqtt/status` every 3 seconds to attempt to keep the instance relatively awake. While it is awake, your `app.py` logic reliably dumps packets into `/tmp/mqtt_stream_data.csv`.
* **The Caveat:** When testing your physical ESP32 sensors via the active cloud deployment, keep the browser tab open. If you close the tab, the Vercel app goes to sleep, and it will miss background MQTT logs untill you ping it again.
