#!/usr/bin/env python3
"""
gps_launcher.py

Real-time iPhone GPS location simulator launcher.
Manages USB device detection, tunnel lifecycle, and HTTP API.

Supports multiple iPhones at once: broadcast endpoints (/devices/set,
/devices/clear) fan out to every connected device simultaneously, so several
phones can be driven in sync from a single UI action. Per-device endpoints
remain available for controlling one device at a time.

Author  : Aroha Lin <https://github.com/ArohaLin>
Repo    : https://github.com/ArohaLin/iphone-gps-controller
License : MIT License
Copyright (c) 2026 Aroha Lin
"""

import asyncio, inspect, json, logging, math, os, re, secrets, shutil, signal, socket, sys, time
import urllib.parse
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from logging.handlers import RotatingFileHandler
from contextlib import AsyncExitStack
from aiohttp import web
from aiohttp.web_middlewares import middleware

# Usage: sudo python3 gps_launcher.py [PORT] [HOST]
# HOST defaults to 127.0.0.1. Use 0.0.0.0 to allow iPhone polling over WiFi.
# Example: sudo python3 gps_launcher.py 8090 0.0.0.0
META_PORT        = int(sys.argv[1])   if len(sys.argv) > 1 else 8090
META_HOST        = sys.argv[2].strip() if len(sys.argv) > 2 else '127.0.0.1'
SCAN_SEC         = 6
TUNNEL_TIMEOUT   = 40
TUNNEL_RETRIES   = 3
TUNNEL_RETRY_SEC = 5
DEVICE_BOOT_WAIT = 8
# Require this many consecutive missed scans before treating device as gone.
# Guards against transient scan failures causing remove→re-add loops.
MISS_THRESHOLD   = 2
WIFI_SCAN_TIMEOUT = 12
WIFI_PROBE_TIMEOUT = 3
WIFI_HEALTH_INTERVAL = 10
WIFI_HEALTH_TIMEOUT = 5

# ── Shortcut integration ──────────────────────────────────────────────
# Name must exactly match the Shortcut name on the user's iPhone.
SHORTCUT_NAME  = 'GPS步數增加'
# One-time random token; printed at startup so the user can paste it into
# the iPhone Shortcut for poll-based fallback authentication.
STEPS_TOKEN    = secrets.token_urlsafe(16)
# Per-device pending step queue (udid → int).
# Steps are added here on every add request. When xcrun succeeds the
# count is cleared; if xcrun is unavailable the iPhone Shortcut polls
# /shortcut/sync to drain the queue.
_pending_steps: dict = {}

def _check_devicectl() -> bool:
    """xcrun exists on any Mac with CLT, but devicectl only ships with full Xcode 15+."""
    if not shutil.which('xcrun'):
        return False
    import subprocess as _sp
    try:
        r = _sp.run(['xcrun', 'devicectl', 'help'],
                    capture_output=True, timeout=6)
        return r.returncode == 0
    except Exception:
        return False

# Evaluated once at import time so every call site can use the cached value.
_HAS_DEVICECTL = _check_devicectl()

def _local_ip() -> str:
    """Best-guess LAN IP (for poll-based Shortcut fallback)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

def _free_port(port: int) -> None:
    """Kill any process currently listening on *port* (macOS/Linux)."""
    import subprocess as _sp
    try:
        result = _sp.run(
            ['lsof', '-ti', f'tcp:{port}'],
            capture_output=True, text=True,
        )
        pids = [p.strip() for p in result.stdout.splitlines() if p.strip()]
        my_pid = str(os.getpid())
        for pid in pids:
            if pid == my_pid:
                continue
            try:
                os.kill(int(pid), signal.SIGTERM)
                log.info(f'Sent SIGTERM to PID {pid} (was holding port {port})')
            except (ProcessLookupError, ValueError):
                pass
        if pids:
            time.sleep(0.4)   # give the old process a moment to release the socket
    except Exception as e:
        log.debug(f'_free_port: {e}')

# ── Logging ──────────────────────────────────────────────────────────
_LOG_FILE = os.path.splitext(os.path.abspath(sys.argv[0]))[0] + '.log'
_fmt      = logging.Formatter('%(asctime)s  %(levelname)-7s  %(message)s', datefmt='%H:%M:%S')

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_fmt)

# Rotating log: cap each file at 2 MB, keep 3 old files (~8 MB total max).
# Keeps recent debug history without letting the log grow unbounded.
_file_handler = RotatingFileHandler(
    _LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3, encoding='utf-8'
)
_file_handler.setFormatter(_fmt)

logging.basicConfig(level=logging.INFO, handlers=[_console_handler, _file_handler])
log = logging.getLogger('launcher')
logging.getLogger('aiohttp.access').setLevel(logging.WARNING)
logging.getLogger('aiohttp.server').setLevel(logging.WARNING)

# ── ANSI escape code stripper ─────────────────────────────────────────
_ANSI = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def strip_ansi(s: str) -> str:
    return _ANSI.sub('', s)

# ── Command types ─────────────────────────────────────────────────────
class _SetCmd:
    __slots__ = ('lat', 'lon')
    def __init__(self, lat, lon): self.lat = lat; self.lon = lon

class _ClearCmd:
    def __init__(self, done=None):
        self.done = done


class DevicePreferences:
    """私密版本化設定；損壞時一律停用，拒絕覆寫原檔。"""
    def __init__(self, path):
        self.path = Path(path)
        self.error = None
        try:
            self.values = self.read()
        except Exception as error:
            self.values = {}
            self.error = f'裝置設定無法讀取，已安全停用：{error}'

    def read(self):
        try:
            raw = self.path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return {}
        data = json.loads(raw)
        if (not isinstance(data, dict) or type(data.get('version')) is not int
                or data['version'] != 1 or not isinstance(data.get('devices'), dict)
                or any(not isinstance(k, str) or not k or type(v) is not bool
                       for k, v in data['devices'].items())):
            raise ValueError('設定格式錯誤（需要 version=1 與布林值 devices）')
        return data['devices']

    def enabled(self, udid):
        return False if self.error else self.values.get(udid, True)

    def save(self, udid, enabled):
        if type(enabled) is not bool:
            raise ValueError('enabled 必須為布林值')
        if self.error:
            raise ValueError(self.error)
        values = self.read()  # 寫入前再驗證，不能蓋掉執行中損壞的原檔。
        values[udid] = enabled
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                    dir=self.path.parent, prefix='.device_preferences.', suffix='.tmp',
                    delete=False) as output:
                temporary = output.name
                json.dump({'version': 1, 'devices': values}, output, ensure_ascii=False, indent=2)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            temporary = None
            self.values = values
        finally:
            if temporary is not None:
                os.unlink(temporary)


_preferences = DevicePreferences(Path(__file__).with_name('device_preferences.json'))


class UserDataError(Exception):
    """使用者資料無法安全讀寫；status 為回給前端的 HTTP 狀態碼。"""
    def __init__(self, message, status=500):
        super().__init__(message)
        self.status = status


USER_DATA_MAX_BYTES = 5 * 1024 * 1024
_USER_DATA_LISTS = ('favorites', 'cruiseRoutes')


def _atomic_write_bytes(path, payload, prefix):
    """同目錄暫存檔 → fsync → os.replace；中途失敗不留下半個檔案。"""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent, prefix=prefix,
                                         suffix='.tmp', delete=False) as output:
            temporary = output.name
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)


def _hand_back_to_sudo_user(path):
    """launcher 以 sudo 執行時，把檔案交還給原本的使用者並設為 600（只有本人可讀寫）。
    失敗只記 warning，不影響已完成的存檔。"""
    uid, gid = os.environ.get('SUDO_UID'), os.environ.get('SUDO_GID')
    if uid is not None and gid is not None:
        try:
            if os.geteuid() == 0:
                os.chown(path, int(uid), int(gid))
        except (OSError, ValueError) as error:
            log.warning('無法把 %s 交還給使用者（不影響存檔）：%s', Path(path).name, error)
    try:
        os.chmod(path, 0o600)
    except OSError as error:
        log.warning('無法把 %s 權限設為 600（不影響存檔）：%s', Path(path).name, error)


class UserDataStore:
    """收藏點、巡航路線、地圖設定存在電腦上；原子寫入、保留上一代，損壞時拒絕覆寫。"""
    def __init__(self, path):
        self.path = Path(path)
        self.prev_path = self.path.with_name(self.path.name + '.prev')

    @staticmethod
    def empty():
        return {'version': 1, 'updatedAt': None,
                'favorites': [], 'cruiseRoutes': [], 'mapSettings': {}}

    @staticmethod
    def validate(patch):
        """只檢查頂層型別；內容細節（name/lat/lon/waypoints…）由前端負責。未知欄位忽略。"""
        if not isinstance(patch, dict):
            raise ValueError('資料格式錯誤：需要 JSON 物件')
        fields = {}
        for key in _USER_DATA_LISTS:
            if key in patch:
                value = patch[key]
                if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                    raise ValueError(f'{key} 必須是陣列，且每一項都是物件')
                fields[key] = value
        if 'mapSettings' in patch:
            if not isinstance(patch['mapSettings'], dict):
                raise ValueError('mapSettings 必須是物件')
            fields['mapSettings'] = patch['mapSettings']
        return fields

    def _load(self):
        """回傳 (原始位元組或 None, 資料)。檔案損壞一律拋 UserDataError，呼叫端不得覆寫。"""
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return None, self.empty()
        except PermissionError:
            raise UserDataError(f'沒有權限讀取 {self.path.name}，請用 sudo 啟動 launcher'
                                '（為保護原檔，已停止寫入）') from None
        except OSError as error:
            raise UserDataError(f'無法讀取使用者資料檔 {self.path.name}：{error}') from None
        hint = f'為保護原檔已停止寫入，請檢查 {self.path.name}（上一版在 {self.prev_path.name}）'
        try:
            data = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise UserDataError(f'使用者資料檔損壞（JSON 無法解析：{error}），{hint}') from None
        if (not isinstance(data, dict) or type(data.get('version')) is not int
                or data['version'] != 1):
            raise UserDataError(f'使用者資料檔損壞（需要 version=1），{hint}')
        try:
            fields = self.validate(data)
        except ValueError as error:
            raise UserDataError(f'使用者資料檔損壞（{error}），{hint}') from None
        result = self.empty()
        result.update(fields)
        updated = data.get('updatedAt')
        result['updatedAt'] = updated if isinstance(updated, str) else None
        return raw, result

    def read(self):
        """回傳 (檔案是否存在, 資料)。"""
        raw, data = self._load()
        return raw is not None, data

    def update(self, patch):
        """部分更新：只覆蓋有給的頂層欄位，其餘保留。回傳寫入後的完整資料。"""
        try:
            fields = self.validate(patch)
        except ValueError as error:
            raise UserDataError(str(error), 400) from None
        if not fields:
            raise UserDataError('沒有可更新的欄位（favorites、cruiseRoutes、mapSettings）', 400)
        raw, data = self._load()  # 寫入前再驗證，絕不蓋掉損壞的原檔。
        data.update(fields)
        data['updatedAt'] = (datetime.now(timezone.utc)
                             .isoformat(timespec='milliseconds').replace('+00:00', 'Z'))
        payload = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        try:
            if raw is not None:
                # 先保留上一代，防止前端誤刪後無從找回。
                _atomic_write_bytes(self.prev_path, raw, '.user_data.prev.')
                _hand_back_to_sudo_user(self.prev_path)
            _atomic_write_bytes(self.path, payload, '.user_data.')
        except OSError as error:
            raise UserDataError(f'無法保存使用者資料：{error}') from None
        _hand_back_to_sudo_user(self.path)
        return data


_user_data = UserDataStore(Path(__file__).with_name('user_data.json'))
_user_data_lock = asyncio.Lock()
_MAP_HTML_PATH = Path(__file__).with_name('gps_map.html')
DISABLE_CLEAR_TIMEOUT = 16  # 既有 set、健康檢查、clear 各有 5 秒上限。
_CLEAR_WARNING = '未能確認手機定位已清除'

# ── Single-slot mailbox for latest command ────────────────────────────
class _Mailbox:
    def __init__(self):
        self._cmd   = None
        self._clears = []
        self._event = asyncio.Event()
    def post(self, cmd):
        if isinstance(cmd, _ClearCmd):
            self._clears.append(cmd)
        else:
            self._cmd = cmd
        self._event.set()
    def clear(self):
        self._cmd = None
        if not self._clears:
            self._event.clear()
    async def wait(self):
        await self._event.wait()
        if self._clears:
            cmd = self._clears.pop(0)
        else:
            cmd, self._cmd = self._cmd, None
        if not self._clears and self._cmd is None:
            self._event.clear()
        return cmd

    def discard(self):
        self.clear()
        for cmd in self._clears:
            if cmd.done and not cmd.done.done():
                cmd.done.set_result(False)
        self._clears.clear()
        self._event.clear()

# ── Per-device context ────────────────────────────────────────────────
class DeviceCtx:
    def __init__(self, idx, udid, name, ios, connection_type='usb'):
        self.idx         = idx
        self.udid        = udid
        self.name        = name
        self.ios         = ios
        self.connection_type = connection_type
        self.enabled = _preferences.enabled(udid)
        self._enabled_lock = asyncio.Lock()
        self._control_generation = 0
        self._shortcut_tasks = set()
        self._enabled_tasks = set()
        self._shortcut_ever_sent = False
        self._sync_generation = None
        self._sync_remaining = 0
        self.rsd_host    = None
        self.rsd_port    = None
        self.tunnel_proc = None
        self.setup_task  = None   # setup_device task handle (cancelled on removal)
        self.worker_task = None   # gps_worker task handle (cancelled on removal)
        self.closing     = False  # set during teardown to stop reconnect loop
        self._mailbox    = _Mailbox()
        self._start_t    = time.time()
        self.state = {
            'connected': False, 'simulating': False,
            'last_lat': None, 'last_lon': None,
            'set_count': 0, 'error': None,
            'steps': 0,          # accumulated simulated step count (server-side)
        }

    def to_dict(self):
        return {
            'idx': self.idx, 'udid': self.udid,
            'name': self.name, 'ios': self.ios,
            'connection_type': self.connection_type,
            'enabled': self.enabled,
            **({'preference_error': _preferences.error} if _preferences.error else {}),
            **self.state,
            'uptime_sec': int(time.time() - self._start_t),
            'tunnel_ok':  self.rsd_host is not None,
        }

_devices: dict     = {}
_miss_counts: dict = {}   # udid -> consecutive scan-miss count
_connection_mode = 'usb'
_device_lock = asyncio.Lock()
_usb_snapshot = None
_mode_generation = 0
_wifi_scan_task = None
# 身份不符須人工確認後重啟；探索或切換模式不可自動解除。
_wifi_rejected_udids = set()


# ── Server-side cruise controller ────────────────────────────────────
def _distance_m(a, b):
    """Great-circle distance between two {lat, lon} dictionaries."""
    radius = 6371000.0
    lat1, lat2 = math.radians(a['lat']), math.radians(b['lat'])
    dlat = lat2 - lat1
    dlon = math.radians(b['lon'] - a['lon'])
    h = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return 2 * radius * math.asin(min(1.0, math.sqrt(h)))


class CruiseController:
    """One cruise task shared by every browser and all selected devices."""

    def __init__(self, tick_interval=1.0):
        self.tick_interval = tick_interval
        self.task = None
        self._op_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._run_id = 0
        self.phase = 'stopped'
        self.device_idx = None
        self.target_udids = []   # 目前會收到巡航位置的手機（斷線時暫時移出）
        # 這次巡航的原始名單：斷線重連可自動加回；使用者停用／清除則 forget。
        self._members = set()
        # 這次巡航中被使用者「清除定位」的手機：同步全部模式下也不自動加入。
        self._excluded = set()
        self.waypoints = []
        self.speed = 0.0
        self.loop = False
        self.position = None
        self.target_index = 1
        self.distance_done = 0.0
        self.total_distance = 0.0
        self._last_time = None

    @staticmethod
    def validate_waypoints(raw):
        if not isinstance(raw, list) or len(raw) < 2:
            raise ValueError('請至少提供 2 個航點')
        points = []
        for item in raw:
            try:
                lat, lon = float(item['lat']), float(item['lon'])
            except (KeyError, TypeError, ValueError):
                raise ValueError('航點格式錯誤') from None
            if (not math.isfinite(lat) or not math.isfinite(lon)
                    or not -90 <= lat <= 90 or not -180 <= lon <= 180):
                raise ValueError('航點座標超出範圍')
            points.append({'lat': lat, 'lon': lon})
        return points

    @staticmethod
    def validate_speed(raw):
        try:
            speed = float(raw)
        except (TypeError, ValueError):
            raise ValueError('速度必須大於 0') from None
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError('速度必須大於 0')
        return speed

    def _route_distance(self, start=None, start_index=None):
        if not self.waypoints:
            return 0.0
        pos = start or self.waypoints[0]
        idx = self.target_index if start_index is None else start_index
        distance = 0.0
        for point in self.waypoints[idx:]:
            distance += _distance_m(pos, point)
            pos = point
        return distance

    def _post_position(self):
        if not self.position:
            return
        for udid in self.target_udids:
            ctx = _devices.get(udid)
            if ctx and ctx.enabled and not ctx.closing and ctx.state['connected']:
                ctx._mailbox.post(_SetCmd(self.position['lat'], self.position['lon']))

    async def remove_device(self, udid, forget=False):
        """斷線類呼叫用預設值：只暫時移出，重連後由 rejoin 加回。
        使用者主動意圖（停用裝置、清除該手機定位）傳 forget=True，之後不再自動加回。"""
        async with self._op_lock:
            if forget:
                self._members.discard(udid)
                self._excluded.add(udid)
            if udid in self.target_udids:
                self.target_udids.remove(udid)
                if not self.target_udids:
                    await self._stop_unlocked()

    async def rejoin(self, ctx):
        """GPS 連上、或使用者重新啟用控制時呼叫（事件驅動，不輪詢）。

        單機巡航：只有原本就在這次巡航名單內的手機會加回。
        同步全部巡航：任何已連線且已啟用控制的手機都會加入（巡航途中才連上的也算），
        但這次巡航中被使用者「清除定位」的手機除外。
        只把『目前』巡航位置送給未被使用者停用的手機；
        不重播任何舊指令。暫停中也會送，讓手機停在巡航目前的位置。
        鎖順序：呼叫端（gps_worker）此時不持有任何鎖；_op_lock 內不 await 其他工作，
        因此不會與 _device_lock／_enabled_lock 互相等待。"""
        async with self._op_lock:
            eligible = (ctx.udid in self._members
                        or (self.device_idx == 'all' and ctx.udid not in self._excluded))
            if (self.phase not in ('running', 'paused')
                    or not eligible
                    or not ctx.state['connected']
                    or not ctx.enabled or ctx.closing
                    or _devices.get(ctx.udid) is not ctx
                    or ctx.udid in self.target_udids):
                return False
            joined_new = ctx.udid not in self._members
            self._members.add(ctx.udid)
            self.target_udids.append(ctx.udid)
            if self.device_idx != 'all':
                # 單機巡航：重插後 idx 會變，同步更新讓前端對得上。
                self.device_idx = ctx.idx
            if self.position:
                ctx._mailbox.post(_SetCmd(self.position['lat'], self.position['lon']))
            log.info('[%s] %s%s的巡航並送出目前位置', ctx.name,
                     '已加入同步全部' if joined_new else '重新連線，已加回',
                     '暫停中' if self.phase == 'paused' else '進行中')
            return True

    def _restart_loop(self):
        self.position = dict(self.waypoints[0])
        self.target_index = 1
        self.distance_done = 0.0
        self.total_distance = self._route_distance(self.position, 1)

    def _advance_to(self, now):
        if self.phase != 'running' or self._last_time is None:
            return
        elapsed = max(0.0, now - self._last_time)
        self._last_time = now
        remaining_move = self.speed * 1000.0 / 3600.0 * elapsed

        while remaining_move > 0 and self.phase == 'running':
            if self.target_index >= len(self.waypoints):
                if self.loop:
                    self._restart_loop()
                    self._post_position()
                    if self.total_distance <= 1e-6:
                        break
                    continue
                self.phase = 'completed'
                break

            target = self.waypoints[self.target_index]
            leg_distance = _distance_m(self.position, target)
            if leg_distance <= 1e-6:
                self.position = dict(target)
                self.target_index += 1
                continue

            move = min(remaining_move, leg_distance)
            ratio = move / leg_distance
            self.position = {
                'lat': self.position['lat'] + (target['lat'] - self.position['lat']) * ratio,
                'lon': self.position['lon'] + (target['lon'] - self.position['lon']) * ratio,
            }
            self.distance_done += move
            remaining_move -= move
            if move >= leg_distance - 1e-6:
                self.position = dict(target)
                self.target_index += 1

        if self.target_index >= len(self.waypoints) and self.phase == 'running':
            if not self.loop:
                self.phase = 'completed'

    async def _run(self):
        try:
            while self.phase in ('running', 'paused'):
                if self.phase == 'paused':
                    self._wake.clear()
                    await self._wake.wait()
                    continue

                self._advance_to(asyncio.get_running_loop().time())
                self._post_position()
                if self.phase != 'running':
                    break

                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), self.tick_interval)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            raise

    def status(self):
        progress = 0.0
        if self.total_distance > 0:
            progress = min(1.0, self.distance_done / self.total_distance)
        elif self.phase == 'completed':
            progress = 1.0
        return {
            'ok': True,
            'id': self._run_id,
            'state': self.phase,
            'active': self.phase in ('running', 'paused'),
            'paused': self.phase == 'paused',
            'deviceIdx': self.device_idx,
            'target_udids': list(self.target_udids),
            'waypoints': [dict(p) for p in self.waypoints],
            'speed': self.speed,
            'loop': self.loop,
            'position': dict(self.position) if self.position else None,
            'progress': progress,
            'target_index': self.target_index,
        }

    async def _stop_unlocked(self):
        self.phase = 'stopped'
        self._members.clear()
        self._excluded.clear()
        self._wake.set()
        old_task, self.task = self.task, None
        if old_task and not old_task.done():
            old_task.cancel()
            try:
                await old_task
            except asyncio.CancelledError:
                pass
        return self.status()

    async def stop(self):
        async with self._op_lock:
            return await self._stop_unlocked()

    async def start(self, device_idx, waypoints, speed, loop=False):
        points = self.validate_waypoints(waypoints)
        speed = self.validate_speed(speed)

        async with self._op_lock:
            if device_idx == 'all':
                targets = [c for c in _devices.values() if c.enabled and c.state['connected']]
                selector = 'all'
            else:
                try:
                    idx = int(device_idx)
                except (TypeError, ValueError):
                    raise ValueError('裝置編號格式錯誤') from None
                ctx = next((c for c in _devices.values() if c.idx == idx), None)
                if not ctx:
                    raise LookupError('找不到裝置')
                targets = [ctx] if ctx.enabled and ctx.state['connected'] else []
                selector = idx
            if not targets:
                raise LookupError('沒有已連線的裝置')

            await self._stop_unlocked()
            self._run_id += 1
            self.phase = 'running'
            self.device_idx = selector
            self.target_udids = [c.udid for c in targets]
            self._members = set(self.target_udids)
            self._excluded = set()
            self.waypoints = points
            self.speed = speed
            self.loop = bool(loop)
            self.position = dict(points[0])
            self.target_index = 1
            self.distance_done = 0.0
            self.total_distance = self._route_distance(self.position, 1)
            self._last_time = asyncio.get_running_loop().time()
            self._post_position()
            self.task = asyncio.create_task(self._run(), name='gps-cruise')
            return self.status()

    async def pause(self, allowed=None):
        async with self._op_lock:
            if allowed is not None and not allowed():
                return self.status()
            if self.phase == 'running':
                self._advance_to(asyncio.get_running_loop().time())
                self._post_position()
                if self.phase == 'running':
                    self.phase = 'paused'
                    self._wake.set()
            return self.status()

    async def resume(self):
        async with self._op_lock:
            self.target_udids = [u for u in self.target_udids if u in _devices
                                 and _devices[u].enabled and _devices[u].state['connected']]
            if not self.target_udids:
                return await self._stop_unlocked()
            if self.phase == 'paused':
                self._last_time = asyncio.get_running_loop().time()
                self.phase = 'running'
                self._wake.set()
            return self.status()

    async def update(self, data):
        async with self._op_lock:
            if self.phase not in ('running', 'paused'):
                raise ValueError('目前沒有進行中的巡航')

            now = asyncio.get_running_loop().time()
            was_running = self.phase == 'running'
            if was_running:
                self._advance_to(now)
                self._post_position()

            if 'speed' in data:
                self.speed = self.validate_speed(data['speed'])
            if 'loop' in data:
                self.loop = bool(data['loop'])
            if 'waypoints' in data:
                points = self.validate_waypoints(data['waypoints'])
                next_index = self.target_index
                self.waypoints = points
                if next_index >= len(points):
                    self.phase = 'completed'
                else:
                    self.target_index = next_index
                    self.total_distance = (self.distance_done
                                           + self._route_distance(self.position, next_index))

            if was_running and self.phase == 'running':
                self._last_time = now
            self._wake.set()
            return self.status()


_cruise = CruiseController()

# ── USB device scan (API + JSON CLI fallback, USB-only) ──────────────
# pymobiledevice3 list_devices() includes both USB and Network (WiFi-sync)
# devices. We only want physical USB connections, so every method filters
# on connection_type / ConnectionType == 'USB'.
async def scan_usb_devices():
    try:
        from pymobiledevice3.usbmux import list_devices
        raw = list_devices()
        if inspect.isawaitable(raw):
            raw = await raw
        result = []
        for d in raw:
            conn = getattr(d, 'connection_type', 'USB')
            if conn != 'USB':
                log.debug(f'Skipping Network device {getattr(d, "serial", d)} ({conn})')
                continue
            udid = getattr(d, 'serial', None) or getattr(d, 'udid', None) or str(d)
            name, ios = f'iPhone ({udid[-4:]})', '?'
            cached = _devices.get(udid)
            if cached:
                result.append({'udid': udid, 'name': cached.name, 'ios': cached.ios})
                continue
            # 掃描僅枚舉 USB；名稱與版本由已管理的 RSD 連線補齊。
            # 避免 lockdown 工廠尚未回傳就被取消，無法取得連線來關閉。
            result.append({'udid': udid, 'name': name, 'ios': ios})
        return result  # 成功的空清單就是沒有 USB，不再啟動備援程序。
    except Exception as e:
        log.debug(f'API scan: {e}')

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'pymobiledevice3', 'usbmux', 'list', '--usb', '--simple',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode != 0:
            return None
        text = strip_ansi(out.decode()).strip()
        if text:
            data = json.loads(text)
            if not isinstance(data, list) or not all(isinstance(u, str) and u for u in data):
                return None
            return [{'udid': u, 'name': f'iPhone ({u[-4:]})', 'ios': '?'} for u in data]
    except Exception as e:
        log.debug(f'CLI scan: {e}')
    finally:
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()

    return None  # 掃描失敗與確定沒有裝置必須分開。


async def _browse_wifi_endpoints():
    # 9.9.1 browse_service 在第二個 mDNS socket 建立失敗／取消時，
    # 尚未進入清理區塊。放入我們持有的短命程序，退出即由 OS 回收 sockets。
    script = (
        'import asyncio,json\n'
        'from pymobiledevice3.remote.tunnel_service import browse_remotepairing\n'
        'async def main():\n'
        ' answers=await browse_remotepairing(timeout=2)\n'
        ' print(json.dumps([(a.full_ip,r.port) for r in answers for a in r.addresses]))\n'
        'asyncio.run(main())\n'
    )
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, '-c', script, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        output, _ = await asyncio.wait_for(proc.communicate(), 5)
        if proc.returncode != 0:
            raise OSError('Wi-Fi 探索程序失敗')
        return json.loads(output)
    finally:
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()


async def scan_wifi_devices():
    """9.9.1 remote pairing：只嘗試既有配對，不要求新配對，結果僅是候選。

    不使用回傳整批連線的工廠：握手開始前即持有資源，取消也能關閉。
    None 表示探索失敗；[] 表示探索成功但沒有可用候選。
    此協定回應不能證明裝置身份；同端點多 ID 成功時排除整個端點。
    """
    from pymobiledevice3.remote.tunnel_service import (
        iter_remote_paired_identifiers, RemotePairingTunnelService,
    )
    tasks = []
    probe_failed = False
    candidates = {}
    endpoint_matches = {}
    endpoint_finished = {}
    identifiers = set()

    def unambiguous_candidates():
        accepted = set()
        for endpoint, matches in endpoint_matches.items():
            # 尚未試完所有配對 ID，也不能排除歧義。
            if endpoint_finished.get(endpoint, 0) == len(identifiers) and len(matches) == 1:
                accepted.update(matches)
        return [candidates[u] for u in sorted(accepted) if u not in _wifi_rejected_udids]
    try:
        identifiers = set(iter_remote_paired_identifiers())
        if not identifiers:
            return []
        semaphore = asyncio.Semaphore(4)

        async def probe(identifier, address, port):
            nonlocal probe_failed
            endpoint = (address, port)
            async with semaphore:
                conn = RemotePairingTunnelService(identifier, address, port)
                try:
                    await asyncio.wait_for(conn.connect(autopair=False), WIFI_PROBE_TIMEOUT)
                    # connect 成功僅代表候選；9.9.1 不完整驗證 peer 身份。
                    # 真實握手沒有可用的身份欄位，後續必須核對 RSD.udid。
                    cached = _devices.get(identifier)
                    candidates[identifier] = {
                        'udid': identifier,
                        'name': cached.name if cached else f'iPhone ({identifier[-4:]})',
                        'ios': cached.ios if cached else '?'}
                    endpoint_matches.setdefault(endpoint, set()).add(identifier)
                    endpoint_finished[endpoint] = endpoint_finished.get(endpoint, 0) + 1
                    return candidates[identifier]
                except Exception as error:
                    probe_failed = True
                    endpoint_finished[endpoint] = endpoint_finished.get(endpoint, 0) + 1
                    log.debug('Wi-Fi 配對驗證失敗 %s %s:%s: %s',
                              identifier, address, port, type(error).__name__)
                    return None
                finally:
                    await _close_connection(conn)

        async with asyncio.timeout(WIFI_SCAN_TIMEOUT):
            endpoints = {tuple(endpoint) for endpoint in await _browse_wifi_endpoints()}
            tasks = [asyncio.create_task(probe(u, address, port))
                     for address, port in endpoints for u in identifiers]
            await asyncio.gather(*tasks)
            if probe_failed and not candidates:
                return None
            return unambiguous_candidates()
    except Exception as error:
        log.debug('Wi-Fi 探索失敗: %s', error)
        # 只保留已試完所有 ID 且沒有歧義的端點候選。
        return unambiguous_candidates() or None
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _close_connection(connection):
    try:
        result = connection.close()
        if inspect.isawaitable(result):
            await asyncio.wait_for(result, timeout=3)
    except Exception as error:
        log.debug('連線清理失敗: %s', error)


async def _close_rsd(rsd):
    # 9.9.1 的 RSD.close 在 lockdown.close 失敗時不會關閉 service。
    # 分開釋放它實際持有的兩個連線，避免拔線時漏掉第二個。
    if rsd.lockdown is not None:
        await _close_connection(rsd.lockdown)
    await _close_connection(rsd.service)


async def _usb_present(udid):
    """True=仍插著、False=確定拔線、None=暫時無法確認；不建立 lockdown。"""
    try:
        from pymobiledevice3.usbmux import list_devices
        devices = list_devices()
        if inspect.isawaitable(devices):
            devices = await devices
        return any(d.serial == udid and d.connection_type == 'USB' for d in devices)
    except Exception:
        return None

# ── Graceful tunnel termination ───────────────────────────────────────
def _signal_proc_group(proc, sig):
    """
    Send `sig` to the subprocess's entire process group, falling back to the
    single process if the group can't be resolved. The tunnel is spawned with
    start_new_session=True so it becomes its own group leader (pgid == pid);
    killing the group also takes down any tunnel child processes that
    `pymobiledevice3 remote start-tunnel` forked under sudo.
    """
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except (ProcessLookupError, OSError):
        # Group gone or unavailable — fall back to the bare process
        try:
            proc.send_signal(sig)
        except (ProcessLookupError, OSError):
            pass

async def terminate_tunnel(ctx: DeviceCtx):
    proc = ctx.tunnel_proc
    # Always clear cached tunnel endpoint so stale workers can't reuse it.
    ctx.rsd_host = None
    ctx.rsd_port = None
    if proc is None or proc.returncode is not None:
        ctx.tunnel_proc = None
        return
    try:
        _signal_proc_group(proc, signal.SIGTERM)
        await asyncio.wait_for(proc.wait(), timeout=4.0)
    except asyncio.TimeoutError:
        _signal_proc_group(proc, signal.SIGKILL)
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
    except (ProcessLookupError, OSError):
        pass
    except Exception:
        pass
    ctx.tunnel_proc = None

# ── Single tunnel attempt ─────────────────────────────────────────────
async def _try_start_tunnel(ctx: DeviceCtx) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'pymobiledevice3',
            'remote', 'start-tunnel', '--connection-type', ctx.connection_type,
            '--udid', ctx.udid,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,   # own process group → killpg cleans up children
        )
    except Exception as e:
        log.error(f'[{ctx.name}] Failed to start tunnel process: {e}')
        return False

    ctx.tunnel_proc = proc
    rsd_host = rsd_port = None
    deadline = asyncio.get_event_loop().time() + TUNNEL_TIMEOUT

    while asyncio.get_event_loop().time() < deadline:
        if ctx.closing:   # device unplugged mid-establishment → abort cleanly
            break
        try:
            raw = await asyncio.wait_for(proc.stdout.readline(), timeout=3.0)
        except asyncio.TimeoutError:
            if proc.returncode is not None:
                log.warning(f'[{ctx.name}] Tunnel process exited early (rc={proc.returncode})')
                break
            continue
        if not raw:
            break

        line = strip_ansi(raw.decode(errors='replace')).strip()
        log.debug(f'[tunnel/{ctx.name}] {line}')

        m = re.search(r'RSD\s*Address\s*[:\s]\s*([0-9a-f][0-9a-f:]+)', line, re.I)
        if m: rsd_host = m.group(1).strip().rstrip(':')
        m = re.search(r'RSD\s*Port\s*[:\s]\s*(\d+)', line, re.I)
        if m: rsd_port = int(m.group(1))

        if rsd_host and rsd_port:
            ctx.rsd_host = rsd_host
            ctx.rsd_port = rsd_port
            log.info(f'[{ctx.name}] ✅ Tunnel OK  {rsd_host}:{rsd_port}')
            asyncio.create_task(_drain(proc.stdout))
            return True

    await terminate_tunnel(ctx)
    return False

async def start_tunnel_with_retry(ctx: DeviceCtx) -> bool:
    for attempt in range(1, TUNNEL_RETRIES + 1):
        if ctx.closing:   # device was unplugged mid-setup → stop spawning tunnels
            return False
        if ctx.connection_type == 'usb' and await _usb_present(ctx.udid) is not True:
            return False
        suffix = '' if attempt == 1 else f' (attempt {attempt})'
        log.info(f'[{ctx.name}] Starting tunnel...{suffix}')
        ok = await _try_start_tunnel(ctx)
        if ok: return True
        if ctx.closing:
            return False
        if attempt < TUNNEL_RETRIES:
            log.warning(f'[{ctx.name}] Tunnel failed, retrying in {TUNNEL_RETRY_SEC}s...')
            await terminate_tunnel(ctx)
            await asyncio.sleep(TUNNEL_RETRY_SEC)
    log.error(f'[{ctx.name}] ❌ Tunnel failed after {TUNNEL_RETRIES} attempts')
    return False

async def _drain(stream):
    try:
        async for _ in stream: pass
    except Exception:
        pass

# ── Shortcut trigger ─────────────────────────────────────────────────
async def _run_cmd(args: list, timeout: float = 20.0) -> tuple[int, str]:
    """Run a subprocess and return (returncode, stderr_text)."""
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, err.decode(errors='replace').strip()
    except asyncio.TimeoutError:
        return -1, 'timeout'
    except FileNotFoundError:
        return -2, 'command not found'
    except Exception as e:
        return -3, str(e)
    finally:
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()

async def _trigger_shortcut(ctx: DeviceCtx, count: int) -> bool:
    """
    Try to open the Shortcuts URL on the iPhone via USB.
    Attempts three methods in order:
      1. xcrun devicectl  — requires iOS 17+ and Xcode 15+
      2. ideviceopenurl  — requires iOS 12-16 and libimobiledevice
      3. (fallback)       — pending queue for WiFi polling
    Returns True if any USB method succeeded.
    """
    generation = ctx._control_generation
    def allowed():
        return ctx.enabled and not ctx.closing and ctx._control_generation == generation
    if not allowed():
        return False
    ctx._shortcut_ever_sent = True  # 一旦嘗試交給外部服務就不能保證撤回。
    url = f'shortcuts://run-shortcut?name={urllib.parse.quote(SHORTCUT_NAME)}&input={count}'

    # ── Method 1: xcrun devicectl (iOS 17+, full Xcode 15+ required) ───
    if _HAS_DEVICECTL:
        rc, err = await _run_cmd([
            'xcrun', 'devicectl', 'device', 'process', 'launch',
            '--device', ctx.udid, '--url-scheme', url,
        ])
        if rc == 0:
            log.info(f'[{ctx.name}] ✅ Shortcut triggered via devicectl: +{count} steps')
            return True
        # rc=64  → "No provider" (device is iOS <17)
        # rc=72  → devicectl utility not found at runtime (shouldn't reach here,
        #          but guard anyway)
        # Others → unexpected; log as warning
        if rc not in (64, 72, -1, -2, -3):
            log.warning(f'[{ctx.name}] devicectl rc={rc}: {err[:120]}')

    # ── Method 2: ideviceopenurl (iOS 12-16, libimobiledevice) ───────
    if not allowed():
        return False
    if shutil.which('ideviceopenurl'):
        rc, err = await _run_cmd(['ideviceopenurl', '-u', ctx.udid, url])
        if rc == 0:
            log.info(f'[{ctx.name}] ✅ Shortcut triggered via ideviceopenurl: +{count} steps')
            return True
        log.warning(f'[{ctx.name}] ideviceopenurl rc={rc}: {err[:120]}')

    # ── Method 3: pymobiledevice3 DVT ProcessControl (no external tools) ─
    # Uses the RSD tunnel that's already open for GPS simulation.
    # Tries to launch the shortcuts:// URL scheme via DVT instruments.
    if not allowed():
        return False
    if ctx.rsd_host:
        try:
            from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
            from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
            from pymobiledevice3.services.dvt.instruments.processcontrol import ProcessControl
            async with RemoteServiceDiscoveryService((ctx.rsd_host, ctx.rsd_port)) as rsd:
                async with DvtProvider(rsd) as dvt:
                    async with ProcessControl(dvt) as pc:
                        if not allowed():
                            return False
                        pid = await pc.launch(
                            bundle_id='',
                            arguments=[url],
                            environment={},
                            kill_existing=False,
                            start_suspended=False,
                        )
                        if pid:
                            log.info(f'[{ctx.name}] ✅ Shortcut triggered via DVT ProcessControl: +{count} steps')
                            return True
        except Exception as e:
            log.debug(f'[{ctx.name}] DVT ProcessControl attempt: {e}')

    log.debug(f'[{ctx.name}] No USB trigger succeeded → steps stay in pending queue')
    return False

def _schedule_shortcut(ctx, count):
    if not ctx.enabled or ctx.closing:
        return
    task = asyncio.create_task(_trigger_shortcut_bg(ctx, count, ctx._control_generation))
    ctx._shortcut_tasks.add(task)
    task.add_done_callback(ctx._shortcut_tasks.discard)


async def _trigger_shortcut_bg(ctx: DeviceCtx, count: int, generation=None):
    """Background task: trigger shortcut; clear pending count if it succeeds."""
    generation = ctx._control_generation if generation is None else generation
    if not ctx.enabled or ctx.closing or generation != ctx._control_generation:
        return
    ok = await _trigger_shortcut(ctx, count)
    if ok and ctx.enabled and generation == ctx._control_generation:
        _pending_steps[ctx.udid] = max(0, _pending_steps.get(ctx.udid, 0) - count)

# ── GPS worker ────────────────────────────────────────────────────────
class _DTXDisconnected(ConnectionError):
    pass


async def _gps_operation(ctx, phase, operation, dvt=None):
    """監測既有 DTX；在資源清理前記錄原始失敗階段。"""
    pending = []
    try:
        wait_disconnected = getattr(getattr(dvt, '_dtx', None), 'wait_disconnected', None)
        if wait_disconnected is None:
            return await operation
        work = asyncio.ensure_future(operation)
        disconnected = asyncio.create_task(wait_disconnected())
        pending = [work, disconnected]
        done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        if disconnected in done:
            disconnected.result()
            raise _DTXDisconnected('DTX 定位通道已關閉')
        return work.result()
    except Exception as error:
        if phase != '等待指令' or not isinstance(error, asyncio.TimeoutError):
            log.warning('[%s] %s失敗，enabled=%s，%s: %s',
                        ctx.name, phase, ctx.enabled, type(error).__name__, error)
        raise
    finally:
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


async def _wifi_health(rsd):
    # 9.9.1 remote lockdown 初始化已使用同一 QueryType 請求。
    # 沿用現有連線發送唯讀請求，不讀快取、不開新 RSD、不重送定位。
    if rsd.lockdown is None:
        raise ConnectionError('Wi-Fi 健康檢查缺少 remote lockdown 連線')
    if await rsd.lockdown.query_type() != 'com.apple.mobile.lockdown':
        raise ConnectionError('Wi-Fi remote lockdown 回應類型不符')


async def gps_worker(ctx: DeviceCtx):
    from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
    from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
    from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

    class OwnedRSD(RemoteServiceDiscoveryService):
        async def create_service_connection(self, *args, **kwargs):
            service = await super().create_service_connection(*args, **kwargs)
            resources.push_async_callback(_close_connection, service)
            return service

    class OwnedDvt(DvtProvider):
        async def _open_dtx_connection(self, *args, **kwargs):
            dtx = await super()._open_dtx_connection(*args, **kwargs)
            # 9.9.1 原實作握手成功才記錄 _dtx；取消握手也須能 close。
            self._dtx = dtx
            return dtx

    retry_delay = 2
    channel_recoveries = 0

    while not ctx.closing:
        present = await _usb_present(ctx.udid) if ctx.connection_type == 'usb' else True
        if present is False:
            await teardown_device(ctx)
            break
        if present is None:
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)
            continue
        if not ctx.rsd_host:
            if not await start_tunnel_with_retry(ctx):
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 30)
                continue
        try:
            log.info(f'[{ctx.name}] Connecting GPS...')
            async with AsyncExitStack() as resources:
                rsd = OwnedRSD((ctx.rsd_host, ctx.rsd_port))
                resources.push_async_callback(_close_rsd, rsd)
                await _gps_operation(ctx, 'RSD 連線', asyncio.wait_for(rsd.__aenter__(), TUNNEL_TIMEOUT))
                if ctx.connection_type == 'wifi' and rsd.udid != ctx.udid:
                    _wifi_rejected_udids.add(ctx.udid)
                    raise ConnectionError('Wi-Fi 裝置身份不符')
                # 9.9.1 connect() 會把既有 remote lockdown.all_values 存在此處。
                values = rsd.all_values or {}
                ctx.name = values.get('DeviceName') or ctx.name
                ctx.ios = values.get('ProductVersion') or ctx.ios
                dvt = OwnedDvt(rsd)
                resources.push_async_callback(_close_connection, dvt)
                await _gps_operation(ctx, 'DVT 連線', asyncio.wait_for(dvt.__aenter__(), TUNNEL_TIMEOUT))
                loc = LocationSimulation(dvt)
                resources.push_async_callback(_close_connection, loc)
                await _gps_operation(ctx, '定位服務連線', asyncio.wait_for(loc.__aenter__(), TUNNEL_TIMEOUT), dvt)
                ctx.state['connected'] = True
                ctx.state['error'] = None
                log.info(f'[{ctx.name}] ✅ GPS connected')
                # 三種重連路徑（同 ctx 的 DTX 恢復、USB 暫時錯誤重試、拔插／Wi-Fi
                # 由掃描建新 ctx）都會走到這裡：原本在這次巡航名單內的手機自動跟上。
                await _cruise.rejoin(ctx)

                next_health = asyncio.get_running_loop().time()
                while True:
                    if ctx.connection_type == 'wifi':
                        now = asyncio.get_running_loop().time()
                        if now >= next_health:
                            await _gps_operation(ctx, 'Wi-Fi 心跳',
                                asyncio.wait_for(_wifi_health(rsd), WIFI_HEALTH_TIMEOUT), dvt)
                            next_health = asyncio.get_running_loop().time() + WIFI_HEALTH_INTERVAL
                        try:
                            cmd = await _gps_operation(ctx, '等待指令', asyncio.wait_for(
                                ctx._mailbox.wait(), max(0.001, next_health - asyncio.get_running_loop().time())), dvt)
                        except asyncio.TimeoutError:
                            continue
                    else:
                        cmd = await _gps_operation(ctx, '等待指令', ctx._mailbox.wait(), dvt)
                    if isinstance(cmd, _ClearCmd):
                        cleared = False
                        try:
                            await _gps_operation(ctx, '清除定位', asyncio.wait_for(loc.clear(), WIFI_HEALTH_TIMEOUT), dvt)
                            cleared = True
                            ctx.state.update(simulating=False, last_lat=None, last_lon=None)
                            log.info(f'[{ctx.name}] 🔴 GPS cleared')
                        except _DTXDisconnected:
                            raise
                        except Exception as error:
                            # 清除失敗不等於已復原，也不必拆掉健康連線。
                            ctx.state['error'] = f'{_CLEAR_WARNING}：{error}'
                        finally:
                            if cmd.done and not cmd.done.done():
                                cmd.done.set_result(cleared)
                    elif isinstance(cmd, _SetCmd):
                        if not ctx.enabled:
                            continue
                        await _gps_operation(ctx, '設定定位', asyncio.wait_for(loc.set(cmd.lat, cmd.lon), WIFI_HEALTH_TIMEOUT), dvt)
                        retry_delay = 2
                        ctx.state['simulating'] = True
                        ctx.state['last_lat'] = cmd.lat
                        ctx.state['last_lon'] = cmd.lon
                        ctx.state['set_count'] += 1
                        log.debug(f'[{ctx.name}] 📍 {cmd.lat:.5f},{cmd.lon:.5f}')
        except asyncio.CancelledError:
            ctx.state.update(connected=False, simulating=False, last_lat=None, last_lon=None)
            ctx._mailbox.discard()
            raise
        except Exception as e:
            ctx.state['connected']  = False
            ctx.state['simulating'] = False
            ctx.state['error']      = f'{type(e).__name__}: {e}'
            log.warning('[%s] GPS 工作程序中斷，enabled=%s，%s', ctx.name, ctx.enabled, ctx.state['error'])
            ctx.state.update(last_lat=None, last_lon=None)
            ctx._mailbox.discard()
            # 斷線只暫時移出巡航（不 forget），重連成功後由 _cruise.rejoin 加回。
            await _cruise.remove_device(ctx.udid)
            if ctx.closing:
                break
            if isinstance(e, _DTXDisconnected) and channel_recoveries == 0:
                # DTX 關閉不代表 tunnel 已死。保留 ctx/停用設定，在原 tunnel
                # 重建一次定位服務；再次失敗才走既有清理。不重播舊指令：重連後
                # 只會把『目前』巡航位置送給原本就在這次巡航名單內的手機。
                channel_recoveries += 1
                log.warning('[%s] 保留原 tunnel，嘗試恢復定位服務一次', ctx.name)
                continue
            # If the tunnel subprocess has died (e.g. a quick unplug→replug
            # within the miss window left this ctx in place but its tunnel
            # stale), rebuild it before retrying instead of spinning on a
            # dead RSD endpoint forever.
            await terminate_tunnel(ctx)
            if ctx.connection_type == 'wifi':
                # 真正通道失敗才退出；由探索重新建立新 ctx，絕不重播舊指令。
                # 若這支手機原本就在進行中巡航的名單內，重連後只會收到『目前』
                # 巡航位置（見 CruiseController.rejoin）；被停用的手機不會加回。
                await teardown_device(ctx)
                break
            if await _usb_present(ctx.udid) is False:
                await teardown_device(ctx)
                break
            log.warning(f'[{ctx.name}] 連線中斷，{retry_delay} 秒後確認 USB 再重試: {e}')
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 30)
    log.debug(f'[{ctx.name}] gps_worker exited')

# ── Device scanner ────────────────────────────────────────────────────
_next_device_idx = 0


async def _apply_scan(found, connection_type, generation=None):
    global _next_device_idx, _usb_snapshot
    async with _device_lock:
        if generation is not None and generation != _mode_generation:
            return
        if _connection_mode not in ('auto', connection_type):
            return
        if connection_type == 'usb':
            _usb_snapshot = found
        if found is None:
            return
        found_udids = {d['udid'] for d in found}
        for udid, ctx in list(_devices.items()):
            if ctx.connection_type != connection_type:
                continue
            if udid in found_udids:
                _miss_counts.pop(udid, None)
                continue
            # mDNS 沒出現不代表斷線；Wi-Fi 由有期限的通道健康檢查判斷。
            if connection_type == 'wifi' and not ctx.closing:
                continue
            count = _miss_counts.get(udid, 0) + 1
            _miss_counts[udid] = count
            await teardown_device(ctx)
            if count >= MISS_THRESHOLD:
                _devices.pop(udid, None)
                _miss_counts.pop(udid, None)

        _next_device_idx = max(_next_device_idx, max((c.idx for c in _devices.values()), default=-1) + 1)
        usb_udids = {d['udid'] for d in (_usb_snapshot or [])}
        for info in found:
            udid = info['udid']
            if connection_type == 'wifi' and udid in _wifi_rejected_udids:
                continue
            if connection_type == 'wifi' and _connection_mode == 'auto':
                # USB 探索未知時先等下次，避免初次選擇誤搶 USB。
                if _usb_snapshot is None or udid in usb_udids:
                    continue
            previous = _devices.get(udid)
            if previous and previous.closing:
                await teardown_device(previous)
                _devices.pop(udid, None)
            if udid not in _devices:
                ctx = DeviceCtx(_next_device_idx, udid, info['name'], info['ios'], connection_type)
                _next_device_idx += 1
                _devices[udid] = ctx
                ctx.setup_task = asyncio.create_task(setup_device(ctx))


async def device_scanner():
    # Wi-Fi 的探索與驗證獨立運作，不拖慢 USB 拔插偵測。
    async def wifi_loop():
        global _wifi_scan_task
        try:
            while True:
                if _connection_mode in ('wifi', 'auto'):
                    generation = _mode_generation
                    _wifi_scan_task = asyncio.create_task(scan_wifi_devices())
                    try:
                        found = await _wifi_scan_task
                    except asyncio.CancelledError:
                        if asyncio.current_task().cancelling():
                            raise
                        continue
                    await _apply_scan(found, 'wifi', generation)
                await asyncio.sleep(SCAN_SEC)
        finally:
            await _cancel_task(_wifi_scan_task)
            _wifi_scan_task = None

    wifi_task = asyncio.create_task(wifi_loop())
    try:
        while True:
            if _connection_mode in ('usb', 'auto'):
                generation = _mode_generation
                await _apply_scan(await scan_usb_devices(), 'usb', generation)
            await asyncio.sleep(SCAN_SEC)
    finally:
        await _cancel_task(wifi_task)

async def setup_device(ctx: DeviceCtx):
    await asyncio.sleep(DEVICE_BOOT_WAIT)
    if ctx.closing:
        return
    ctx.worker_task = asyncio.create_task(gps_worker(ctx))

async def _cancel_task(task):
    if task and task is not asyncio.current_task() and not task.done():
        task.cancel()
        try:
            await task
        except BaseException:
            pass

async def teardown_device(ctx: DeviceCtx):
    """
    Fully release a removed device: stop in-flight setup, the reconnect loop,
    and the tunnel process group. Without this the setup/worker keeps running
    on a dead device — re-spawning tunnels and retrying a dead RSD endpoint —
    leaving orphan tunnel processes that block a clean reconnect on re-plug.

    Order matters: cancel setup_task FIRST (it may still be spawning tunnels),
    then worker_task, then kill whatever tunnel_proc remains. terminate_tunnel
    runs last so it reaps the final proc the cancelled tasks left behind.
    """
    ctx.closing = True
    ctx.state.update(connected=False, simulating=False, last_lat=None, last_lon=None)
    ctx._mailbox.discard()
    # 拔線／Wi-Fi 斷線／切換連線模式都屬斷線：同一支手機之後由新 ctx 重連時可加回巡航。
    await _cruise.remove_device(ctx.udid)
    _pending_steps.pop(ctx.udid, None)
    for task in list(ctx._shortcut_tasks):
        await _cancel_task(task)
    await _cancel_task(ctx.setup_task)
    ctx.setup_task = None
    # setup_device may have spawned the worker just before cancellation
    await _cancel_task(ctx.worker_task)
    ctx.worker_task = None
    await terminate_tunnel(ctx)
    log.debug(f'[{ctx.name}] teardown complete')

# ── HTTP API ──────────────────────────────────────────────────────────
def _get_ctx(request):
    idx = int(request.match_info.get('idx', -1))
    for ctx in _devices.values():
        if ctx.idx == idx: return ctx
    return None

async def route_devices(request):
    return web.json_response([c.to_dict() for c in _devices.values()])

def _disabled_response():
    return web.json_response({'ok': False, 'error': '此手機已停用控制'}, status=403)


async def _set_device_enabled(ctx, enabled):
    async with ctx._enabled_lock:
        was_enabled = ctx.enabled
        response = await _set_device_enabled_locked(ctx, enabled)
    # 鎖外呼叫（rejoin 會拿巡航鎖）：同步全部巡航中重新啟用的手機，立刻跟上目前巡航位置。
    if enabled and not was_enabled and ctx.enabled and ctx.state['connected'] and not ctx.closing:
        await _cruise.rejoin(ctx)
    return response


async def _set_device_enabled_locked(ctx, enabled):
    """呼叫端須持有 ctx._enabled_lock。"""
    try:
        _preferences.save(ctx.udid, enabled)
    except Exception as error:
        return web.json_response({'ok': False, 'error': f'無法保存裝置設定：{error}',
                                  'enabled': ctx.enabled}, status=500)
    was_enabled = ctx.enabled
    ctx.enabled = enabled
    if enabled:
        if not was_enabled:
            ctx._mailbox.clear()
        return web.json_response({'ok': True, 'enabled': True})
    ctx._control_generation += 1
    ctx._sync_generation = None
    ctx._sync_remaining = 0
    ctx._mailbox.clear()
    _pending_steps.pop(ctx.udid, None)
    cleared = False
    done = None
    if ctx.state['connected'] and not ctx.closing:
        done = asyncio.get_running_loop().create_future()
        ctx._mailbox.post(_ClearCmd(done))
    # 使用者主動停用：移出這次巡航；同步全部模式下重新啟用會再加入，單機巡航則會結束。
    await _cruise.remove_device(ctx.udid)
    for task in list(ctx._shortcut_tasks):
        await _cancel_task(task)
    if done is not None:
        try:
            cleared = await asyncio.wait_for(asyncio.shield(done), DISABLE_CLEAR_TIMEOUT)
        except asyncio.TimeoutError:
            pass
    warnings = []
    if not cleared:
        warnings.append(_CLEAR_WARNING)
    if ctx._shortcut_ever_sent:
        warnings.append('已交給手機的捷徑無法撤回')
    return web.json_response({'ok': True, 'enabled': False,
                              **({'warning': '；'.join(warnings)} if warnings else {})})


async def route_device_enabled(request):
    ctx = _get_ctx(request)
    if not ctx:
        return web.json_response({'ok': False, 'error': 'device not found'}, status=404)
    try:
        data = await request.json()
        if not isinstance(data, dict) or type(data.get('enabled')) is not bool:
            raise ValueError('enabled 必須為布林值')
    except Exception as error:
        return web.json_response({'ok': False, 'error': str(error)}, status=400)
    # HTTP 呼叫者中途離開也必須完成已保存的停用與清除。
    task = asyncio.create_task(_set_device_enabled(ctx, data['enabled']))
    ctx._enabled_tasks.add(task)
    task.add_done_callback(ctx._enabled_tasks.discard)
    return await asyncio.shield(task)

async def route_connection_mode(request):
    global _connection_mode, _usb_snapshot, _mode_generation
    if request.method == 'POST':
        try:
            data = await request.json()
            mode = data.get('mode') if isinstance(data, dict) else None
            if mode not in ('usb', 'wifi', 'auto'):
                raise ValueError('連線模式須為 usb、wifi 或 auto')
        except Exception as error:
            return web.json_response({'ok': False, 'error': str(error)}, status=400)
        async with _device_lock:
            if mode != _connection_mode:
                _connection_mode = mode
                _mode_generation += 1
                _usb_snapshot = None
                await _cancel_task(_wifi_scan_task)
                for udid, ctx in list(_devices.items()):
                    if mode != 'auto' and ctx.connection_type != mode:
                        await teardown_device(ctx)
                        _devices.pop(udid, None)
                        _miss_counts.pop(udid, None)
    return web.json_response({'ok': True, 'mode': _connection_mode})

async def route_set(request):
    ctx = _get_ctx(request)
    if not ctx:
        return web.json_response({'ok': False, 'error': 'device not found'}, status=404)
    if not ctx.enabled:
        return _disabled_response()
    if not ctx.state['connected']:
        return web.json_response({'ok': False, 'error': 'GPS not connected'}, status=503)
    generation = ctx._control_generation
    try:
        data = await request.json()
        lat, lon = float(data['lat']), float(data['lon'])
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=400)
    if not ctx.enabled or generation != ctx._control_generation:
        return _disabled_response()
    await _cruise.pause(lambda: ctx.enabled and generation == ctx._control_generation)
    if not ctx.enabled or generation != ctx._control_generation:
        return _disabled_response()
    ctx._mailbox.post(_SetCmd(lat, lon))
    return web.json_response({'ok': True, 'lat': lat, 'lon': lon})

async def route_clear(request):
    ctx = _get_ctx(request)
    if not ctx:
        return web.json_response({'ok': False, 'error': 'device not found'}, status=404)
    # 使用者主動清除這支手機的定位：永久移出這次巡航，重連也不自動加回。
    await _cruise.remove_device(ctx.udid, forget=True)
    if not ctx.state['connected']:
        return web.json_response({'ok': False, 'error': 'GPS not connected'}, status=503)
    ctx._mailbox.post(_ClearCmd())
    return web.json_response({'ok': True})

async def route_device_status(request):
    ctx = _get_ctx(request)
    if not ctx:
        return web.json_response({'ok': False, 'error': 'device not found'}, status=404)
    return web.json_response(ctx.to_dict())


# ── Cruise endpoints ─────────────────────────────────────────────────
def _cruise_error(error):
    status = 404 if isinstance(error, LookupError) else 400
    return web.json_response({'ok': False, 'error': str(error)}, status=status)


async def route_cruise_start(request):
    try:
        data = await request.json()
        result = await _cruise.start(
            data.get('deviceIdx'), data.get('waypoints'), data.get('speed'),
            data.get('loop', False),
        )
        return web.json_response(result)
    except (ValueError, LookupError) as e:
        return _cruise_error(e)
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=400)


async def route_cruise_status(request):
    return web.json_response(_cruise.status())


async def route_cruise_pause(request):
    return web.json_response(await _cruise.pause())


async def route_cruise_resume(request):
    return web.json_response(await _cruise.resume())


async def route_cruise_update(request):
    try:
        data = await request.json()
        if not isinstance(data, dict) or not any(
                key in data for key in ('speed', 'waypoints', 'loop')):
            raise ValueError('沒有可更新的巡航設定')
        return web.json_response(await _cruise.update(data))
    except (ValueError, LookupError) as e:
        return _cruise_error(e)
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=400)


async def route_cruise_stop(request):
    return web.json_response(await _cruise.stop())

# ── User data (favorites / cruise routes / map settings) ─────────────
# Safari 以 file:// 開啟時 localStorage 會遺失，改存在電腦上的 user_data.json。
async def _read_limited_body(request, limit):
    if request.content_length is not None and request.content_length > limit:
        raise UserDataError('資料太大（上限 5MB）', 413)
    chunks, size = [], 0
    async for chunk in request.content.iter_any():
        size += len(chunk)
        if size > limit:
            raise UserDataError('資料太大（上限 5MB）', 413)
        chunks.append(chunk)
    return b''.join(chunks)


async def route_userdata_get(request):
    async with _user_data_lock:
        try:
            exists, data = _user_data.read()
        except UserDataError as error:
            return web.json_response({'ok': False, 'error': str(error)}, status=error.status)
    return web.json_response({'ok': True, 'exists': exists, 'data': data})


async def route_userdata_post(request):
    try:
        body = await _read_limited_body(request, USER_DATA_MAX_BYTES)
        try:
            patch = json.loads(body.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise UserDataError('資料格式錯誤：不是有效的 JSON', 400) from None
        async with _user_data_lock:
            data = _user_data.update(patch)
    except UserDataError as error:
        if error.status >= 500:
            log.warning('使用者資料未保存：%s', error)
        return web.json_response({'ok': False, 'error': str(error)}, status=error.status)
    return web.json_response({'ok': True, 'updatedAt': data['updatedAt']})


async def route_map_page(request):
    # 只回傳固定的 gps_map.html，不接受任何路徑參數，避免任意檔案讀取。
    if not _MAP_HTML_PATH.is_file():
        return web.json_response({'ok': False, 'error': '找不到 gps_map.html'}, status=404)
    return web.FileResponse(_MAP_HTML_PATH, headers={
        'Content-Type': 'text/html; charset=utf-8',
        'Cache-Control': 'no-cache',
    })

# ── Broadcast endpoints ───────────────────────────────────────────────
# Fan out a single command to every currently-connected device. Each
# device has its own mailbox + GPS worker, so posting is non-blocking
# and the iPhones move in parallel.
async def route_set_all(request):
    generations = {c.udid: c._control_generation for c in _devices.values() if c.enabled}
    try:
        data = await request.json()
        lat, lon = float(data['lat']), float(data['lon'])
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=400)

    def allowed(c):
        return c.enabled and c.state['connected'] and generations.get(c.udid) == c._control_generation
    targets = [c for c in _devices.values() if allowed(c)]
    if not targets:
        return web.json_response(
            {'ok': False, 'error': 'no connected devices'}, status=503,
        )
    await _cruise.pause(lambda: any(allowed(c) for c in targets))
    targets = [c for c in targets if allowed(c)]
    if not targets:
        return _disabled_response()
    for ctx in targets:
        ctx._mailbox.post(_SetCmd(lat, lon))
    return web.json_response({
        'ok': True, 'lat': lat, 'lon': lon,
        'count': len(targets),
        'idxs':  [c.idx for c in targets],
    })

async def route_clear_all(request):
    await _cruise.stop()
    targets = [c for c in _devices.values() if c.state['connected']]
    if not targets:
        return web.json_response(
            {'ok': False, 'error': 'no connected devices'}, status=503,
        )
    for ctx in targets:
        ctx._mailbox.post(_ClearCmd())
    return web.json_response({
        'ok': True,
        'count': len(targets),
        'idxs':  [c.idx for c in targets],
    })

# ── Step-count endpoints ──────────────────────────────────────────────
# The server tracks a per-device step counter in memory.
# Route handlers are intentionally thin — insert your pymobiledevice3 /
# HealthKit call inside each handler where marked, then the server count
# stays in sync with the device.

async def route_steps_get(request):
    ctx = _get_ctx(request)
    if not ctx:
        return web.json_response({'ok': False, 'error': 'device not found'}, status=404)
    return web.json_response({'ok': True, 'steps': ctx.state['steps']})

async def route_steps_add(request):
    ctx = _get_ctx(request)
    if not ctx:
        return web.json_response({'ok': False, 'error': 'device not found'}, status=404)
    if not ctx.enabled:
        return _disabled_response()
    generation = ctx._control_generation
    try:
        data  = await request.json()
        count = max(1, int(data['count']))
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=400)
    if not ctx.enabled or generation != ctx._control_generation:
        return _disabled_response()
    ctx.state['steps'] += count
    _pending_steps[ctx.udid] = _pending_steps.get(ctx.udid, 0) + count
    log.info(f'[{ctx.name}] +{count} steps  (total={ctx.state["steps"]} pending={_pending_steps[ctx.udid]})')
    # Fire-and-forget: trigger the iPhone Shortcut in the background.
    # If xcrun succeeds the pending counter is cleared automatically.
    _schedule_shortcut(ctx, count)
    return web.json_response({
        'ok': True, 'steps': ctx.state['steps'], 'added': count,
        'pending': _pending_steps.get(ctx.udid, 0),
    })

async def route_steps_remove(request):
    ctx = _get_ctx(request)
    if not ctx:
        return web.json_response({'ok': False, 'error': 'device not found'}, status=404)
    if not ctx.enabled:
        return _disabled_response()
    generation = ctx._control_generation
    try:
        data  = await request.json()
        count = max(1, int(data['count']))
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=400)
    if not ctx.enabled or generation != ctx._control_generation:
        return _disabled_response()
    removed = min(count, ctx.state['steps'])
    # ── TODO: call pymobiledevice3 / HealthKit here to remove `removed` steps ──
    ctx.state['steps'] -= removed
    log.info(f'[{ctx.name}] -{removed} steps  (total {ctx.state["steps"]})')
    return web.json_response({'ok': True, 'steps': ctx.state['steps'], 'removed': removed})

async def route_steps_reset(request):
    ctx = _get_ctx(request)
    if not ctx:
        return web.json_response({'ok': False, 'error': 'device not found'}, status=404)
    if not ctx.enabled:
        return _disabled_response()
    old = ctx.state['steps']
    ctx.state['steps'] = 0
    log.info(f'[{ctx.name}] steps reset (was {old})')
    return web.json_response({'ok': True, 'steps': 0})

# Broadcast step operations ─────────────────────────────────────────
async def route_steps_add_all(request):
    generations = {c.udid: c._control_generation for c in _devices.values() if c.enabled}
    try:
        data  = await request.json()
        count = max(1, int(data['count']))
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=400)
    targets = [c for c in _devices.values() if c.enabled
               and generations.get(c.udid) == c._control_generation]
    for ctx in targets:
        ctx.state['steps'] += count
        _pending_steps[ctx.udid] = _pending_steps.get(ctx.udid, 0) + count
        _schedule_shortcut(ctx, count)
    return web.json_response({
        'ok': True, 'added': count, 'count': len(targets),
        'device_steps': {str(c.idx): c.state['steps'] for c in targets},
    })

async def route_steps_remove_all(request):
    generations = {c.udid: c._control_generation for c in _devices.values() if c.enabled}
    try:
        data  = await request.json()
        count = max(1, int(data['count']))
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=400)
    targets = [c for c in _devices.values() if c.enabled
               and generations.get(c.udid) == c._control_generation]
    for ctx in targets:
        removed = min(count, ctx.state['steps'])
        ctx.state['steps'] -= removed
    return web.json_response({'ok': True, 'count': len(targets)})

# ── Shortcut API endpoints ────────────────────────────────────────────
async def route_shortcut_info(request):
    """Returns status, token, local IP — for the UI setup panel."""
    global SHORTCUT_NAME
    has_idevurl = shutil.which('ideviceopenurl') is not None
    return web.json_response({
        'ok':            True,
        'shortcut_name': SHORTCUT_NAME,
        'token':         STEPS_TOKEN,
        'has_xcrun':     _HAS_DEVICECTL,      # true only when devicectl itself is present
        'has_idevurl':   has_idevurl,
        'usb_ok':        _HAS_DEVICECTL or has_idevurl,
        'local_ip':      _local_ip(),
        'meta_host':     META_HOST,
        'meta_port':     META_PORT,
        'network_ok':    META_HOST != '127.0.0.1',
        'pending':       {ctx.idx: _pending_steps.get(ctx.udid, 0)
                          for ctx in _devices.values()},
    })

async def route_shortcut_config(request):
    """Update the Shortcut name at runtime."""
    global SHORTCUT_NAME
    try:
        data = await request.json()
        name = data.get('name', '').strip()
        if not name:
            return web.json_response({'ok': False, 'error': 'name cannot be empty'}, status=400)
        SHORTCUT_NAME = name
        log.info(f'Shortcut name updated → "{SHORTCUT_NAME}"')
        return web.json_response({'ok': True, 'shortcut_name': SHORTCUT_NAME})
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=400)

async def route_shortcut_sync(request):
    """
    Poll endpoint for the iPhone Shortcut (fallback when xcrun is unavailable).
    The Shortcut calls GET /shortcut/sync?token=TOKEN&idx=IDX, reads the
    returned step count, writes it to HealthKit, then POSTs /shortcut/ack.
    """
    if request.rel_url.query.get('token') != STEPS_TOKEN:
        return web.json_response({'ok': False, 'error': 'unauthorized'}, status=401)
    try:
        idx = int(request.rel_url.query.get('idx', 0))
    except ValueError:
        idx = 0
    for ctx in _devices.values():
        if ctx.idx == idx:
            if not ctx.enabled:
                return _disabled_response()
            pending = _pending_steps.get(ctx.udid, 0)
            # 記錄本次實際交付量；舊格式 ack 不需新增欄位。
            ctx._sync_generation = ctx._control_generation
            ctx._sync_remaining = pending
            if pending:
                ctx._shortcut_ever_sent = True
            return web.json_response({'ok': True, 'steps': pending, 'idx': idx,
                                      'generation': ctx._control_generation})
    return web.json_response({'ok': True, 'steps': 0, 'idx': idx})

async def route_shortcut_ack(request):
    """
    Acknowledgement from the iPhone Shortcut after Health write succeeds.
    Body: {"idx": 0, "count": N}
    """
    if request.rel_url.query.get('token') != STEPS_TOKEN:
        return web.json_response({'ok': False, 'error': 'unauthorized'}, status=401)
    try:
        data  = await request.json()
        idx   = int(data.get('idx', 0))
        count = int(data.get('count', 0))
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=400)
    for ctx in _devices.values():
        if ctx.idx == idx:
            if not ctx.enabled:
                return _disabled_response()
            generation = data.get('generation')
            if (ctx._sync_generation != ctx._control_generation
                    or ('generation' in data and
                        (type(generation) is not int or generation != ctx._control_generation))):
                return web.json_response({'ok': False, 'error': '捷徑回報已過期，請重新同步'}, status=409)
            confirmed = min(max(0, count), ctx._sync_remaining, _pending_steps.get(ctx.udid, 0))
            ctx._sync_remaining -= confirmed
            _pending_steps[ctx.udid] = max(0, _pending_steps.get(ctx.udid, 0) - confirmed)
            log.info(f'[{ctx.name}] ✅ Health ack: {confirmed} steps confirmed (remaining {_pending_steps[ctx.udid]})')
            return web.json_response({'ok': True, 'confirmed': confirmed,
                                      'remaining': _pending_steps.get(ctx.udid, 0)})
    return web.json_response({'ok': False, 'error': 'device not found'}, status=404)

_USERDATA_ROUTE = '/userdata'
_LOOPBACK_HOSTS = ('localhost', '127.0.0.1', '::1')


def _userdata_origin_allowed(request):
    """/userdata 存有 API key 與住家等位置：只允許本機頁面讀寫。
    允許的 Origin：沒有 Origin、'null'（file:// 頁面）、http://localhost:PORT、http://127.0.0.1:PORT。
    另檢查 Host 必須是本機名稱，擋 DNS rebinding（同源 GET 不帶 Origin）。"""
    origin = request.headers.get('Origin')
    if origin is not None and origin not in (
            'null', f'http://localhost:{META_PORT}', f'http://127.0.0.1:{META_PORT}'):
        return False
    try:
        hostname = urllib.parse.urlsplit('//' + (request.headers.get('Host') or '')).hostname
    except ValueError:
        return False
    return hostname in _LOOPBACK_HOSTS


async def _userdata_guarded(request, handler):
    origin = request.headers.get('Origin')
    if not _userdata_origin_allowed(request):
        log.warning('已拒絕來源不明的 /userdata 請求：Origin=%s Host=%s',
                    origin, request.headers.get('Host'))
        resp = web.json_response({'ok': False, 'error': '不允許的來源'}, status=403)
    elif request.method == 'OPTIONS':
        resp = web.Response(headers={
            'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })
    elif request.method == 'POST' and request.content_type != 'application/json':
        # 擋掉不需 preflight 的 text/plain／表單跨站偷寫。
        resp = web.json_response({'ok': False, 'error': '請以 application/json 送出'}, status=415)
    else:
        resp = await handler(request)
    # 絕不回 *：允許時回原 Origin（含 'null'），沒有 Origin 就不需要 CORS 標頭。
    resp.headers.pop('Access-Control-Allow-Origin', None)
    if origin is not None and resp.status != 403:
        resp.headers['Access-Control-Allow-Origin'] = origin
    resp.headers['Vary'] = 'Origin'
    return resp


@middleware
async def cors_middleware(request, handler):
    if request.path == _USERDATA_ROUTE:
        return await _userdata_guarded(request, handler)
    if request.method == 'OPTIONS':
        return web.Response(headers={
            'Access-Control-Allow-Origin':  '*',
            'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type',
        })
    resp = await handler(request)
    resp.headers['Access-Control-Allow-Origin'] = '*'
    return resp

# ── Entry point ───────────────────────────────────────────────────────
def _build_app():
    """建立 HTTP 路由表（獨立出來讓測試能驗證真正的路由）。"""
    app = web.Application(middlewares=[cors_middleware])
    # 地圖頁面：也可用 http://localhost:PORT/ 開啟（只回傳固定的 gps_map.html）
    app.router.add_get ('/',                    route_map_page)
    app.router.add_get ('/map',                 route_map_page)
    # 使用者資料（收藏點、巡航路線、地圖設定）
    app.router.add_get ('/userdata',            route_userdata_get)
    app.router.add_post('/userdata',            route_userdata_post)
    app.router.add_get ('/connection-mode',     route_connection_mode)
    app.router.add_post('/connection-mode',     route_connection_mode)
    app.router.add_get ('/devices',             route_devices)
    app.router.add_post('/device/{idx}/set',    route_set)
    app.router.add_post('/device/{idx}/clear',  route_clear)
    app.router.add_post('/device/{idx}/enabled', route_device_enabled)
    app.router.add_get ('/device/{idx}/status', route_device_status)
    # broadcast to all connected devices
    app.router.add_post('/devices/set',         route_set_all)
    app.router.add_post('/devices/clear',       route_clear_all)
    # Server-side cruise (survives browser backgrounding/closing)
    app.router.add_post('/cruise/start',         route_cruise_start)
    app.router.add_get ('/cruise/status',        route_cruise_status)
    app.router.add_post('/cruise/pause',         route_cruise_pause)
    app.router.add_post('/cruise/resume',        route_cruise_resume)
    app.router.add_post('/cruise/update',        route_cruise_update)
    app.router.add_post('/cruise/stop',          route_cruise_stop)
    # Steps per-device
    app.router.add_get ('/device/{idx}/steps',        route_steps_get)
    app.router.add_post('/device/{idx}/steps/add',    route_steps_add)
    app.router.add_post('/device/{idx}/steps/remove', route_steps_remove)
    app.router.add_post('/device/{idx}/steps/reset',  route_steps_reset)
    # Steps broadcast
    app.router.add_post('/devices/steps/add',         route_steps_add_all)
    app.router.add_post('/devices/steps/remove',      route_steps_remove_all)
    # Shortcut integration
    app.router.add_get ('/shortcut/info',   route_shortcut_info)
    app.router.add_post('/shortcut/config', route_shortcut_config)
    app.router.add_get ('/shortcut/sync',   route_shortcut_sync)
    app.router.add_post('/shortcut/ack',    route_shortcut_ack)
    _step_paths = [
        '/device/{idx}/steps/add', '/device/{idx}/steps/remove',
        '/device/{idx}/steps/reset',
        '/devices/steps/add', '/devices/steps/remove',
    ]
    for path in ['/connection-mode', '/device/{idx}/set', '/device/{idx}/clear', '/device/{idx}/enabled',
                 '/devices/set', '/devices/clear',
                 '/cruise/start', '/cruise/pause', '/cruise/resume',
                 '/cruise/update', '/cruise/stop', '/userdata'] + _step_paths:
        app.router.add_route('OPTIONS', path, lambda r: web.Response())
    return app


async def main():
    asyncio.create_task(device_scanner())

    app = _build_app()

    # Kill any previous instance occupying the same port
    _free_port(META_PORT)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, META_HOST, META_PORT,
                      reuse_address=True, reuse_port=True).start()

    log.info(f'🚀 GPS Launcher  {META_HOST}:{META_PORT}  log={_LOG_FILE}')
    log.info(f'   地圖頁面  http://localhost:{META_PORT}/')
    log.info(f'   GET/POST http://localhost:{META_PORT}/userdata  (收藏點／巡航路線存檔：{_user_data.path.name})')
    log.info(f'   GET  http://localhost:{META_PORT}/devices')
    log.info(f'   POST http://localhost:{META_PORT}/device/{{idx}}/set')
    log.info(f'   POST http://localhost:{META_PORT}/device/{{idx}}/clear')
    log.info(f'   POST http://localhost:{META_PORT}/devices/set    (broadcast)')
    log.info(f'   POST http://localhost:{META_PORT}/devices/clear  (broadcast)')
    log.info(f'   GET  http://localhost:{META_PORT}/device/{{idx}}/steps')
    log.info(f'   POST http://localhost:{META_PORT}/device/{{idx}}/steps/add')
    log.info(f'   POST http://localhost:{META_PORT}/device/{{idx}}/steps/remove')
    _has_idevurl  = shutil.which('ideviceopenurl') is not None
    log.info(f'── Shortcut integration ────────────────────────────────')
    log.info(f'   devicectl (iOS 17+)  : {_HAS_DEVICECTL}'
             + ('' if _HAS_DEVICECTL else '  (需完整版 Xcode 15+，非 CLT)'))
    log.info(f'   ideviceopenurl(≤16)  : {_has_idevurl}'
             + ('' if _has_idevurl else '  (brew install libimobiledevice)'))
    log.info(f'   Shortcut name        : {SHORTCUT_NAME}')
    log.info(f'   Steps token          : {STEPS_TOKEN}')
    log.info(f'   Bound to             : {META_HOST}:{META_PORT}')
    log.info(f'   Local IP             : {_local_ip()}')
    if not (_HAS_DEVICECTL or _has_idevurl):
        log.warning('步數無法自動觸發 iPhone 捷徑，建議選擇下列其一：')
        log.warning('  A) brew install libimobiledevice   ← 推薦，支援 iOS ≤16')
        log.warning('  B) 從 App Store 安裝完整版 Xcode  ← 支援 iOS 17+')
        if META_HOST == '127.0.0.1':
            log.warning('  C) 改用 WiFi 輪詢：sudo python3 %s %d 0.0.0.0', sys.argv[0], META_PORT)
    log.info('Scanning USB devices...')

    try:
        await asyncio.Event().wait()
    finally:
        await _cleanup_all()

async def _cleanup_all():
    """Tear down every device tunnel on shutdown so no start-tunnel children
    are orphaned (orphaned tunnels hold the device and block the next run)."""
    await _cruise.stop()
    ctxs = list(_devices.values())
    _devices.clear()
    if not ctxs:
        return
    log.info(f'Cleaning up {len(ctxs)} device tunnel(s)...')
    await asyncio.gather(*(teardown_device(c) for c in ctxs), return_exceptions=True)
    log.info('Cleanup done')

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info('Stopped')
