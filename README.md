# 📍 iPhone GPS Controller

A tool for real-time iPhone GPS location simulation via USB from a Mac.  
`gps_launcher.py` handles device discovery and exposes an HTTP API;  
`gps_map.html` provides an interactive Leaflet map interface for controlling GPS.

> **Author:** Aroha Lin · **License:** MIT · **Copyright (c) 2026 Aroha Lin**  
> **Repo:** https://github.com/ArohaLin/iphone-gps-controller

🌏 **繁體中文版說明：[README.zh-TW.md](README.zh-TW.md)**

---

## What's New

This release is a major upgrade over the first version:

- **Multi-device sync** — drive several iPhones at once. A 🔗 *Sync All Devices* card broadcasts every set / clear / cruise step to all connected phones simultaneously.
- **Cruise upgrades** — pause / resume (keeps progress even if you move elsewhere meanwhile), save / load named routes, and batch-paste many coordinates at once.
- **Favorites overhaul** — manual add, automatic country name (Traditional Chinese), live local time, drag-free reordering with a pinned toolbar, and edit name + coordinates.
- **Much more robust backend** — unplug / re-plug now reconnects automatically (no launcher restart needed), USB-only device filtering, full process-group tunnel cleanup, self-healing reconnect, and file logging.

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
| 🗺 Cruise Mode | Multi-waypoint route planning with per-second advancement, loop, **pause/resume**, **save/load**, and **batch paste** |
| 📤 GPX Export | Export waypoint routes as standard `.gpx` files |
| 🔍 Place Search | Search global locations via Nominatim (OpenStreetMap) |
| ⭐ Favorites | Save locations with country name + live local time, reorder, and edit |
| 🕐 Local Time | Display local time + cross-day indicator at target coordinates (Open-Meteo API) |
| 🔗 Multi-Device Sync | Broadcast one action to all connected iPhones at once |
| 📱 Multi-Device | Manage multiple iPhones simultaneously with seamless switching |
| 🔄 Auto-Reconnect | Self-healing tunnel/GPS reconnect; clean unplug → re-plug handling |
| 📝 File Logging | Every run is logged to `gps_launcher.log` next to the script |

---

## Requirements

| Item | Requirement |
|------|-------------|
| **OS** | macOS (tunnel creation requires `sudo`) |
| **Python** | 3.8 or higher |
| **iPhone iOS** | iOS 16 or higher (iOS 17+ requires RSD tunnel + Developer Mode) |
| **Connection** | USB (Lightning or USB-C) |
| **Browser** | Chrome / Firefox / Safari (Clipboard API support required) |
| **Network** | Backend is local-only by default; Search / Timezone require internet |

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

### 3. File Structure

```
iphone-gps-controller/
├── gps_launcher.py   ← Backend Python service
└── gps_map.html      ← Frontend map interface
```

---

## Quick Start

### Step 1: Start the Backend

```bash
sudo python3 gps_launcher.py
```

> ⚠️ **Tunnel creation requires `sudo`** — macOS will prompt for your password.

On successful startup, the terminal will show:

```
20:00:00 INFO    🚀 GPS Launcher  127.0.0.1:8090  log=/path/to/gps_launcher.log
20:00:00 INFO       GET  http://localhost:8090/devices
20:00:00 INFO       POST http://localhost:8090/device/{idx}/set
20:00:00 INFO       POST http://localhost:8090/devices/set    (broadcast)
20:00:00 INFO    Scanning USB devices...
20:00:06 INFO    Device found: Aroha's iPhone (A1B2)
20:00:14 INFO    [Aroha's iPhone] Starting tunnel...
20:00:18 INFO    [Aroha's iPhone] ✅ Tunnel OK  fd12::1:8a:0:0%utun3:61234
20:00:18 INFO    [Aroha's iPhone] ✅ GPS connected
```

### Step 2: Open the Frontend Map

Open `gps_map.html` directly in your browser:

```bash
open gps_map.html
# or drag-and-drop onto any browser window
```

### Step 3: Set a GPS Location

1. Confirm the iPhone shows a **green dot (Connected)** in the device list
2. Click anywhere on the map → iPhone GPS updates instantly
3. A toast notification confirms success

---

## Backend — gps_launcher.py

### Command-Line Arguments

```bash
sudo python3 gps_launcher.py [PORT] [HOST]
```

Both arguments are **optional** — run `sudo python3 gps_launcher.py` with nothing after it and the defaults below are used. Pass them only when you want to override.

| Argument | Description | Default |
|----------|-------------|---------|
| `PORT` | HTTP API listening port | `8090` |
| `HOST` | Bind address. Use `0.0.0.0` to expose the API on your LAN; the default keeps it local-only | `127.0.0.1` |

Examples:

```bash
sudo python3 gps_launcher.py                   # use all defaults (8090, local-only)
sudo python3 gps_launcher.py 9000              # custom port
sudo python3 gps_launcher.py 8090 0.0.0.0      # expose API on the LAN
```

### Internal Constants (editable in source)

| Constant | Description | Default |
|----------|-------------|---------|
| `SCAN_SEC` | USB device scan interval (seconds) | `6` |
| `TUNNEL_TIMEOUT` | Single tunnel establishment timeout (seconds) | `40` |
| `TUNNEL_RETRIES` | Number of tunnel retry attempts on failure | `3` |
| `TUNNEL_RETRY_SEC` | Wait between retry attempts (seconds) | `5` |
| `DEVICE_BOOT_WAIT` | Seconds to wait after device detection before tunneling | `8` |
| `MISS_THRESHOLD` | Consecutive missed scans before a device is treated as removed (debounces transient scan failures) | `2` |

### Logging

Every run writes to `gps_launcher.log` (same name as the script, `.log` extension), alongside live console output. The log file is **not** meant to be committed — it can contain device names and UDIDs.

### Internal Architecture

```
Startup
 └─ device_scanner  (every 6 seconds, USB-only)
      ├─ New device   → setup_device (wait 8s → start tunnel → gps_worker)
      └─ Device gone  → teardown_device (cancel tasks → kill tunnel group → clear state)
                        (only after MISS_THRESHOLD consecutive misses)

gps_worker  (one persistent coroutine per device)
 └─ Connect RSD → DvtProvider → LocationSimulation
      ├─ SetCmd(lat, lon) → loc.set(lat, lon)
      ├─ ClearCmd         → loc.clear()
      └─ Tunnel died?     → rebuild it automatically (self-healing)

Shutdown (Ctrl-C) → _cleanup_all → tear down every device tunnel
```

The launcher uses **three fallback methods** for USB device scanning:
1. `pymobiledevice3` Python API (preferred)
2. CLI subprocess (`python -m pymobiledevice3 usbmux list`)
3. Regex UDID extraction from CLI output (last resort)

Only **USB-connected** devices are used; Wi-Fi-paired devices are ignored so the launcher never tries to tunnel a phone that isn't physically plugged in.

### Reliability (unplug / re-plug)

Earlier versions could get stuck after unplugging and re-plugging a phone, requiring a launcher restart. This is fixed:

- The per-device `gps_worker` and `setup_device` tasks are tracked and **cancelled** on removal.
- The tunnel subprocess is spawned in its own process group and torn down with `killpg`, so no orphan tunnel processes linger.
- The cached RSD endpoint is cleared on teardown, and a worker that detects a dead tunnel **rebuilds it** automatically.
- On shutdown, all tunnels are cleaned up so the next run starts clean.

### Stopping the Service

Press `Ctrl + C` for a graceful shutdown (all tunnels are released).

---

## Frontend — gps_map.html Usage

The frontend polls the backend API (default `http://localhost:8090`) every **1.5 seconds** to update device status.

---

### 📍 Normal Mode

**Click to Set Position** — click the map → coordinates are pushed to iPhone instantly; the HUD shows live cursor coordinates.

**Manual Coordinate Input** — enter `Lat, Lon` in a single field (spaces are ignored) and click **Set**.

**Direction Pad** — 8-direction buttons with a configurable step distance (meters); keyboard control also available (see [Keyboard Shortcuts](#keyboard-shortcuts)).

**Copy Coordinates** — copies `lat, lon` to the clipboard (comma + space, 6 decimals).

**Clear GPS Simulation** — removes the simulated location; iPhone reverts to real GPS.

**Device Status Card** — Connected / Simulating / Set Count / Uptime.

**Timezone Info** — local time (Open-Meteo) and a cross-day ⚠ indicator.

---

### 🔗 Sync All Devices

When two or more iPhones are connected, a **🔗 Sync All Devices** card appears at the top of the device list. Select it to broadcast every action — set position, clear, and cruise steps — to **all connected devices at once**. The card shows how many devices are connected (e.g. `2 / 2`).

Select an individual device card instead to control just that one.

---

### 🗺 Cruise Mode

**Adding Waypoints** — click the map to add numbered waypoints; drag to move, double-click to delete. **⎌ Undo** removes the last, **Clear All** resets.

**Batch Paste Coordinates** — paste many coordinates at once (one per line, or many comma-pairs separated by spaces). Supported formats: `25.0330, 121.5654`, `25.0330 121.5654`, `25.0330,121.5654`, negative values, and lines with leading labels. Spaces are normalized automatically. Click **Apply** to replace the current route (you'll be asked to confirm if waypoints already exist).

**Speed & Loop** — speed in km/h; loop restarts from the first waypoint after the end.

**Route Info** — waypoints, distance (auto m/km), and ETA.

**Playing a Cruise**
1. At least 2 waypoints are required.
2. Click ▶ **Play** → one interpolated GPS step is pushed per second.
3. Click ⏸ **Pause** → progress and position are **remembered**. You can move the GPS elsewhere meanwhile; pressing ▶ **Resume** continues from exactly where you paused.
4. Click ■ **Stop** → ends the run (the last position stays; GPS is not cleared).

> Moving the GPS manually (map click, input, D-pad, favorite) **while a cruise is playing** auto-pauses the cruise so your manual position isn't overwritten.

**Save / Load Routes** — **💾 Save Route** stores the current waypoints + speed + loop under a name (in `localStorage`); **📂 Load Route** lists saved routes to load or delete.

**Export GPX** — downloads `cruise_route.gpx` (standard GPX 1.1).

---

### ⭐ Favorites

Stored in the browser's `localStorage` (key: `gps_favorites_v1`); persists across refreshes and restarts.

**How to Add**
- **⭐ Add Current Position** — saves the current coordinates (you enter a name).
- **✚ Add Manually** — type a name + coordinates in a dialog.
- From a search-result Popup, **⭐ Add to Favorites**.

The country name (in Traditional Chinese) is filled in automatically via reverse geocoding.

**Each item shows** the name + country, plus a **live local time** and cross-day tag.

**Item buttons:** 🗺 pan map · 📍 set as GPS · ✎ edit name & coordinates · 🗑 delete.

**Reordering** — the ↑ / ↓ buttons sit in a **pinned toolbar** that stays visible while you scroll. Click an item to select it, then use ↑ / ↓ to move it; the selection stays put so you can move it repeatedly without re-clicking.

**Map click in Favorites mode** — switches to Normal mode and sets that position.

---

### 🔍 Place Search

1. Type a place name (Chinese or English) and press Enter / the search button.
2. Up to 7 results are shown, each with country and local time.
3. Click a result → map jumps there and a Popup offers: **📍 Set as GPS**, **✚ Add waypoint** (Cruise mode only), and **⭐ Add to Favorites**.

---

### Device Management

- USB is scanned every 6 seconds; new devices appear automatically.
- Disconnected devices are removed (after a short debounce); the frontend auto-switches to the next connected device.
- Status dot colors: 🟢 GPS connected · 🟡 tunnel OK, GPS connecting · 🔴 tunnel establishing / failed.

---

## HTTP API Reference

The backend listens on `http://{HOST}:{PORT}` (default `127.0.0.1:8090`) with CORS fully open.

### Per-device

| Method & Path | Description |
|---------------|-------------|
| `GET /devices` | List all detected devices (array of status objects) |
| `POST /device/{idx}/set` | Set coordinates — body `{ "lat": .., "lon": .. }` |
| `POST /device/{idx}/clear` | Clear simulation, restore real GPS |
| `GET /device/{idx}/status` | Detailed status of one device |

### Broadcast (all connected devices)

| Method & Path | Description |
|---------------|-------------|
| `POST /devices/set` | Set the same coordinates on every connected device |
| `POST /devices/clear` | Clear simulation on every connected device |

**Example — `GET /devices` response:**
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

A broadcast response includes the affected device count, e.g. `{ "ok": true, "count": 2, "lat": .., "lon": .. }`.

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
- iOS 17+ requires **Developer Mode**: Settings → Privacy & Security → Developer Mode → Enable
- Ensure Xcode Command Line Tools are up to date: `xcode-select --install`
- Try restarting the iPhone before reconnecting

**Q3: GPS set but iPhone location doesn't change?**
- Confirm the device card shows a **green dot (Connected)** and **Simulating: Yes**
- Some apps need to be relaunched to pick up the new location

**Q4: Frontend shows "Launcher not running"?**
- Confirm `gps_launcher.py` is running and shows `🚀 GPS Launcher`
- If you changed the port, update `const META = 'http://localhost:PORT';` in `gps_map.html`

**Q5: GPS disappears after stopping a cruise?**
- By design: stopping a cruise **keeps the last position**. Use **Clear GPS** in Normal mode to restore real GPS.

**Q6: Phone won't reconnect after unplug/re-plug (older behavior)?**
- This is fixed in the current version — re-plugging reconnects automatically without restarting the launcher.

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
