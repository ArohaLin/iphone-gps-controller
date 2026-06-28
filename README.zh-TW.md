# 📍 iPhone GPS Controller

從 Mac 透過 USB 即時模擬 iPhone GPS 定位的工具。  
`gps_launcher.py` 負責偵測裝置並提供 HTTP API；  
`gps_map.html` 提供互動式 Leaflet 地圖介面來控制 GPS。

> **作者：** Aroha Lin · **授權：** MIT · **Copyright (c) 2026 Aroha Lin**  
> **專案：** https://github.com/ArohaLin/iphone-gps-controller

🌏 **English version: [README.md](README.md)**

---

## 本次更新重點

這一版相較最初版本是大幅升級：

- **多裝置同步** — 同時操控多支 iPhone。新增 🔗 *同步全部裝置* 卡片，一個動作（設定 / 清除 / 巡航）即可同步廣播到所有已連線手機。
- **巡航強化** — 暫停 / 繼續（即使中途把 GPS 移到別處，進度仍保留）、儲存 / 讀取命名路線、一次批次貼上多個座標。
- **最愛全面翻新** — 手動加入、自動帶出國家名稱（繁體中文）、即時當地時間、置頂工具列免滑動排序、可修改名稱與座標。
- **後端大幅強化** — 拔插手機後可自動重連（不必重啟 Launcher）、只認 USB 裝置、tunnel 以行程群組完整清除、斷線自我修復、檔案日誌。

---

## 目錄

1. [功能特色](#功能特色)
2. [系統需求](#系統需求)
3. [安裝](#安裝)
4. [快速開始](#快速開始)
5. [後端 — gps_launcher.py](#後端--gps_launcherpy)
6. [前端 — gps_map.html 使用說明](#前端--gps_maphtml-使用說明)
7. [HTTP API 參考](#http-api-參考)
8. [鍵盤快速鍵](#鍵盤快速鍵)
9. [疑難排解](#疑難排解)
10. [第三方授權](#第三方授權)

---

## 功能特色

| 功能 | 說明 |
|------|------|
| 📍 點擊設定 GPS | 點地圖或輸入座標，立即推送到 iPhone |
| 🧭 方向移動 | 八方向鍵，可設定每步距離（公尺） |
| 🗺 巡航模式 | 多航點路線規劃，每秒前進、循環、**暫停/繼續**、**儲存/讀取**、**批次貼上** |
| 📤 匯出 GPX | 將航點路線匯出成標準 `.gpx` 檔 |
| 🔍 地點搜尋 | 透過 Nominatim（OpenStreetMap）搜尋全球地點 |
| ⭐ 我的最愛 | 儲存地點並顯示國家名稱與即時當地時間，可排序與修改 |
| 🕐 當地時間 | 顯示目標座標的當地時間與跨日提示（Open-Meteo API） |
| 🔗 多裝置同步 | 一個動作同步廣播到所有已連線 iPhone |
| 📱 多裝置管理 | 同時連接多支 iPhone，無縫切換 |
| 🔄 自動重連 | tunnel/GPS 斷線自我修復；拔插處理乾淨 |
| 📝 檔案日誌 | 每次執行記錄到腳本旁的 `gps_launcher.log` |

---

## 系統需求

| 項目 | 需求 |
|------|------|
| **作業系統** | macOS（建立 tunnel 需 `sudo`） |
| **Python** | 3.8 以上 |
| **iPhone iOS** | iOS 16 以上（iOS 17+ 需 RSD tunnel + 開發者模式） |
| **連線** | USB（Lightning 或 USB-C） |
| **瀏覽器** | Chrome / Firefox / Safari（需支援 Clipboard API） |
| **網路** | 後端預設僅限本機；搜尋 / 時區需連網 |

---

## 安裝

### 1. 安裝 Python 套件

```bash
pip install aiohttp pymobiledevice3
```

> 使用虛擬環境：
> ```bash
> python3 -m venv venv
> source venv/bin/activate
> pip install aiohttp pymobiledevice3
> ```

### 2. 信任裝置

1. 用 USB 線連接 iPhone 與 Mac
2. iPhone 跳出「信任這部電腦？」時點 **信任**
3. 確認 Mac 的 `usbmuxd` 正在執行（通常會自動啟動）

### 3. 檔案結構

```
iphone-gps-controller/
├── gps_launcher.py   ← 後端 Python 服務
└── gps_map.html      ← 前端地圖介面
```

---

## 快速開始

### 步驟 1：啟動後端

```bash
sudo python3 gps_launcher.py
```

> ⚠️ **建立 tunnel 需要 `sudo`** — macOS 會要求輸入密碼。

啟動成功時，終端機會顯示：

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

### 步驟 2：開啟前端地圖

直接用瀏覽器開啟 `gps_map.html`：

```bash
open gps_map.html
# 或拖曳到任意瀏覽器視窗
```

### 步驟 3：設定 GPS 位置

1. 確認裝置清單中 iPhone 顯示 **綠點（已連線）**
2. 點地圖任意位置 → iPhone GPS 立即更新
3. 右上角會跳出提示確認成功

---

## 後端 — gps_launcher.py

### 命令列參數

```bash
sudo python3 gps_launcher.py [PORT] [HOST]
```

兩個參數都是**選填**的 — 直接執行 `sudo python3 gps_launcher.py`、後面什麼都不加，就會套用下表的預設值。只有想覆寫時才需要填。

| 參數 | 說明 | 預設 |
|------|------|------|
| `PORT` | HTTP API 監聽埠 | `8090` |
| `HOST` | 綁定位址。設 `0.0.0.0` 可把 API 開放到區網；預設僅限本機 | `127.0.0.1` |

範例：

```bash
sudo python3 gps_launcher.py                   # 全部用預設（8090、僅限本機）
sudo python3 gps_launcher.py 9000              # 自訂埠
sudo python3 gps_launcher.py 8090 0.0.0.0      # 開放 API 到區網
```

### 內部常數（可於原始碼調整）

| 常數 | 說明 | 預設 |
|------|------|------|
| `SCAN_SEC` | USB 掃描間隔（秒） | `6` |
| `TUNNEL_TIMEOUT` | 單次 tunnel 建立逾時（秒） | `40` |
| `TUNNEL_RETRIES` | tunnel 失敗重試次數 | `3` |
| `TUNNEL_RETRY_SEC` | 重試間隔（秒） | `5` |
| `DEVICE_BOOT_WAIT` | 偵測到裝置後、建立 tunnel 前的等待（秒） | `8` |
| `MISS_THRESHOLD` | 連續幾次掃描未見才視為移除（避免暫時掃描失敗誤判） | `2` |

### 日誌

每次執行會寫入 `gps_launcher.log`（與腳本同名、副檔名 `.log`），同時輸出到主控台。日誌**不應上傳** — 內含裝置名稱與 UDID。

### 內部架構

```
啟動
 └─ device_scanner（每 6 秒，只認 USB）
      ├─ 新裝置   → setup_device（等 8 秒 → 建 tunnel → gps_worker）
      └─ 裝置消失 → teardown_device（取消任務 → 殺 tunnel 群組 → 清除狀態）
                    （連續達 MISS_THRESHOLD 次才執行）

gps_worker（每台裝置一個常駐協程）
 └─ 連線 RSD → DvtProvider → LocationSimulation
      ├─ SetCmd(lat, lon) → loc.set(lat, lon)
      ├─ ClearCmd         → loc.clear()
      └─ tunnel 死掉？    → 自動重建（自我修復）

關閉（Ctrl-C）→ _cleanup_all → 拆除所有裝置 tunnel
```

USB 掃描採用**三層備援**：
1. `pymobiledevice3` Python API（首選）
2. CLI 子行程（`python -m pymobiledevice3 usbmux list`）
3. 從 CLI 輸出以 regex 擷取 UDID（最後手段）

只使用 **USB 連線**的裝置；Wi-Fi 配對的裝置會被忽略，避免去 tunnel 一支沒有實體插上的手機。

### 穩定性（拔插處理）

舊版在拔掉手機再重插後常常卡住、必須重啟 Launcher，此問題已修正：

- 每台裝置的 `gps_worker` 與 `setup_device` 任務會被追蹤並在移除時**取消**。
- tunnel 子行程以獨立行程群組啟動，並用 `killpg` 整組終止，不再殘留孤兒 tunnel。
- 拆除時清空快取的 RSD 位址；worker 偵測到 tunnel 已死會**自動重建**。
- 關閉時清除所有 tunnel，確保下次啟動乾淨。

### 停止服務

按 `Ctrl + C` 即可優雅關閉（會釋放所有 tunnel）。

---

## 前端 — gps_map.html 使用說明

前端每 **1.5 秒**輪詢後端 API（預設 `http://localhost:8090`）更新裝置狀態。

---

### 📍 一般模式

**點擊設定位置** — 點地圖 → 座標立即推送到 iPhone；HUD 顯示即時游標座標。

**手動輸入座標** — 在單一欄位輸入 `Lat, Lon`（空格會自動過濾）後按 **設定**。

**方向鍵** — 八方向按鈕，可設定每步距離（公尺）；也支援鍵盤操作（見 [鍵盤快速鍵](#鍵盤快速鍵)）。

**複製座標** — 複製 `lat, lon` 到剪貼簿（逗號後加空格，6 位小數）。

**停止 GPS 模擬** — 移除模擬位置，iPhone 恢復真實 GPS。

**裝置狀態卡** — 已連線 / 模擬中 / 送出次數 / 運行時間。

**時區資訊** — 當地時間（Open-Meteo）與跨日 ⚠ 提示。

---

### 🔗 同步全部裝置

當連接兩支以上 iPhone 時，裝置清單頂端會出現 **🔗 同步全部裝置** 卡片。選取它後，所有動作（設定位置、清除、巡航步進）都會**同步廣播到所有已連線裝置**。卡片會顯示連線數（例如 `2 / 2`）。

若只想控制單一裝置，改點該裝置卡片即可。

---

### 🗺 巡航模式

**新增航點** — 點地圖依序新增編號航點；拖曳可移動、雙擊可刪除。**⎌ 復原** 移除最後一個、**清除全部** 重設。

**批次貼上座標** — 一次貼上多個座標（每行一個，或同行多組逗號座標以空格分隔）。支援格式：`25.0330, 121.5654`、`25.0330 121.5654`、`25.0330,121.5654`、負值、以及行首含文字標籤。空格會自動正規化。按 **套用** 取代目前路線（若已有航點會先確認）。

**速度與循環** — 速度以 km/h 設定；循環會在抵達終點後從第一個航點重新開始。

**路線資訊** — 航點數、距離（自動 m/km）、預計時間。

**播放巡航**
1. 至少需要 2 個航點。
2. 按 ▶ **播放** → 每秒推送一個插值後的 GPS 步進。
3. 按 ⏸ **暫停** → 進度與位置會**被記住**。期間可把 GPS 移到別處；按 ▶ **繼續** 會從暫停處接續。
4. 按 ■ **停止** → 結束（停在最後位置，不清除 GPS）。

> 在**巡航播放中**手動移動 GPS（點地圖、輸入、方向鍵、最愛）會自動暫停巡航，避免你的手動位置被下一步覆蓋。

**儲存 / 讀取路線** — **💾 儲存路線** 把目前航點 + 速度 + 循環設定以名稱存到 `localStorage`；**📂 讀取路線** 列出已存路線可讀取或刪除。

**匯出 GPX** — 下載 `cruise_route.gpx`（標準 GPX 1.1）。

---

### ⭐ 我的最愛

儲存在瀏覽器 `localStorage`（key：`gps_favorites_v1`）；重新整理或重開瀏覽器都會保留。

**新增方式**
- **⭐ 加入目前位置** — 儲存目前座標（自行輸入名稱）。
- **✚ 手動加入** — 在對話框輸入名稱 + 座標。
- 從搜尋結果 Popup 按 **⭐ 加入我的最愛**。

國家名稱（繁體中文）會透過反向地理編碼自動帶入。

**每個項目會顯示** 名稱 + 國家，以及**即時當地時間**與跨日標籤。

**項目按鈕：** 🗺 移動地圖 · 📍 設定為 GPS · ✎ 修改名稱與座標 · 🗑 刪除。

**排序** — ↑ / ↓ 按鈕位於**置頂工具列**，捲動時固定可見。點項目選取它，再用 ↑ / ↓ 移動；選取狀態會保留，方便連續移動而不必重新點選。

**最愛模式點地圖** — 會切換到一般模式並設定該位置。

---

### 🔍 地點搜尋

1. 輸入地名（中英文皆可）按 Enter 或搜尋鈕。
2. 最多顯示 7 筆結果，各含國家與當地時間。
3. 點結果 → 地圖跳轉並出現 Popup，提供：**📍 設定為 GPS**、**✚ 加入航點**（僅巡航模式）、**⭐ 加入我的最愛**。

---

### 裝置管理

- 每 6 秒掃描 USB，新裝置自動出現。
- 斷線裝置會移除（有短暫防抖）；前端自動切換到下一台已連線裝置。
- 狀態燈號：🟢 GPS 已連線 · 🟡 tunnel 完成、GPS 連線中 · 🔴 tunnel 建立中 / 失敗。

---

## HTTP API 參考

後端監聽 `http://{HOST}:{PORT}`（預設 `127.0.0.1:8090`），CORS 全開。

### 單一裝置

| 方法與路徑 | 說明 |
|-----------|------|
| `GET /devices` | 列出所有偵測到的裝置（狀態物件陣列） |
| `POST /device/{idx}/set` | 設定座標 — body `{ "lat": .., "lon": .. }` |
| `POST /device/{idx}/clear` | 清除模擬、恢復真實 GPS |
| `GET /device/{idx}/status` | 單一裝置詳細狀態 |

### 廣播（所有已連線裝置）

| 方法與路徑 | 說明 |
|-----------|------|
| `POST /devices/set` | 對每台已連線裝置設定相同座標 |
| `POST /devices/clear` | 對每台已連線裝置清除模擬 |

**範例 — `GET /devices` 回應：**
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

廣播回應會包含影響的裝置數，例如 `{ "ok": true, "count": 2, "lat": .., "lon": .. }`。

---

## 鍵盤快速鍵

> 當輸入框取得焦點時，快速鍵會停用。

| 按鍵 | 動作 |
|------|------|
| `↑` / `W` | 向北移動 |
| `↓` / `S` | 向南移動 |
| `←` / `A` | 向西移動 |
| `→` / `D` | 向東移動 |
| `Q` | 向西北 |
| `E` | 向東北 |
| `Z` | 向西南 |
| `C` | 向東南 |
| `+` / `=` | 放大 |
| `-` | 縮小 |
| `F` | 將地圖重新置中到目前座標 |

---

## 疑難排解

**Q1：啟動後偵測不到裝置？**
- 確認 iPhone 已解鎖並點過 **信任這部電腦**
- 試著拔插 USB 線
- 執行 `python3 -m pymobiledevice3 usbmux list` 驗證是否偵測得到

**Q2：tunnel 一直失敗（`❌ Tunnel failed after 3 attempts`）？**
- iOS 17+ 需開啟 **開發者模式**：設定 → 隱私權與安全性 → 開發者模式 → 開啟
- 確認 Xcode 命令列工具為最新：`xcode-select --install`
- 重新連接前先重開 iPhone

**Q3：設定了 GPS 但 iPhone 位置沒變？**
- 確認裝置卡顯示 **綠點（已連線）** 且 **模擬中：是**
- 部分 App 需重新開啟才會吃到新位置

**Q4：前端顯示「Launcher 未啟動」？**
- 確認 `gps_launcher.py` 正在執行且顯示 `🚀 GPS Launcher`
- 若改了埠號，請同步修改 `gps_map.html` 內的 `const META = 'http://localhost:PORT';`

**Q5：停止巡航後 GPS 消失？**
- 此為設計行為：停止巡航會**保留最後位置**。要恢復真實 GPS，請用一般模式的 **停止 GPS 模擬**。

**Q6：拔插手機後無法重連（舊版行為）？**
- 目前版本已修正 — 重插後會自動重連，不必重啟 Launcher。

---

## 第三方授權

| 套件 | 授權 |
|------|------|
| [Leaflet.js](https://leafletjs.com/) | BSD 2-Clause |
| [OpenStreetMap / Nominatim](https://www.openstreetmap.org/) | ODbL |
| [Open-Meteo API](https://open-meteo.com/) | CC BY 4.0 |
| [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) | GPL-3.0 |
| [aiohttp](https://docs.aiohttp.org/) | Apache 2.0 |

---

*Copyright (c) 2026 Aroha Lin — MIT License*
