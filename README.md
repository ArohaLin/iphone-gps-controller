# 📍 iPhone GPS Controller

A tool for real-time iPhone GPS location simulation from a Mac, over USB or a paired Wi-Fi connection.  
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
- **Much more robust backend** — unplug / re-plug now reconnects automatically (no launcher restart needed), selectable USB / Wi-Fi / Auto connection mode, full process-group tunnel cleanup, self-healing reconnect, and file logging.
- **Persistent data** — favorites, cruise routes, and base map settings are now saved to `user_data.json` next to the launcher, so they survive page refreshes, closing the browser, and reboots. Data that older versions kept in the browser is migrated automatically the first time you connect to the new version.
- **Auto-rejoin for disconnected phones** — if one phone drops out during a multi-device cruise, it automatically rejoins the cruise when it reconnects and jumps straight to the current cruise position, with no need to stop and restart; in a "🔗 Sync All Devices" cruise, phones that connect after the cruise has started join automatically too (no extra background polling).
- **Quick cruise route switching** — the cruise panel has a new "⭐ My cruise routes" dropdown; saved routes can be renamed, overwritten with the current waypoints, or deleted.
- **Optional Google Maps** — switch the base map between OpenStreetMap / Google Maps / Google Satellite (requires your own Google Maps API key). Google is only contacted when you pick a Google base map.
- **⚡ Jump cruise** — a new "Jump" cruise style: teleports to each waypoint, circles around it for a while (defaults: 15 s, 30 m radius, circle speed up to 19 km/h), then jumps to the next one; settings can be changed at any time and take effect immediately, and the original constant-speed cruise is unaffected.

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
| **Connection** | USB (Lightning or USB-C), or Wi-Fi with a paired iPhone on the same local network as the Mac |
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

**First-time Wi-Fi setup:** first complete the trust and pairing steps above over USB, and enable Developer Mode on the iPhone under Settings → Privacy & Security → Developer Mode (restart and confirm when prompted). Select the phone in Finder, check "Show this iPhone when on Wi-Fi" on the General tab, and click Apply. Put the Mac and iPhone on the same local network, then choose Wi-Fi or Auto mode on the map. Once the device card shows Wi-Fi and Connected, confirm it still works after unplugging the cable.

The launcher starts in **USB** mode; **Wi-Fi** uses the wireless connection, and **Auto** lets the backend pick whichever connection is available. Each device card shows the connection type that phone is actually using. Switching modes aborts incompatible connections and cruises; reconnecting does not automatically replay the previous position or cruise, so start them again manually. If you get a message that connection modes aren't supported, restart with the new version of the launcher.

If you want to test a tunnel manually, stop the launcher's automatic system first; when you're done, close the manual tunnel before starting the launcher again — never run both at the same time. Connection stability during sleep and while the screen is locked has not yet been tested on real devices, and connection is not guaranteed once the phone disappears from Finder.

### 3. File Structure

```
iphone-gps-controller/
├── gps_launcher.py   ← Backend Python service
├── gps_map.html      ← Frontend map interface
├── user_data.json    ← Your favorites / cruise routes / base map settings (created automatically on first run; listed in .gitignore)
└── user_data.json.prev ← Automatic backup of the previous version (one generation kept before each save)
```

> `user_data.json` contains your private locations and Google key, so it is excluded from git — do not upload it manually. When the launcher runs under `sudo`, the file's ownership is automatically changed back to your own user account with permissions 600 (only you can read and write it).

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

**Recommended:** once the launcher is running, open **http://localhost:8090/** in your browser (the launcher serves the map page directly).

```bash
open http://localhost:8090/
```

You can also open the file directly as before (`open gps_map.html` or drag-and-drop it onto a browser window) — it works the same. But opening it via the URL is more reliable, and it lets you restrict your Google key to "only http://localhost:8090/*", which is safer.

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
| `SCAN_SEC` | Device scan interval (seconds) | `6` |
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
 └─ device_scanner  (every 6 seconds, per connection mode)
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

The default **USB** mode only uses USB devices; switch to **Wi-Fi** or **Auto** to include paired wireless devices. Wi-Fi is the connection between the Mac and the phone — you do not need to expose the control API to your local network; the frontend still connects to `http://localhost:8090`.

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

When two or more iPhones are connected, a **🔗 Sync All Devices** card appears at the top of the device list. Select it to broadcast every action — set position, clear, and cruise steps — only to **devices that are connected and have control enabled**. The card shows the connected count and the controllable count separately.

All devices share a single cruise route; playing a different route on each phone is not currently supported. Each device card is labeled USB or Wi-Fi.

Select an individual device card instead to control just that one.

Each phone card has an **Enable control** toggle that can be operated from the keyboard and only updates once the setting is saved successfully. The setting is stored per phone UDID in `device_preferences.json` and is kept across reconnects. Disabling does not remove pairing and keeps the phone's connection and toggle, but stops GPS / cruise / step control for that phone; re-enabling does not automatically replay the previous position, but during a "Sync All Devices" cruise a re-enabled phone immediately follows the current cruise position. If saving fails, the toggle stays as it was; if control is disabled but clearing the simulation on the phone fails, a warning is shown — do not assume the real location has been restored. If an older launcher is missing the toggle data, restart with the new version.

Shortcuts already handed off to the phone may not be revocable, and disabling control does not guarantee that every location app on iOS updates its position immediately.

---

### 🗺 Cruise Mode

**Adding Waypoints** — click the map to add numbered waypoints; drag to move, double-click to delete. **⎌ Undo** removes the last, **Clear All** resets.

**Batch Paste Coordinates** — paste many coordinates at once (one per line, or many comma-pairs separated by spaces). Supported formats: `25.0330, 121.5654`, `25.0330 121.5654`, `25.0330,121.5654`, negative values, and lines with leading labels. Spaces are normalized automatically. Click **Apply** to replace the current route (you'll be asked to confirm if waypoints already exist).

**Speed & Loop** — speed in km/h; loop restarts from the first waypoint after the end.

**Cruise style: 🚶 Constant speed / ⚡ Jump** — switch between two styles at the top of the cruise panel:
- **🚶 Constant speed** (default): moves along the waypoints at the set speed, exactly as before.
- **⚡ Jump**: teleports to each waypoint, circles around it for a while, then teleports to the next one. Three settings: **Stay** (seconds, default 15, 1–3600), **Circle radius** (meters, default 30, 0–500; 0 = stay still on the waypoint), and **Circle speed** (km/h, default 19, **max 19** — larger values are changed to 19 automatically).
  - On arrival the phone lands directly on the circle (on the side facing its previous position) and circles clockwise at constant speed; pausing freezes both the countdown and the circling.
  - With loop on, it jumps back to the first waypoint after the last one; with loop off, the cruise ends after the last waypoint's stay.
  - The map draws a dashed circle around every waypoint; in Route Info the distance shows "—" and the ETA = number of waypoints × stay seconds; progress shows "Point x/n · y s left".
- **Change anything at any time, effective immediately**: during a cruise you can change the stay time (if it becomes shorter than the time already spent, it jumps to the next point right away), the radius, the circle speed, or switch styles (switching to Jump jumps to the next point and starts staying there; switching back to Constant speed walks from the current position to the next point; if it is on the last waypoint with loop on, it restarts from the first waypoint) — no need to stop and restart. While typing in a field, the change is sent about 0.8 s after you stop typing (or right away when you press Enter or leave the field), so a half-typed number never takes effect early. The last style and settings you used are remembered in `user_data.json` and kept after reopening the page.
- Jump mode needs the new launcher; an older launcher shows a prompt to restart it instead of silently falling back to constant speed.

**Route Info** — waypoints, distance (auto m/km), and ETA.

**Playing a Cruise**
1. At least 2 waypoints are required.
2. Click ▶ **Play** → one interpolated GPS step is pushed per second.
3. Click ⏸ **Pause** → progress and position are **remembered**. You can move the GPS elsewhere meanwhile; pressing ▶ **Resume** continues from exactly where you paused.
4. Click ■ **Stop** → ends the run (the last position stays; GPS is not cleared).

> Moving the GPS manually (map click, input, D-pad, favorite) **while a cruise is playing** auto-pauses the cruise so your manual position isn't overwritten.

**Auto-rejoin for disconnected phones** — during a multi-device cruise, if one phone disconnects (cable pulled, Wi-Fi dropped, a transient error), it automatically rejoins this cruise once it reconnects and immediately receives the *current* cruise position (if the cruise is paused, it jumps to the paused position); old commands are not replayed. In a "🔗 Sync All Devices" cruise, phones that connect after the cruise has started (with control enabled) join automatically as well; in a single-device cruise, other phones that connect are not pulled in. A phone with **control disabled** does not follow, and turning the toggle back on during a Sync All Devices cruise makes it follow immediately; a phone you **cleared** is not pulled back in for the rest of that cruise (starting a new cruise resets this). If all phones disconnect at once (or the only phone in a single-device cruise disconnects), the cruise stops automatically and you need to press ▶ Play again after reconnecting.

**Save / Load Routes** — **💾 Save Route** saves the current waypoints + speed + loop + cruise style (including Jump's stay / radius / circle speed) under a name; older saved routes always load as Constant speed (stored in `user_data.json`); if the name already exists, you're asked whether to overwrite that route or save it as a new one. **📂 Load Route** lists saved routes, each with **Load**, **✏️ Rename**, **⬆️ Overwrite with current waypoints**, and **🗑 Delete**.

**⭐ My cruise routes (quick switch)** — a dropdown in the cruise panel lists all saved routes; pick one to apply it instantly, and the last applied route is remembered. Switching while a cruise is running asks first; after you confirm, the current cruise stops and the new route is applied.

**Export GPX** — downloads `cruise_route.gpx` (standard GPX 1.1).

---

### ⭐ Favorites

Stored in `user_data.json` next to the launcher; persists across refreshes, closing the browser, and reboots. The browser only keeps a cache: if the launcher isn't running or is an older version, additions / edits are held in the browser for now and synced automatically once the launcher connects (you'll see a one-time notice).

**Export / Import Backup** — **💾 Export Backup** downloads `gps_backup.json` (favorites, cruise routes, and base map choice; it does **not** include your Google key). When moving to a new computer, press **📥 Import Backup** there. Backup files exported by older versions can also be imported.

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

### 🗺 Base Map (OpenStreetMap / Google)

The menu in the top-right corner of the map switches between **OpenStreetMap** (default, free), **Google Maps**, and **Google Satellite**; your choice is remembered and applied the next time you open the page. Search and country-name reverse lookups still use Nominatim (OpenStreetMap), so they never incur Google charges.

**Setting your Google key** — click the ⚙️ next to the menu, paste your Google Maps API key, and save (it is stored in `user_data.json` and is not included in exported backups). If you pick Google without a key, the settings dialog opens and the map stays on OpenStreetMap; if the key is invalid or Google fails to load, the map automatically falls back to OpenStreetMap. After changing to a new key, refresh the page.

**Cost and overspend protection** (based on Google's official pricing as of November 2026)
- The Google Maps JavaScript API is only loaded when you pick a Google base map. Billing is per page open = 1 map load; dragging, zooming, and cruise movement are not charged separately.
- The first 10,000 map loads each month are free; beyond that it's roughly US$7 per 1,000. Personal use usually stays within the free tier.
- Your Google Cloud project needs billing enabled (a card on file) and the "Maps JavaScript API" enabled.
- We recommend going to Google Cloud Console → **APIs & Services → Maps JavaScript API → Quotas** and setting the daily map load limit to 300: that caps a month at about 9,300 loads, guaranteeing you stay within the free tier. On days you hit the limit, the Google base map temporarily won't display — you won't be charged.
- We recommend restricting the key under "API restrictions" to Maps JavaScript API only; if you open the page via http://localhost:8090/, you can also add a "Website restriction" of `http://localhost:8090/*`.

---

### Device Management

- Devices are scanned every 6 seconds according to the USB / Wi-Fi / Auto mode; new devices appear automatically.
- Disconnected devices are removed (after a short debounce); the frontend auto-switches to the next connected device.
- Status dot colors: 🟢 GPS connected · 🟡 tunnel OK, GPS connecting · 🔴 tunnel establishing / failed.

---

## HTTP API Reference

The backend listens on `http://{HOST}:{PORT}` (default `127.0.0.1:8090`). CORS is fully open for the device-control APIs; the exception is `/userdata`, which only accepts the local map page (`file://` or `http://localhost:PORT`, `http://127.0.0.1:PORT`) and rejects all other sites.

### Map Page & User Data

| Method & Path | Description |
|---------------|-------------|
| `GET /`, `GET /map` | Serves `gps_map.html` (no other files are served) |
| `GET /userdata` | Read `{ ok, exists, data: { version, updatedAt, favorites, cruiseRoutes, mapSettings } }` |
| `POST /userdata` | Partial update; body may contain `favorites`, `cruiseRoutes`, `mapSettings` (requires `Content-Type: application/json`, 5MB max); returns `{ ok, updatedAt }` |

Saves are written atomically, and the previous version is kept in `user_data.json.prev`. If the file is corrupted or can't be read due to permissions, the launcher refuses to overwrite it and returns `{ ok: false, error }`.

### Per-device

| Method & Path | Description |
|---------------|-------------|
| `GET /devices` | List all detected devices (array of status objects) |
| `GET /connection-mode` | Get the mode; returns `{ "ok": true, "mode": "usb" }` (mode can be `usb` / `wifi` / `auto`) |
| `POST /connection-mode` | Set the mode — body `{ "mode": "wifi" }`; returns the same structure on success and aborts incompatible connections and cruises |
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
    "connection_type": "usb",
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

**Q7: My favorites or cruise routes are gone?**
- In the new version, data is stored in `user_data.json`, so it no longer disappears when the browser's data is cleared.
- If you see a "Launcher not connected (or older version)" notice, your data is being held in the browser for now; it syncs automatically once you start the new launcher.
- If you deleted something by mistake, you can recover it from `user_data.json.prev` (the previous version): stop the launcher, copy `.prev` over `user_data.json`, then start the launcher again.

**Q8: I picked Google Maps but it keeps falling back to OpenStreetMap?**
- Check that the key is correct and that **Maps JavaScript API** and billing are enabled in Google Cloud.
- If the key has a "Website restriction", open the page via http://localhost:8090/ instead (opening the file directly has no website address, so the restriction blocks it).
- Check whether you've hit the daily quota limit you set (it resets at midnight Pacific Time the next day).

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
