[English](README.md) | [繁體中文](README.zh-TW.md)

# 📍 iPhone GPS Controller

透過 Mac（USB 連線）即時模擬 / 修改 iPhone GPS 位置的工具。  
後端由 `gps_launcher.py` 管理裝置連線與 HTTP API；  
前端為 `gps_map.html`，以 Leaflet 地圖介面操控位置。

> **Author:** Aroha Lin · **License:** MIT · **Copyright (c) 2026 Aroha Lin**  
> **Repo:** https://github.com/ArohaLin/iphone-gps-controller

---

## 目錄

1. [功能一覽](#功能一覽)
2. [系統需求](#系統需求)
3. [安裝步驟](#安裝步驟)
4. [快速開始](#快速開始)
5. [後端 gps_launcher.py](#後端-gps_launcherpy)
6. [前端 gps_map.html 操作說明](#前端-gps_maphtml-操作說明)
7. [HTTP API 參考](#http-api-參考)
8. [鍵盤快速鍵](#鍵盤快速鍵)
9. [常見問題](#常見問題)
10. [第三方授權](#第三方授權)

---

## 功能一覽

| 功能 | 說明 |
|------|------|
| 📍 點擊設定 GPS | 點擊地圖或輸入座標，立即推送到 iPhone |
| 🧭 方向移動 | 8 方向位移鍵盤，可設定步距（公尺） |
| 🗺 巡航模式 | 多航點路線規劃，設速自動逐秒前進，支援循環 |
| 📤 GPX 匯出 | 將航點路線匯出為標準 `.gpx` 檔案 |
| 🔍 地點搜尋 | 透過 Nominatim（OpenStreetMap）搜尋全球地點 |
| ⭐ 我的最愛 | 常用地點儲存於 localStorage，跨次使用 |
| 🕐 當地時間 | 即時顯示目標座標當地時間（Open-Meteo API） |
| 📱 多裝置 | 同時管理複數 iPhone，切換無縫 |
| 🔄 自動重連 | Tunnel 或 GPS 斷線後自動重試 |

---

## 系統需求

| 項目 | 需求 |
|------|------|
| **作業系統** | macOS（需 `sudo` 建立 USB tunnel） |
| **Python** | 3.8 以上 |
| **iPhone iOS** | iOS 16 以上 |
| **開發者模式** | iPhone 上**必須開啟**（設定 → 隱私與安全性 → 開發者模式） |
| **連線方式** | USB（Lightning 或 USB-C） |
| **瀏覽器** | Chrome / Firefox / Safari（需支援 Clipboard API） |
| **網路** | 後端僅需本機；搜尋 / 時區功能需可連外網 |

---

## 安裝步驟

### 1. 安裝 Python 套件

```bash
pip install aiohttp pymobiledevice3
```

> 若使用 Python 虛擬環境：
> ```bash
> python3 -m venv venv
> source venv/bin/activate
> pip install aiohttp pymobiledevice3
> ```

### 2. 信任裝置

1. 用 USB 線將 iPhone 連接到 Mac
2. iPhone 出現「是否信任此電腦？」→ 點選 **信任**
3. 確認 Mac 上的 `usbmuxd` 服務已啟動（通常自動執行）

### 3. 開啟 iPhone 開發者模式（必要）

本工具透過 DVT（DeveloperTools）服務模擬 GPS，**開發者模式為必要條件**，未開啟將導致 USB Tunnel 無法建立，裝置持續顯示紅色失敗狀態。

**方法 A — 命令列啟用（iPhone 需已解鎖並完成信任）：**

```bash
python3 -m pymobiledevice3 amfi enable-developer-mode
```

**方法 B — 手動操作：**

1. iPhone 開啟 **設定 → 隱私與安全性 → 開發者模式**
2. 點選開啟 → 確認重新啟動
3. 重開機後，再次確認「開啟開發者模式」

> ⚠️ 開啟開發者模式後，iPhone 需重新啟動才能生效。  
> ⚠️ 請勿在不信任的網路環境下使用開發者模式。

### 4. 下載檔案

```
iphone-gps-controller/
├── gps_launcher.py   ← 後端 Python 服務
└── gps_map.html      ← 前端地圖介面
```

---

## 快速開始

### Step 1：啟動後端

```bash
python3 gps_launcher.py
```

啟動後，終端機會顯示：

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

> ⚠️ **tunnel 需要 sudo 權限**，首次執行時系統會要求輸入 Mac 密碼。

### Step 2：開啟前端地圖

用瀏覽器直接開啟 `gps_map.html`：

```bash
open gps_map.html
# 或直接拖曳到瀏覽器視窗
```

### Step 3：設定 GPS 位置

1. 左側裝置清單確認 iPhone 顯示 **已連線（綠點）**
2. 點擊地圖上任意位置 → iPhone GPS 立即更新
3. 右上角顯示 toast 通知確認成功

---

## 後端 gps_launcher.py

### 啟動參數

```bash
python3 gps_launcher.py [PORT]
```

| 參數 | 說明 | 預設值 |
|------|------|--------|
| `PORT` | HTTP API 監聽埠號 | `8090` |

範例：自訂埠號

```bash
python3 gps_launcher.py 9000
```

### 內部常數（可於程式碼修改）

| 常數 | 說明 | 預設值 |
|------|------|--------|
| `SCAN_SEC` | USB 裝置掃描間隔（秒） | `6` |
| `TUNNEL_TIMEOUT` | 單次 tunnel 建立超時（秒） | `40` |
| `TUNNEL_RETRIES` | Tunnel 失敗重試次數 | `3` |
| `TUNNEL_RETRY_SEC` | 重試等待間隔（秒） | `5` |
| `DEVICE_BOOT_WAIT` | 裝置偵測後等待秒數（再建 tunnel） | `8` |

### 運作流程

```
啟動
 └─ device_scanner（每 6 秒）
      ├─ 偵測到新裝置 → setup_device（等 8s → 建 tunnel → gps_worker）
      └─ 裝置移除 → 結束 tunnel

gps_worker（每裝置一個常駐 coroutine）
 └─ 連接 RSD → DvtProvider → LocationSimulation
      ├─ 收到 SetCmd(lat, lon) → loc.set(lat, lon)
      └─ 收到 ClearCmd        → loc.clear()
```

### 停止服務

按 `Ctrl + C` 即可優雅結束。

---

## 前端 gps_map.html 操作說明

前端連接後端 API（預設 `http://localhost:8090`），每 **1.5 秒** 輪詢裝置狀態。

---

### 📍 一般模式（Normal）

**點擊設定位置**
- 直接點擊地圖 → 立即推送 GPS 座標至 iPhone
- 地圖右下角 HUD 即時顯示滑鼠座標

**手動輸入座標**
1. 在側欄「緯度」「經度」欄位輸入數值
2. 按 **前往** 按鈕確認

**方向移動（Direction Pad）**
- 8 方向按鈕：N / NE / E / SE / S / SW / W / NW
- **步距**欄位設定每步位移（公尺，預設 10m）
- 也可用鍵盤控制（詳見[鍵盤快速鍵](#鍵盤快速鍵)）

**複製座標**
- 按 📋 圖示 → 複製格式：`lat,lon`（保留 6 位小數）

**清除 GPS 模擬**
- 按 **清除 GPS** → 解除位置模擬，iPhone 回復真實 GPS

**裝置狀態卡**

| 欄位 | 說明 |
|------|------|
| 連線狀態 | GPS Service 是否成功連接 |
| 模擬中 | 是否正在傳送假座標 |
| 設定次數 | 本次啟動後累計推送次數 |
| 運作時間 | 裝置被偵測到的累計時間 |

**時區資訊**
- **當地時間**：以 Open-Meteo API 查詢目標座標時區後換算
- **跨日**：目標地與台灣是否為不同日期（⚠ 黃色警示）

---

### 🗺 巡航模式（Cruise）

**建立航點**
1. 切換到「巡航」分頁
2. 點擊地圖依序加入航點（黃色圓形編號標記）
3. 可**拖曳**移動航點位置
4. **雙點擊**標記刪除單一航點
5. 按 **⎌ 復原** 刪除最後一個航點，**清除全部** 重置

**設定速度與循環**
- 速度：km/h（輸入框，直接修改）
- 循環：開啟後抵達終點自動從頭再跑

**路線資訊面板**

| 欄位 | 說明 |
|------|------|
| 航點數 | 目前加入的航點總數 |
| 距離 | 總路線距離（自動顯示 m 或 km） |
| 預估時間 | 以設定速度走完全程的時間 |

**播放巡航**
1. 至少需 2 個航點
2. 按 ▶ **播放** → 每秒推送一步（基於速度內插）
3. 進度條即時顯示「目前步數 / 總步數」
4. 按 ■ **停止** → 停在最後位置（不清除 GPS）

**匯出 GPX**
- 按 **匯出 GPX** → 下載 `cruise_route.gpx`
- 標準 GPX 1.1 格式，可匯入 Google Maps、Garmin 等工具

---

### ⭐ 我的最愛（Favorites）

- 地點資料儲存於瀏覽器 `localStorage`（`gps_favorites_v1`）
- **持久保存**，重新整理或關閉視窗後仍存在

**新增方式**
1. 一般模式下按 **⭐ 加入最愛** → 輸入地標名稱
2. 搜尋結果 Popup 中點「⭐ 加入我的最愛」

**最愛清單按鈕**

| 按鈕 | 功能 |
|------|------|
| 🗺 | 地圖視角跳轉到該地點 |
| 📍 | 將此地點設為 iPhone GPS 位置 |
| ✏ | 重新命名 |
| 🗑 | 刪除 |

- 每個地點同樣顯示**當地時間**與**跨日**標示
- 點擊地點名稱（非按鈕）→ 地圖跳轉

---

### 🔍 地點搜尋（Search）

1. 在側欄搜尋框輸入地名（支援中英文）
2. 按 Enter 或搜尋鈕 → 呼叫 Nominatim API
3. 最多顯示 7 筆結果，每筆顯示國家與當地時間
4. 點擊結果 → 地圖跳轉 + Popup 選單：
   - **📍 設定為 GPS 位置** — 立即推送
   - **✚ 加入航點**（巡航模式下才出現）
   - **⭐ 加入我的最愛**

---

### 裝置管理

- 後端每 6 秒掃描 USB，新裝置 **自動加入清單**
- 拔除裝置後自動從清單移除，前端切換到下一台已連線裝置
- 可同時連接多支 iPhone，點擊裝置卡切換操作目標
- 裝置狀態顏色：
  - 🟢 綠色：GPS 已連線
  - 🟡 黃色：Tunnel OK，GPS 連線中
  - 🔴 紅色：Tunnel 建立中 / 失敗

---

## HTTP API 參考

後端監聽 `http://127.0.0.1:{PORT}`（預設 8090），CORS 全開放。

### GET `/devices`

取得所有已偵測裝置清單。

**回應範例：**
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

設定指定裝置的 GPS 模擬座標。

**Request Body（JSON）：**
```json
{ "lat": 25.03300, "lon": 121.56540 }
```

**回應（成功）：**
```json
{ "ok": true, "lat": 25.033, "lon": 121.5654 }
```

**回應（失敗）：**
```json
{ "ok": false, "error": "GPS not connected" }
```

---

### POST `/device/{idx}/clear`

清除指定裝置的 GPS 模擬，恢復真實位置。

**回應：**
```json
{ "ok": true }
```

---

### GET `/device/{idx}/status`

取得單一裝置的詳細狀態（格式同 `/devices` 陣列中的單一物件）。

---

## 鍵盤快速鍵

> 焦點在輸入框時，方向鍵不生效。

| 按鍵 | 動作 |
|------|------|
| `↑` / `W` | 向北移動 |
| `↓` / `S` | 向南移動 |
| `←` / `A` | 向西移動 |
| `→` / `D` | 向東移動 |
| `Q` | 向西北移動 |
| `E` | 向東北移動 |
| `Z` | 向西南移動 |
| `C` | 向東南移動 |
| `+` / `=` | 地圖放大 |
| `-` | 地圖縮小 |
| `F` | 地圖回到目前座標 |

---

## 常見問題

**Q1：啟動後沒有偵測到裝置？**
- 確認 iPhone 已解鎖並選擇「信任此電腦」
- 嘗試重新插拔 USB 線
- 執行 `python3 -m pymobiledevice3 usbmux list` 確認裝置可被偵測

**Q2：Tunnel 一直失敗（`❌ Tunnel failed after 3 attempts`）？**
- 確認 iPhone 已開啟開發者模式（設定 → 隱私與安全性 → 開發者模式），這是必要條件
- 確認 Mac 已安裝最新版 Xcode Command Line Tools：`xcode-select --install`
- 嘗試重新啟動 iPhone 再連線

**Q3：GPS 設定後 iPhone 位置沒有改變？**
- 確認裝置卡顯示**綠色**已連線
- 確認地圖左上方狀態顯示「模擬中：是」
- 部分 App 需重新開啟才能讀取到新位置

**Q4：前端地圖開啟後顯示「Launcher 未啟動」？**
- 確認 `gps_launcher.py` 正在執行且顯示 `🚀 GPS Launcher port=8090`
- 確認瀏覽器未封鎖 `localhost:8090`（Safari 可能需調整設定）
- 若更改了 PORT，請在 `gps_map.html` 第一行 JS 修改 `const META = 'http://localhost:PORT';`

**Q5：巡航停止後 GPS 位置消失？**
- 設計如此：停止巡航後會**保持在最後停止位置**，不會自動清除 GPS
- 若要恢復真實 GPS，請按「清除 GPS」按鈕

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
