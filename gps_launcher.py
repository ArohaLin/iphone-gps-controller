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

import asyncio, inspect, json, logging, os, re, secrets, shutil, signal, socket, sys, time
import urllib.parse
from logging.handlers import RotatingFileHandler
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

class _ClearCmd: pass

# ── Single-slot mailbox for latest command ────────────────────────────
class _Mailbox:
    def __init__(self):
        self._cmd   = None
        self._event = asyncio.Event()
    def post(self, cmd):
        self._cmd = cmd
        self._event.set()
    async def wait(self):
        await self._event.wait()
        self._event.clear()
        cmd, self._cmd = self._cmd, None
        return cmd

# ── Per-device context ────────────────────────────────────────────────
class DeviceCtx:
    def __init__(self, idx, udid, name, ios):
        self.idx         = idx
        self.udid        = udid
        self.name        = name
        self.ios         = ios
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
            **self.state,
            'uptime_sec': int(time.time() - self._start_t),
            'tunnel_ok':  self.rsd_host is not None,
        }

_devices: dict     = {}
_miss_counts: dict = {}   # udid -> consecutive scan-miss count

# ── USB device scan (3 fallback methods, USB-only) ────────────────────
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
            try:
                from pymobiledevice3.lockdown import create_using_usbmux
                ld = create_using_usbmux(serial=udid)
                if inspect.isawaitable(ld): ld = await ld
                vals = ld.all_values
                name = vals.get('DeviceName', name)
                ios  = vals.get('ProductVersion', '?')
            except Exception:
                pass
            result.append({'udid': udid, 'name': name, 'ios': ios})
        if result: return result
    except Exception as e:
        log.debug(f'API scan: {e}')

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'pymobiledevice3', 'usbmux', 'list',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        text = strip_ansi(out.decode()).strip()
        if text:
            data = json.loads(text)
            if isinstance(data, dict): data = [data]
            result = []
            for d in data:
                # Filter: only USB-connected devices
                if d.get('ConnectionType', 'USB') != 'USB':
                    log.debug(f'Skipping Network device {d.get("SerialNumber", "?")} (CLI)')
                    continue
                udid = d.get('SerialNumber') or d.get('udid', '')
                if udid:
                    result.append({
                        'udid': udid,
                        'name': d.get('DeviceName', f'iPhone ({udid[-4:]})'),
                        'ios':  d.get('ProductVersion', '?'),
                    })
            if result: return result
    except Exception as e:
        log.debug(f'CLI scan: {e}')

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, '-m', 'pymobiledevice3', 'usbmux', 'list',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        text_raw = out.decode()
        # Regex fallback cannot determine connection type from plain text.
        # Only use it if the output contains no indication of "Network" connections.
        if 'Network' not in text_raw:
            udids = re.findall(r'[0-9a-f]{40}', text_raw, re.I)
            if udids:
                return [{'udid': u, 'name': f'iPhone ({u[-4:]})', 'ios': '?'} for u in udids]
        else:
            log.debug('Regex scan: output contains Network devices, skipping to avoid false positives')
    except Exception as e:
        log.debug(f'Regex scan: {e}')

    return []

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
            'remote', 'start-tunnel', '--udid', ctx.udid,
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

    _signal_proc_group(proc, signal.SIGKILL)
    ctx.tunnel_proc = None
    return False

async def start_tunnel_with_retry(ctx: DeviceCtx) -> bool:
    for attempt in range(1, TUNNEL_RETRIES + 1):
        if ctx.closing:   # device was unplugged mid-setup → stop spawning tunnels
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

async def _trigger_shortcut(ctx: DeviceCtx, count: int) -> bool:
    """
    Try to open the Shortcuts URL on the iPhone via USB.
    Attempts three methods in order:
      1. xcrun devicectl  — requires iOS 17+ and Xcode 15+
      2. ideviceopenurl  — requires iOS 12-16 and libimobiledevice
      3. (fallback)       — pending queue for WiFi polling
    Returns True if any USB method succeeded.
    """
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
    if shutil.which('ideviceopenurl'):
        rc, err = await _run_cmd(['ideviceopenurl', '-u', ctx.udid, url])
        if rc == 0:
            log.info(f'[{ctx.name}] ✅ Shortcut triggered via ideviceopenurl: +{count} steps')
            return True
        log.warning(f'[{ctx.name}] ideviceopenurl rc={rc}: {err[:120]}')

    # ── Method 3: pymobiledevice3 DVT ProcessControl (no external tools) ─
    # Uses the RSD tunnel that's already open for GPS simulation.
    # Tries to launch the shortcuts:// URL scheme via DVT instruments.
    if ctx.rsd_host:
        try:
            from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
            from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
            from pymobiledevice3.services.dvt.instruments.processcontrol import ProcessControl
            async with RemoteServiceDiscoveryService((ctx.rsd_host, ctx.rsd_port)) as rsd:
                async with DvtProvider(rsd) as dvt:
                    async with ProcessControl(dvt) as pc:
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

async def _trigger_shortcut_bg(ctx: DeviceCtx, count: int):
    """Background task: trigger shortcut; clear pending count if it succeeds."""
    ok = await _trigger_shortcut(ctx, count)
    if ok:
        _pending_steps[ctx.udid] = max(0, _pending_steps.get(ctx.udid, 0) - count)

# ── GPS worker ────────────────────────────────────────────────────────
async def gps_worker(ctx: DeviceCtx):
    from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
    from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
    from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

    last_target = None

    while not ctx.closing:
        if not ctx.rsd_host:
            await asyncio.sleep(2)
            continue
        try:
            log.info(f'[{ctx.name}] Connecting GPS...')
            async with RemoteServiceDiscoveryService((ctx.rsd_host, ctx.rsd_port)) as rsd:
                async with DvtProvider(rsd) as dvt:
                    async with LocationSimulation(dvt) as loc:
                        ctx.state['connected'] = True
                        ctx.state['error']     = None
                        log.info(f'[{ctx.name}] ✅ GPS connected')

                        if last_target:
                            await loc.set(last_target.lat, last_target.lon)
                            log.info(f'[{ctx.name}] ↩ Restored last position')

                        while True:
                            cmd = await ctx._mailbox.wait()
                            if isinstance(cmd, _ClearCmd):
                                await loc.clear()
                                last_target = None
                                ctx.state.update(simulating=False, last_lat=None, last_lon=None)
                                log.info(f'[{ctx.name}] 🔴 GPS cleared')
                            elif isinstance(cmd, _SetCmd):
                                await loc.set(cmd.lat, cmd.lon)
                                last_target = cmd
                                ctx.state['simulating'] = True
                                ctx.state['last_lat']   = cmd.lat
                                ctx.state['last_lon']   = cmd.lon
                                ctx.state['set_count'] += 1
                                log.debug(f'[{ctx.name}] 📍 {cmd.lat:.5f},{cmd.lon:.5f}')
        except asyncio.CancelledError:
            ctx.state['connected'] = False
            raise
        except Exception as e:
            ctx.state['connected']  = False
            ctx.state['simulating'] = False
            ctx.state['error']      = str(e)
            if ctx.closing:
                break
            # If the tunnel subprocess has died (e.g. a quick unplug→replug
            # within the miss window left this ctx in place but its tunnel
            # stale), rebuild it before retrying instead of spinning on a
            # dead RSD endpoint forever.
            tp = ctx.tunnel_proc
            if tp is None or tp.returncode is not None:
                log.warning(f'[{ctx.name}] Tunnel is dead, rebuilding...')
                await terminate_tunnel(ctx)        # clears stale rsd_host
                await asyncio.sleep(2)
                if ctx.closing:
                    break
                if not await start_tunnel_with_retry(ctx):
                    log.error(f'[{ctx.name}] Tunnel rebuild failed, retrying in 5s...')
                    await asyncio.sleep(5)
                continue
            log.error(f'[{ctx.name}] Disconnected: {e}, retrying in 5s...')
            await asyncio.sleep(5)
    log.debug(f'[{ctx.name}] gps_worker exited')

# ── Device scanner ────────────────────────────────────────────────────
async def device_scanner():
    idx_counter = 0
    while True:
        found       = await scan_usb_devices()
        found_udids = {d['udid'] for d in found}

        for udid in list(_devices.keys()):
            if udid not in found_udids:
                # Increment consecutive-miss counter; only remove after threshold.
                # This prevents transient scan failures from triggering a teardown→re-setup loop.
                count = _miss_counts.get(udid, 0) + 1
                _miss_counts[udid] = count
                if count >= MISS_THRESHOLD:
                    ctx = _devices.pop(udid)
                    _miss_counts.pop(udid, None)
                    _pending_steps.pop(udid, None)
                    log.info(f'Device removed: {ctx.name}  (missed {count} scans)')
                    asyncio.create_task(teardown_device(ctx))
                else:
                    log.debug(f'[{_devices[udid].name}] USB miss {count}/{MISS_THRESHOLD}, waiting...')
            else:
                _miss_counts.pop(udid, None)  # reset on successful find

        for info in found:
            udid = info['udid']
            if udid not in _devices:
                ctx = DeviceCtx(idx_counter, udid, info['name'], info['ios'])
                idx_counter += 1
                _devices[udid] = ctx
                log.info(f'Device found: {ctx.name}  ({udid[-8:]})')
                ctx.setup_task = asyncio.create_task(setup_device(ctx))

        await asyncio.sleep(SCAN_SEC)

async def setup_device(ctx: DeviceCtx):
    await asyncio.sleep(DEVICE_BOOT_WAIT)
    if ctx.closing:
        return
    ok = await start_tunnel_with_retry(ctx)
    if ctx.closing:
        return
    if ok:
        ctx.worker_task = asyncio.create_task(gps_worker(ctx))
    else:
        ctx.state['error'] = 'Tunnel failed'

async def _cancel_task(task):
    if task and not task.done():
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

async def route_set(request):
    ctx = _get_ctx(request)
    if not ctx:
        return web.json_response({'ok': False, 'error': 'device not found'}, status=404)
    if not ctx.state['connected']:
        return web.json_response({'ok': False, 'error': 'GPS not connected'}, status=503)
    try:
        data = await request.json()
        lat, lon = float(data['lat']), float(data['lon'])
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=400)
    ctx._mailbox.post(_SetCmd(lat, lon))
    return web.json_response({'ok': True, 'lat': lat, 'lon': lon})

async def route_clear(request):
    ctx = _get_ctx(request)
    if not ctx:
        return web.json_response({'ok': False, 'error': 'device not found'}, status=404)
    if not ctx.state['connected']:
        return web.json_response({'ok': False, 'error': 'GPS not connected'}, status=503)
    ctx._mailbox.post(_ClearCmd())
    return web.json_response({'ok': True})

async def route_device_status(request):
    ctx = _get_ctx(request)
    if not ctx:
        return web.json_response({'ok': False, 'error': 'device not found'}, status=404)
    return web.json_response(ctx.to_dict())

# ── Broadcast endpoints ───────────────────────────────────────────────
# Fan out a single command to every currently-connected device. Each
# device has its own mailbox + GPS worker, so posting is non-blocking
# and the iPhones move in parallel.
async def route_set_all(request):
    try:
        data = await request.json()
        lat, lon = float(data['lat']), float(data['lon'])
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=400)

    targets = [c for c in _devices.values() if c.state['connected']]
    if not targets:
        return web.json_response(
            {'ok': False, 'error': 'no connected devices'}, status=503,
        )
    for ctx in targets:
        ctx._mailbox.post(_SetCmd(lat, lon))
    return web.json_response({
        'ok': True, 'lat': lat, 'lon': lon,
        'count': len(targets),
        'idxs':  [c.idx for c in targets],
    })

async def route_clear_all(request):
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
    try:
        data  = await request.json()
        count = max(1, int(data['count']))
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=400)
    ctx.state['steps'] += count
    _pending_steps[ctx.udid] = _pending_steps.get(ctx.udid, 0) + count
    log.info(f'[{ctx.name}] +{count} steps  (total={ctx.state["steps"]} pending={_pending_steps[ctx.udid]})')
    # Fire-and-forget: trigger the iPhone Shortcut in the background.
    # If xcrun succeeds the pending counter is cleared automatically.
    asyncio.create_task(_trigger_shortcut_bg(ctx, count))
    return web.json_response({
        'ok': True, 'steps': ctx.state['steps'], 'added': count,
        'pending': _pending_steps.get(ctx.udid, 0),
    })

async def route_steps_remove(request):
    ctx = _get_ctx(request)
    if not ctx:
        return web.json_response({'ok': False, 'error': 'device not found'}, status=404)
    try:
        data  = await request.json()
        count = max(1, int(data['count']))
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=400)
    removed = min(count, ctx.state['steps'])
    # ── TODO: call pymobiledevice3 / HealthKit here to remove `removed` steps ──
    ctx.state['steps'] -= removed
    log.info(f'[{ctx.name}] -{removed} steps  (total {ctx.state["steps"]})')
    return web.json_response({'ok': True, 'steps': ctx.state['steps'], 'removed': removed})

async def route_steps_reset(request):
    ctx = _get_ctx(request)
    if not ctx:
        return web.json_response({'ok': False, 'error': 'device not found'}, status=404)
    old = ctx.state['steps']
    ctx.state['steps'] = 0
    log.info(f'[{ctx.name}] steps reset (was {old})')
    return web.json_response({'ok': True, 'steps': 0})

# Broadcast step operations ─────────────────────────────────────────
async def route_steps_add_all(request):
    try:
        data  = await request.json()
        count = max(1, int(data['count']))
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=400)
    targets = list(_devices.values())
    for ctx in targets:
        ctx.state['steps'] += count
        _pending_steps[ctx.udid] = _pending_steps.get(ctx.udid, 0) + count
        asyncio.create_task(_trigger_shortcut_bg(ctx, count))
    return web.json_response({
        'ok': True, 'added': count, 'count': len(targets),
        'device_steps': {str(c.idx): c.state['steps'] for c in targets},
    })

async def route_steps_remove_all(request):
    try:
        data  = await request.json()
        count = max(1, int(data['count']))
    except Exception as e:
        return web.json_response({'ok': False, 'error': str(e)}, status=400)
    targets = list(_devices.values())
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
            return web.json_response({'ok': True, 'steps': _pending_steps.get(ctx.udid, 0), 'idx': idx})
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
            confirmed = min(count, _pending_steps.get(ctx.udid, 0))
            _pending_steps[ctx.udid] = max(0, _pending_steps.get(ctx.udid, 0) - confirmed)
            log.info(f'[{ctx.name}] ✅ Health ack: {confirmed} steps confirmed (remaining {_pending_steps[ctx.udid]})')
            return web.json_response({'ok': True, 'confirmed': confirmed,
                                      'remaining': _pending_steps.get(ctx.udid, 0)})
    return web.json_response({'ok': False, 'error': 'device not found'}, status=404)

@middleware
async def cors_middleware(request, handler):
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
async def main():
    asyncio.create_task(device_scanner())

    app = web.Application(middlewares=[cors_middleware])
    app.router.add_get ('/devices',             route_devices)
    app.router.add_post('/device/{idx}/set',    route_set)
    app.router.add_post('/device/{idx}/clear',  route_clear)
    app.router.add_get ('/device/{idx}/status', route_device_status)
    # broadcast to all connected devices
    app.router.add_post('/devices/set',         route_set_all)
    app.router.add_post('/devices/clear',       route_clear_all)
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
    for path in ['/device/{idx}/set', '/device/{idx}/clear',
                 '/devices/set', '/devices/clear'] + _step_paths:
        app.router.add_route('OPTIONS', path, lambda r: web.Response())

    # Kill any previous instance occupying the same port
    _free_port(META_PORT)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, META_HOST, META_PORT,
                      reuse_address=True, reuse_port=True).start()

    log.info(f'🚀 GPS Launcher  {META_HOST}:{META_PORT}  log={_LOG_FILE}')
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
