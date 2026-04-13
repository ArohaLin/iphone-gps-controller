[English](README.md) | [繁體中文](README.zh-TW.md)

# 📍 iPhone GPS Controller

A tool for real-time iPhone GPS location simulation via USB from a Mac.  
`gps_launcher.py` handles device discovery and exposes an HTTP API;  
`gps_map.html` provides an interactive Leaflet map interface for controlling GPS.

> **Author:** Aroha Lin · **License:** MIT · **Copyright (c) 2026 Aroha Lin**  
> **Repo:** https://github.com/ArohaLin/iphone-gps-controller

---

## Table of Contents

1. [Features](#features)
2. [Requirements](#requirements)
3. [Installation](#installation)
4. [Quick Start](#quick-start)
5. [Backend — gps_launcher.py](#backend--gps_launcherpy)
6. [Frontend — gps_map.html Usage](#frontend--gps_maphtml-usage)
7. [HTTP API Reference](#http-api-reference)
8. [Keyboard Shortcuts](#keyboard-shortcuts)
9. [Troubleshooting](#troubleshooting)
10. [Third-Party Licenses](#third-party-licenses)

---

## Features

| Feature | Description |
|---------|-------------|
| 📍 Click-to-set GPS | Click the map or enter coordinates to instantly push to iPhone |
| 🧭 Directional Move | 8-direction pad with configurable step distance (meters) |
| 🗺 Cruise Mode | Multi-waypoint route planning with auto per-second advancement and loop support |
| 📤 GPX Export | Export waypoint routes as standard `.gpx` files |
| 🔍 Place Search | Search global locations via Nominatim (OpenStreetMap) |
| ⭐ Favorites | Save frequently-used locations in browser localStorage |
| 🕐 Local Time | Display local time at target coordinates (Open-Meteo API) |
| 📱 Multi-Device | Manage multiple iPhones simultaneously with seamless switching |
| 🔄 Auto-Reconnect | Automatically retries on tunnel or GPS disconnect |

---

## Requirements

| Item | Requirement |
|------|-------------|
| **OS** | macOS (requires `sudo` to create USB tunnel) |
| **Python** | 3.8 or higher |
| **iPhone iOS** | iOS 16 or higher |
| **Developer Mode** | **Must be enabled** on iPhone (Settings → Privacy & Security → Developer Mode) |
| **Connection** | USB (Lightning or USB-C) |
| **Browser** | Chrome / Firefox / Safari (Clipboard API support required) |
| **Network** | Backend is local-only; Search / Timezone features require internet |

---

## Installation

### 1. Install Python Packages

```bash
pip install aiohttp pymobiledevice3
```

> Using a virtual environment:
> ```bash
> python3 -m venv venv
> source venv/bin/activate
> pip install aiohttp pymobiledevice3
> ```

### 2. Trust the Device

1. Connect iPhone to Mac via USB cable
2. Tap **Trust** when iPhone prompts "Trust This Computer?"
3. Ensure `usbmuxd` is running on Mac (usually starts automatically)

### 3. Enable Developer Mode on iPhone (Required)

This tool uses the DVT (DeveloperTools) service to simulate GPS.  
**Developer Mode is mandatory** — without it, the USB tunnel cannot be established and all devices will remain in red (failed) status.

**Method A — Command line (iPhone must be unlocked and trusted):**

```bash
python3 -m pymobiledevice3 amfi enable-developer-mode
```

**Method B — Manual:**

1. On iPhone: **Settings → Privacy & Security → Developer Mode**
2. Toggle on → tap **Restart** to confirm
3. After reboot, confirm **Turn On Developer Mode** when prompted

> ⚠️ Developer Mode takes effect only after the iPhone restarts.  
> ⚠️ Avoid enabling Developer Mode on untrusted networks.

### 4. Download Files

```
iphone-gps-controller/
├── gps_launcher.py   ← Backend Python service
└── gps_map.html      ← Frontend map interface
```

---

## Quick Start

### Step 1: Start the Backend

```bash
python3 gps_launcher.py
```

On successful startup, the terminal will show:

```
09:00:00 INFO    🚀 GPS Launcher port=8090
09:00:00 INFO     GET  http://localhost:8090/devices
09:00:00 INFO     POST http://localhost:8090/device/{idx}/set
09:00:00 INFO     POST http://localhost:8090/device/{idx}/clear
09:00:00 INFO    Scanning USB devices...
09:00:06 INFO    Device found: Aroha's iPhone (A1B2)
09:00:14 INFO    [Aroha's iPhone] Starting tunnel...
09:00:18 INFO    [Aroha's iPhone] ✅ Tunnel OK fd12::1:8a:0:0%utun3:61234
09:00:18 INFO    [Aroha's iPhone] ✅ GPS connected
```

> ⚠️ **Tunnel creation requires `sudo`** — macOS will prompt for your password the first time.

### Step 2: Open the Frontend Map

Open `gps_map.html` directly in your browser:

```bash
open gps_map.html
# or drag-and-drop onto any browser window
```

### Step 3: Set a GPS Location

1. Confirm the iPhone shows a **green dot (Connected)** in the device list
2. Click anywhere on the map → iPhone GPS updates instantly
3. A toast notification in the top-right corner confirms success

---

## Backend — gps_launcher.py

### Command-Line Arguments

```bash
python3 gps_launcher.py [PORT]
```

| Argument | Description | Default |
|----------|-------------|---------|
| `PORT` | HTTP API listening port | `8090` |

Example — use a custom port:

```bash
python3 gps_launcher.py 9000
```

### Internal Constants (editable in source)

| Constant | Description | Default |
|----------|-------------|---------|
| `SCAN_SEC` | USB device scan interval (seconds) | `6` |
| `TUNNEL_TIMEOUT` | Single tunnel establishment timeout (seconds) | `40` |
| `TUNNEL_RETRIES` | Number of tunnel retry attempts on failure | `3` |
| `TUNNEL_RETRY_SEC` | Wait between retry attempts (seconds) | `5` |
| `DEVICE_BOOT_WAIT` | Seconds to wait after device detection before tunneling | `8` |

### Internal Architecture

```
Startup
 └─ device_scanner  (every 6 seconds)
      ├─ New device detected → setup_device (wait 8s → start tunnel → gps_worker)
      └─ Device removed      → terminate tunnel

gps_worker  (one persistent coroutine per device)
 └─ Connect RSD → DvtProvider → LocationSimulation
      ├─ Receives SetCmd(lat, lon) → loc.set(lat, lon)
      └─ Receives ClearCmd        → loc.clear()
```

The launcher uses **three fallback methods** for USB device scanning:
1. `pymobiledevice3` Python API (preferred)
2. CLI subprocess (`python -m pymobiledevice3 usbmux list`)
3. Regex UDID extraction from CLI output (last resort)

### Stopping the Service

Press `Ctrl + C` for a graceful shutdown.

---

## Frontend — gps_map.html Usage

The frontend polls the backend API (default `http://localhost:8090`) every **1.5 seconds** to update device status.

---

### 📍 Normal Mode

**Click to Set Position**
- Click anywhere on the map → GPS coordinates are instantly pushed to iPhone
- The HUD in the bottom-right corner shows live cursor coordinates

**Manual Coordinate Input**
1. Enter values in the **Latitude** and **Longitude** fields in the sidebar
2. Click the **Go** button to confirm

**Direction Pad**
- 8-direction buttons: N / NE / E / SE / S / SW / W / NW
- Set **step distance** in the input field (meters, default 10 m)
- Keyboard control also available (see [Keyboard Shortcuts](#keyboard-shortcuts))

**Copy Coordinates**
- Click the 📋 icon → copies `lat,lon` to clipboard (6 decimal places)

**Clear GPS Simulation**
- Click **Clear GPS** → removes simulated location; iPhone reverts to real GPS

**Device Status Card**

| Field | Description |
|-------|-------------|
| Connected | Whether the GPS service is successfully linked |
| Simulating | Whether fake coordinates are currently being sent |
| Set Count | Total GPS pushes since the launcher started |
| Uptime | Elapsed time since device was detected |

**Timezone Info**
- **Local Time**: timezone queried via Open-Meteo API, then converted
- **Cross-Day**: whether the target location is on a different date than Taiwan (⚠ yellow warning)

---

### 🗺 Cruise Mode

**Adding Waypoints**
1. Switch to the **Cruise** tab
2. Click the map to add waypoints in sequence (yellow numbered circle markers)
3. **Drag** markers to adjust positions
4. **Double-click** a marker to delete it
5. **⎌ Undo** removes the last waypoint; **Clear All** resets the route

**Speed & Loop Settings**
- Speed: enter in km/h
- Loop: when enabled, automatically restarts from the first waypoint after reaching the end

**Route Info Panel**

| Field | Description |
|-------|-------------|
| Waypoints | Total number of added waypoints |
| Distance | Total route distance (auto-unit: m or km) |
| ETA | Estimated time to complete the full route |

**Playing a Cruise**
1. At least 2 waypoints are required
2. Click ▶ **Play** → one GPS step is pushed per second (speed-interpolated)
3. The progress bar shows current step / total steps in real time
4. Click ■ **Stop** → stays at the last position (GPS is NOT cleared)

**Export GPX**
- Click **Export GPX** → downloads `cruise_route.gpx`
- Standard GPX 1.1 format, compatible with Google Maps, Garmin, etc.

---

### ⭐ Favorites

- Location data is stored in the browser's `localStorage` (key: `gps_favorites_v1`)
- **Persistent** — survives page refreshes and browser restarts

**How to Add**
1. In Normal mode, click **⭐ Add to Favorites** → enter a label
2. From a search result Popup, click **⭐ Add to Favorites**

**Favorite Item Buttons**

| Button | Action |
|--------|--------|
| 🗺 | Pan map to this location |
| 📍 | Set as iPhone GPS position |
| ✏ | Rename |
| 🗑 | Delete |

- Each favorite displays its **local time** and **cross-day** indicator
- Click the location name (not a button) → map pans to it

---

### 🔍 Place Search

1. Type a place name in the sidebar search box (Chinese or English)
2. Press Enter or the search button → calls Nominatim API
3. Up to 7 results are shown, each with country and local time
4. Click any result → map jumps to it and a Popup appears with:
   - **📍 Set as GPS position** — pushes immediately
   - **✚ Add waypoint** (visible in Cruise mode only)
   - **⭐ Add to Favorites**

---

### Device Management

- The backend scans USB every 6 seconds; new devices **appear automatically**
- Disconnected devices are removed; the frontend auto-switches to the next connected device
- Multiple iPhones can be connected simultaneously; click a device card to select it
- Device status indicator colors:
  - 🟢 Green: GPS connected
  - 🟡 Yellow: Tunnel OK, GPS connecting
  - 🔴 Red: Tunnel establishing / failed

---

## HTTP API Reference

The backend listens on `http://127.0.0.1:{PORT}` (default 8090) with CORS fully open.

### GET `/devices`

Returns all detected devices.

**Response example:**
```json
[
  {
    "idx": 0,
    "udid": "00008110-000A1234ABCD001E",
    "name": "Aroha's iPhone",
    "ios": "17.4",
    "connected": true,
    "simulating": true,
    "last_lat": 25.03300,
    "last_lon": 121.56540,
    "set_count": 42,
    "uptime_sec": 180,
    "tunnel_ok": true,
    "error": null
  }
]
```

---

### POST `/device/{idx}/set`

Sets the GPS simulation coordinates for a specific device.

**Request Body (JSON):**
```json
{ "lat": 25.03300, "lon": 121.56540 }
```

**Success response:**
```json
{ "ok": true, "lat": 25.033, "lon": 121.5654 }
```

**Failure response:**
```json
{ "ok": false, "error": "GPS not connected" }
```

---

### POST `/device/{idx}/clear`

Clears GPS simulation for a device, restoring real GPS.

**Response:**
```json
{ "ok": true }
```

---

### GET `/device/{idx}/status`

Returns the detailed status of a single device (same schema as one item in the `/devices` array).

---

## Keyboard Shortcuts

> Shortcuts are disabled when an input field is focused.

| Key | Action |
|-----|--------|
| `↑` / `W` | Move North |
| `↓` / `S` | Move South |
| `←` / `A` | Move West |
| `→` / `D` | Move East |
| `Q` | Move Northwest |
| `E` | Move Northeast |
| `Z` | Move Southwest |
| `C` | Move Southeast |
| `+` / `=` | Zoom in |
| `-` | Zoom out |
| `F` | Re-center map on current coordinates |

---

## Troubleshooting

**Q1: No devices detected after startup?**
- Make sure iPhone is unlocked and has tapped **Trust This Computer**
- Try unplugging and re-plugging the USB cable
- Run `python3 -m pymobiledevice3 usbmux list` to verify detection

**Q2: Tunnel keeps failing (`❌ Tunnel failed after 3 attempts`)?**
- Confirm Developer Mode is enabled on iPhone — this is a required prerequisite (Settings → Privacy & Security → Developer Mode)
- Ensure Xcode Command Line Tools are up to date: `xcode-select --install`
- Try restarting the iPhone before reconnecting

**Q3: GPS set but iPhone location doesn't change?**
- Confirm the device card shows a **green dot (Connected)**
- Confirm the status card shows **Simulating: Yes**
- Some apps need to be relaunched to pick up the new location

**Q4: Frontend shows "Launcher not running"?**
- Confirm `gps_launcher.py` is running and shows `🚀 GPS Launcher port=8090`
- Check that your browser isn't blocking `localhost:8090`
- If you changed the port, update `const META = 'http://localhost:PORT';` in `gps_map.html`

**Q5: GPS disappears after stopping cruise?**
- By design: stopping a cruise **keeps the last position** — GPS is not cleared automatically
- To restore real GPS, click the **Clear GPS** button in Normal mode

---

## Third-Party Licenses

| Package | License |
|---------|---------|
| [Leaflet.js](https://leafletjs.com/) | BSD 2-Clause |
| [OpenStreetMap / Nominatim](https://www.openstreetmap.org/) | ODbL |
| [Open-Meteo API](https://open-meteo.com/) | CC BY 4.0 |
| [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) | GPL-3.0 |
| [aiohttp](https://docs.aiohttp.org/) | Apache 2.0 |

---

*Copyright (c) 2026 Aroha Lin — MIT License*
