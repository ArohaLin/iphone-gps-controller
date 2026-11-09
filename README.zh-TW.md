# 📍 iPhone GPS Controller

從 Mac 透過 USB 或已配對的 Wi-Fi 連線即時模擬 iPhone GPS 定位的工具。
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
- **後端大幅強化** — 拔插手機後可自動重連（不必重啟 Launcher）、可選 USB／Wi-Fi／自動模式、tunnel 以行程群組完整清除、斷線自我修復、檔案日誌。
- **資料永久保存** — 最愛、巡航路線、底圖設定改存在 Launcher 旁的 `user_data.json`，重新整理、關閉瀏覽器、重開機都不會消失；舊版存在瀏覽器裡的資料會在第一次連上新版時自動搬過去。
- **斷線手機自動歸隊** — 多機巡航中某支手機斷線，重新接上後會自動加回巡航並立即跳到目前巡航位置，不必停止重開；選「🔗 同步全部裝置」巡航時，巡航途中才連上的手機也會自動加入（不增加任何背景輪詢）。
- **巡航路線快速切換** — 巡航面板新增「⭐ 我的巡航路線」下拉選單；已存路線可改名、用目前航點覆蓋、刪除。
- **可選 Google 地圖** — 底圖可在 OpenStreetMap／Google 地圖／Google 衛星之間切換（需自備 Google Maps API 金鑰），只有選 Google 時才會連線 Google。

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
| **連線** | USB（Lightning 或 USB-C），或已配對且與 Mac 在同一區域網路的 Wi-Fi |
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

**Wi-Fi 首次設定：** 先用 USB 完成上述信任與配對，並在 iPhone「設定 → 隱私權與安全性」啟用「開發者模式」（Developer Mode），依提示重新啟動並確認。於 Finder 選取手機，在「一般」勾選「在 Wi-Fi 上顯示此 iPhone」並套用；讓 Mac 與 iPhone 連上同一區域網路，再於地圖選取 Wi-Fi 或自動模式。裝置卡顯示 Wi-Fi 且已連線後，再確認拔線後仍可操作。

Launcher 初始模式為 **USB**；**Wi-Fi** 使用無線連線，**自動** 由後端選擇可用連線。裝置卡標示的是每支手機實際的連線方式。切換模式會中止不相容的連線與巡航；重新連上不會自動重播舊位置或巡航，請手動重新開始。若提示不支援連線模式，需重啟新版 Launcher。

若要手動測試 tunnel，請先停止 Launcher 自動系統；測完關閉手動 tunnel 再啟動 Launcher，兩者不可重複開啟。睡眠及鎖屏下的連線穩定性仍待實機測試；不保證手機在 Finder 中消失時仍能連線。

### 3. 檔案結構

```
iphone-gps-controller/
├── gps_launcher.py   ← 後端 Python 服務
├── gps_map.html      ← 前端地圖介面
├── user_data.json    ← 你的最愛／巡航路線／底圖設定（執行後自動產生，已列入 .gitignore）
└── user_data.json.prev ← 上一版資料的自動備份（存檔前保留一代）
```

> `user_data.json` 含有你的私人位置與 Google 金鑰，已排除在 git 之外，請勿手動上傳。以 `sudo` 啟動時，檔案會自動改回你自己的帳號擁有、權限 600（只有你本人能讀寫）。

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

**建議**：Launcher 啟動後，用瀏覽器開啟 **http://localhost:8090/**（Launcher 會直接提供地圖頁面）。

```bash
open http://localhost:8090/
```

也可以像舊版一樣直接開啟檔案（`open gps_map.html` 或拖曳到瀏覽器視窗），功能相同；但用網址開啟較穩定，且 Google 金鑰可以設定「只允許 http://localhost:8090/* 使用」，比較安全。

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
| `SCAN_SEC` | 裝置掃描間隔（秒） | `6` |
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
 └─ device_scanner（每 6 秒，依連線模式掃描）
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

預設 **USB** 模式只使用 USB 裝置；切換至 **Wi-Fi** 或 **自動** 才納入已配對的無線裝置。Wi-Fi 是 Mac 與手機間的連線方式，不需要將控制 API 開放至區域網路；前端仍連至 `http://localhost:8090`。

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

當連接兩支以上 iPhone 時，裝置清單頂端會出現 **🔗 同步全部裝置** 卡片。選取它後，所有動作（設定位置、清除、巡航步進）只會**同步廣播到已連線且啟用控制的裝置**。卡片會分別顯示連線數與可控制台數。

多機共用單一巡航路線；目前不支援每支手機各自播放不同路線。每張裝置卡會分別標示 USB 或 Wi-Fi。

若只想控制單一裝置，改點該裝置卡片即可。

每張手機卡的 **啟用控制** 開關可用鍵盤操作，儲存成功才更新；設定依手機 UDID 保存在 `device_preferences.json`，重連後仍保留。停用不刪除配對，也保留手機連線與開關，但停止對該手機的 GPS／巡航／步數控制；重新啟用不會自動重播舊位置；但若正在進行「同步全部裝置」巡航，重新啟用的手機會直接跟上目前巡航位置。若儲存失敗，開關維持原狀；若已停用但手機清除模擬失敗，畫面會顯示警告，不能視為已恢復真實定位。舊 Launcher 缺少開關資料時，請重啟新版。

已交給手機執行的捷徑可能無法撤回；停用控制也不保證 iOS 所有定位 App 都即時更新位置。

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

**斷線手機自動歸隊** — 多機巡航時，若其中一支手機斷線（拔線、Wi-Fi 中斷、暫時錯誤），重新連上後會自動加回這次巡航，並立刻收到「目前」的巡航位置（暫停中也會跳到暫停處）；不會重播舊指令。選「🔗 同步全部裝置」巡航時，巡航開始後才連上的手機（且已啟用控制）也會自動加入；只選單一手機巡航時，其他手機連上不會被拉進來。被你**停用控制**的手機不會跟，同步全部巡航中再把開關打開會立刻跟上；被你**清除**的手機在這趟巡航中不會再被自動拉回（重新開始巡航才算）。若所有手機同時斷線（或單機巡航時那支斷線），巡航會自動停止，重新連上後需再按 ▶ 播放。

**儲存 / 讀取路線** — **💾 儲存路線** 把目前航點 + 速度 + 循環設定以名稱存檔（存在 `user_data.json`）；名稱重複時會詢問要覆蓋原路線或另存新路線。**📂 讀取路線** 列出已存路線，每條可 **讀取**、**✏️ 改名**、**⬆️ 用目前航點覆蓋**、**🗑 刪除**。

**⭐ 我的巡航路線（快速切換）** — 巡航面板的下拉選單列出所有已存路線，選一下就套用；會記住上次套用的路線。巡航進行中切換會先詢問，確認後停止目前巡航再套用。

**匯出 GPX** — 下載 `cruise_route.gpx`（標準 GPX 1.1）。

---

### ⭐ 我的最愛

儲存在 Launcher 旁的 `user_data.json`；重新整理、關閉瀏覽器、重開機都會保留。瀏覽器只保留一份快取：Launcher 沒開或還是舊版時，新增／修改會先暫存在瀏覽器，等 Launcher 連上後自動同步（畫面會提示一次）。

**匯出 / 匯入備份** — **💾 匯出備份** 下載 `gps_backup.json`（含最愛、巡航路線與底圖選擇，**不含** Google 金鑰）；換電腦時在新電腦按 **📥 匯入備份** 即可。舊版匯出的備份檔也能匯入。

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

### 🗺 底圖切換（OpenStreetMap／Google）

地圖右上角的選單可切換 **OpenStreetMap**（預設、免費）、**Google 地圖**、**Google 衛星**；選擇會被記住，下次開啟直接套用。搜尋與國家名稱反查仍使用 Nominatim（OpenStreetMap），不會產生 Google 費用。

**設定 Google 金鑰** — 按選單旁的 ⚙️ 貼上你的 Google Maps API 金鑰並儲存（存在 `user_data.json`，匯出備份不會帶出）。沒填金鑰就選 Google 時會跳出設定框並維持 OpenStreetMap；金鑰無效或 Google 載入失敗會自動切回 OpenStreetMap。換新金鑰後請重新整理頁面。

**費用與防超支**（以 2026 年 11 月 Google 官方定價為準）
- 只有選 Google 底圖時才會載入 Google Maps JavaScript API；計費單位是「開一次頁面＝1 次 map load」，拖曳、縮放、巡航移動都不另計。
- 每月前 10,000 次 map load 免費，超出約每 1,000 次 7 美元；個人使用通常落在免費額度內。
- Google Cloud 專案需啟用帳單（綁卡），並啟用「Maps JavaScript API」。
- 建議到 Google Cloud Console → **API 和服務 → Maps JavaScript API → 配額**，把每日 map load 上限設為 300：一個月最多約 9,300 次，保證不超過免費額度；到上限當天 Google 底圖會暫時無法顯示，不會扣款。
- 建議在金鑰的「API 限制」只勾 Maps JavaScript API；若用 http://localhost:8090/ 開啟頁面，可再加「網站限制」`http://localhost:8090/*`。

---

### 裝置管理

- 每 6 秒依 USB／Wi-Fi／自動模式掃描，新裝置自動出現。
- 斷線裝置會移除（有短暫防抖）；前端自動切換到下一台已連線裝置。
- 狀態燈號：🟢 GPS 已連線 · 🟡 tunnel 完成、GPS 連線中 · 🔴 tunnel 建立中 / 失敗。

---

## HTTP API 參考

後端監聽 `http://{HOST}:{PORT}`（預設 `127.0.0.1:8090`）。裝置控制類 API 的 CORS 全開；`/userdata` 例外，只接受本機地圖頁面（`file://` 或 `http://localhost:PORT`、`http://127.0.0.1:PORT`），其他網站一律拒絕。

### 地圖頁面與使用者資料

| 方法與路徑 | 說明 |
|-----------|------|
| `GET /`、`GET /map` | 回傳 `gps_map.html`（不提供其他檔案） |
| `GET /userdata` | 讀取 `{ ok, exists, data: { version, updatedAt, favorites, cruiseRoutes, mapSettings } }` |
| `POST /userdata` | 部分更新，body 可含 `favorites`、`cruiseRoutes`、`mapSettings`（須 `Content-Type: application/json`，上限 5MB）；回傳 `{ ok, updatedAt }` |

存檔採原子寫入並保留上一版於 `user_data.json.prev`；若檔案損壞或沒有權限讀取，Launcher 會拒絕覆寫並回傳 `{ ok: false, error }`。

### 單一裝置

| 方法與路徑 | 說明 |
|-----------|------|
| `GET /devices` | 列出所有偵測到的裝置（狀態物件陣列） |
| `GET /connection-mode` | 取得模式，回傳 `{ "ok": true, "mode": "usb" }`（模式可為 `usb`／`wifi`／`auto`） |
| `POST /connection-mode` | 設定模式，body `{ "mode": "wifi" }`；成功回傳相同結構，中止不相容的連線與巡航 |
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

**Q7：最愛或巡航路線不見了？**
- 新版資料存在 `user_data.json`，不再因為瀏覽器清除資料而消失。
- 若畫面提示「Launcher 未連線（或版本較舊）」，代表資料暫存在瀏覽器；啟動新版 Launcher 後會自動同步。
- 誤刪時可用 `user_data.json.prev`（上一版）救回：先停止 Launcher，把 `.prev` 複製成 `user_data.json` 再重新啟動。

**Q8：選 Google 地圖卻一直跳回 OpenStreetMap？**
- 檢查金鑰是否正確，且 Google Cloud 已啟用 **Maps JavaScript API** 與帳單。
- 若金鑰設了「網站限制」，請改用 http://localhost:8090/ 開啟頁面（直接開檔案時沒有網址，會被網站限制擋下）。
- 是否已達你設定的每日配額上限（隔天太平洋時間 0 點重置）。

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
