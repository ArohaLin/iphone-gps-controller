"""USB 生命週期隔離測試：禁止啟動外部程序、連手機或送出系統訊號。"""
import asyncio
import importlib.util
import logging
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch


class Resource:
    def __init__(self):
        self.close = AsyncMock()


class LifecycleHarness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = []
        self.mock('subprocess.run', side_effect=AssertionError('禁止外部程序'))
        self.mock('shutil.which', return_value=None)
        self.mock('logging.handlers.RotatingFileHandler', return_value=logging.NullHandler())
        self.mock('logging.basicConfig')
        self.mock('sys.argv', ['isolated-gps-test'])
        self.mock('asyncio.create_subprocess_exec', side_effect=AssertionError('禁止真 tunnel'))
        self.mock('os.kill', side_effect=AssertionError('禁止送出訊號'))
        self.mock('os.killpg', side_effect=AssertionError('禁止送出訊號'))
        spec = importlib.util.spec_from_file_location('isolated_launcher', Path(__file__).with_name('gps_launcher.py'))
        self.m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.m)
        preferences_dir = tempfile.TemporaryDirectory()
        self.addCleanup(preferences_dir.cleanup)
        self.m._preferences = self.m.DevicePreferences(Path(preferences_dir.name) / 'device_preferences.json')
        self.tasks = []
        self.present = set('ABC')
        self.resources = []
        self.sets = []
        self.fail_stage = None
        self.entered = asyncio.Event()
        self.rsd_values = {}
        owner = self

        class RSD:
            def __init__(self, address):
                self.udid = address[0]
                self.lockdown = Resource()
                self.service = Resource()
                self.all_values = owner.rsd_values
                owner.resources.append(self)

            async def __aenter__(self):
                if owner.fail_stage == 'rsd':
                    raise OSError('RSD 失敗')
                return self

            async def create_service_connection(self, *args, **kwargs):
                return Resource()

        class DVT:
            def __init__(self, rsd):
                self.rsd = rsd
                self._dtx = None
                self.raw = None
                owner.resources.append(self)

            async def _open_dtx_connection(self, *args, **kwargs):
                self.raw = await self.rsd.create_service_connection(123)
                if owner.fail_stage == 'raw':
                    owner.entered.set()
                    await asyncio.Event().wait()
                return SimpleNamespace(aclose=AsyncMock())

            async def __aenter__(self):
                dtx = await self._open_dtx_connection()
                if owner.fail_stage == 'handshake':
                    owner.entered.set()
                    await asyncio.Event().wait()
                self._dtx = dtx
                return self

            async def close(self):
                if self._dtx:
                    await self._dtx.aclose()

        class LOC:
            def __init__(self, dvt):
                self.udid = dvt.rsd.udid
                self.close = AsyncMock()
                owner.resources.append(self)

            async def __aenter__(self):
                if owner.fail_stage == 'loc':
                    raise OSError('定位握手失敗')
                return self

            async def set(self, lat, lon):
                if owner.fail_stage == 'set':
                    raise OSError('定位傳送失敗')
                owner.sets.append((self.udid, lat, lon))

            async def clear(self):
                pass

        self.mock('pymobiledevice3.remote.remote_service_discovery.RemoteServiceDiscoveryService', RSD)
        self.mock('pymobiledevice3.services.dvt.instruments.dvt_provider.DvtProvider', DVT)
        self.mock('pymobiledevice3.services.dvt.instruments.location_simulation.LocationSimulation', LOC)
        self.list_usb = self.mock('pymobiledevice3.usbmux.list_devices', AsyncMock(side_effect=lambda: [SimpleNamespace(serial=u, connection_type='USB') for u in self.present]))
        from pymobiledevice3.lockdown import create_using_usbmux
        self.real_lockdown_factory = create_using_usbmux
        self.factory = self.mock('pymobiledevice3.lockdown.create_using_usbmux', AsyncMock(side_effect=AssertionError('未設定假 lockdown')))
        self.terminate = self.mock_object(self.m, 'terminate_tunnel', AsyncMock(side_effect=self.end_tunnel))
        self.start = self.mock_object(self.m, 'start_tunnel_with_retry', AsyncMock(side_effect=self.start_tunnel))

    def mock(self, target, *args, **kwargs):
        p = patch(target, *args, **kwargs)
        self.patches.append(p)
        return p.start()

    def mock_object(self, target, name, *args, **kwargs):
        p = patch.object(target, name, *args, **kwargs)
        self.patches.append(p)
        return p.start()

    async def start_tunnel(self, ctx):
        if ctx.connection_type == 'usb':
            self.assertIn(ctx.udid, self.present)
        ctx.rsd_host, ctx.rsd_port = ctx.udid, 123
        return True

    async def end_tunnel(self, ctx):
        ctx.rsd_host = ctx.rsd_port = None

    async def until(self, predicate):
        async with asyncio.timeout(2):
            while not predicate():
                await asyncio.sleep(0)

    async def asyncTearDown(self):
        await self.m._cruise.stop()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for ctx in list(self.m._devices.values()):
            await self.m.teardown_device(ctx)
        for p in reversed(self.patches):
            p.stop()

    async def device(self, udid='A', idx=0, connection_type='usb'):
        ctx = self.m.DeviceCtx(idx, udid, udid, '18', connection_type)
        self.m._devices[udid] = ctx
        ctx.worker_task = asyncio.create_task(self.m.gps_worker(ctx))
        self.tasks.append(ctx.worker_task)
        await self.until(lambda: ctx.state['connected'])
        return ctx

    async def quiesce(self, rounds=20):
        """讓已排程的工作全部跑完；巡航 tick_interval 設大時之後不會再自動送位置。"""
        for _ in range(rounds):
            await asyncio.sleep(0)

    def usb_listing(self):
        return [{'udid': u, 'name': u, 'ios': '18'} for u in sorted(self.present)]

    async def unplug(self, udid):
        """模擬 USB 拔線：掃描不到 → teardown（ctx.closing）。"""
        self.present.discard(udid)
        await self.m._apply_scan(self.usb_listing(), 'usb')

    async def replug(self, udid):
        """模擬 USB 插回：掃描建立新 ctx（新 idx）並跑真正的 gps_worker。"""
        self.present.add(udid)
        async def setup(ctx):
            ctx.worker_task = asyncio.create_task(self.m.gps_worker(ctx))
            self.tasks.append(ctx.worker_task)
        with patch.object(self.m, 'setup_device', setup):
            await self.m._apply_scan(self.usb_listing(), 'usb')
        fresh = self.m._devices[udid]
        await self.until(lambda: fresh.state['connected'])
        return fresh

class UsbLifecycleTests(LifecycleHarness):
    async def test_scan_never_opens_lockdown_and_does_not_fallback_for_empty_usb(self):
        self.present = {'A'}
        self.assertEqual(await self.m.scan_usb_devices(), [{'udid': 'A', 'name': 'iPhone (A)', 'ios': '?'}])
        self.factory.assert_not_called()
        self.present.clear()
        self.assertEqual(await self.m.scan_usb_devices(), [])

    async def test_scan_cached_device_skips_lockdown(self):
        self.present = {'A'}
        self.m._devices['A'] = self.m.DeviceCtx(0, 'A', '快取', '18')
        self.assertEqual(await self.m.scan_usb_devices(), [{'udid': 'A', 'name': '快取', 'ios': '18'}])
        self.factory.assert_not_called()

    async def test_scan_has_no_real_factory_cancellation_window(self):
        # 真實 9.9.1 工廠在 create_mux 取消時漏關已取得的 transport。
        # 掃描不再呼叫工廠，因此不會取得這個無法管理的 transport。
        from pymobiledevice3 import lockdown
        raw = Resource()
        async def blocked_mux(**kwargs):
            await asyncio.Event().wait()
        with patch.object(lockdown.ServiceConnection, 'create_using_usbmux', AsyncMock(return_value=raw)) as acquire, \
                patch.object(lockdown.usbmux, 'create_mux', AsyncMock(side_effect=blocked_mux)), \
                patch.object(lockdown, 'create_using_usbmux', AsyncMock(wraps=self.real_lockdown_factory)) as real_factory:
            self.assertEqual(len(await asyncio.wait_for(self.m.scan_usb_devices(), 1)), 3)
            real_factory.assert_not_called()
        self.factory.assert_not_called()
        acquire.assert_not_called()
        raw.close.assert_not_called()

    async def test_metadata_comes_from_managed_rsd_only(self):
        self.rsd_values = {'DeviceName': '測試手機', 'ProductVersion': '18.7'}
        ctx = await self.device()
        self.assertEqual((ctx.name, ctx.ios), ('測試手機', '18.7'))
        self.factory.assert_not_called()
        await self.m.teardown_device(ctx)
        self.resources[0].lockdown.close.assert_awaited_once()

    async def test_missing_rsd_metadata_keeps_existing_name_and_ios(self):
        self.rsd_values = None
        ctx = await self.device()
        self.assertEqual((ctx.name, ctx.ios), ('A', '18'))
        self.factory.assert_not_called()

    async def test_scan_failure_is_unknown_not_unplug(self):
        self.list_usb.side_effect = OSError('暫時失敗')
        self.assertIsNone(await self.m.scan_usb_devices())
        self.assertIsNone(await self.m._usb_present('A'))

    async def test_cli_fallback_only_lists_usb_without_lockdown(self):
        self.list_usb.side_effect = OSError('API 暫時失敗')
        proc = SimpleNamespace(returncode=0, communicate=AsyncMock(return_value=(b'["A"]', b'')))
        with patch('asyncio.create_subprocess_exec', AsyncMock(return_value=proc)) as spawn:
            self.assertEqual((await self.m.scan_usb_devices())[0]['udid'], 'A')
            self.assertEqual(spawn.call_args.args[-2:], ('--usb', '--simple'))
            self.factory.assert_not_awaited()
            proc.returncode = 1
            self.assertIsNone(await self.m.scan_usb_devices())

    async def test_cancel_cli_scan_reaps_only_its_own_fake_process(self):
        self.list_usb.side_effect = OSError('API 暫時失敗')
        entered = asyncio.Event()
        async def communicate():
            entered.set()
            await asyncio.Event().wait()
        proc = SimpleNamespace(returncode=None, communicate=communicate, kill=Mock(), wait=AsyncMock())
        with patch('asyncio.create_subprocess_exec', AsyncMock(return_value=proc)):
            task = asyncio.create_task(self.m.scan_usb_devices())
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        proc.kill.assert_called_once()
        proc.wait.assert_awaited_once()

    async def test_idle_and_simulating_cancel_release_all_owned_connections(self):
        for simulate in (False, True):
            with self.subTest(simulate=simulate):
                self.resources.clear()
                ctx = await self.device()
                if simulate:
                    ctx._mailbox.post(self.m._SetCmd(25, 121))
                    await self.until(lambda: ctx.state['simulating'])
                await self.m.teardown_device(ctx)
                rsd, dvt, loc = self.resources
                rsd.lockdown.close.assert_awaited_once()
                rsd.service.close.assert_awaited_once()
                dvt._dtx.aclose.assert_awaited_once()
                dvt.raw.close.assert_awaited_once()
                loc.close.assert_awaited_once()
                self.assertFalse(ctx.state['connected'])
                self.assertFalse(ctx.state['simulating'])
                self.assertIsNone(ctx._mailbox._cmd)

    async def test_cancel_during_raw_connection_or_dtx_handshake_releases_resources(self):
        for stage in ('raw', 'handshake'):
            with self.subTest(stage=stage):
                self.resources.clear()
                self.entered.clear()
                self.fail_stage = stage
                ctx = self.m.DeviceCtx(0, 'A', 'A', '18')
                ctx.worker_task = asyncio.create_task(self.m.gps_worker(ctx))
                self.tasks.append(ctx.worker_task)
                await asyncio.wait_for(self.entered.wait(), 2)
                await self.m.teardown_device(ctx)
                rsd, dvt = self.resources
                rsd.lockdown.close.assert_awaited_once()
                rsd.service.close.assert_awaited_once()
                dvt.raw.close.assert_awaited_once()
                if stage == 'handshake':
                    dvt._dtx.aclose.assert_awaited_once()

    async def test_lockdown_close_error_does_not_skip_rsd_service(self):
        rsd = SimpleNamespace(lockdown=Resource(), service=Resource())
        rsd.lockdown.close.side_effect = OSError('已拔線')
        await self.m._close_rsd(rsd)
        rsd.service.close.assert_awaited_once()

    async def test_absent_device_never_starts_tunnel_or_rsd(self):
        self.present.clear()
        ctx = self.m.DeviceCtx(0, 'A', 'A', '18')
        await self.m.gps_worker(ctx)
        self.start.assert_not_awaited()
        self.assertEqual(self.resources, [])
        self.assertTrue(ctx.closing)

    async def test_unknown_usb_uses_capped_backoff_without_connecting(self):
        self.list_usb.side_effect = OSError('掃描暫時失敗')
        ctx = self.m.DeviceCtx(0, 'A', 'A', '18')
        delays = []
        async def sleep(delay):
            delays.append(delay)
            if len(delays) == 7:
                ctx.closing = True
        with patch.object(self.m.asyncio, 'sleep', sleep):
            await self.m.gps_worker(ctx)
        self.assertEqual(delays, [2, 4, 8, 16, 30, 30, 30])
        self.start.assert_not_awaited()

    async def test_partial_rsd_and_location_entry_failure_closes_owned_resources(self):
        for stage in ('rsd', 'loc'):
            with self.subTest(stage=stage):
                self.resources.clear()
                self.fail_stage = stage
                self.list_usb.side_effect = [
                    [SimpleNamespace(serial='A', connection_type='USB')], []]
                ctx = self.m.DeviceCtx(0, 'A', 'A', '18')
                await self.m.gps_worker(ctx)
                rsd = self.resources[0]
                rsd.lockdown.close.assert_awaited_once()
                rsd.service.close.assert_awaited_once()
                if stage == 'loc':
                    dvt, loc = self.resources[1:]
                    dvt._dtx.aclose.assert_awaited_once()
                    loc.close.assert_awaited_once()
                self.assertTrue(ctx.closing)

    async def test_transient_send_error_recovers_without_old_position_or_cruise(self):
        ctx = await self.device()
        ctx._mailbox.post(self.m._SetCmd(25, 121))
        await self.until(lambda: ctx.state['simulating'])
        self.m._cruise.target_udids = ['A']
        self.m._cruise.phase = 'running'
        self.fail_stage = 'set'
        original_sleep = asyncio.sleep
        delays = []
        async def recover(delay):
            if delay:
                delays.append(delay)
                self.fail_stage = None
            await original_sleep(0)
        with patch.object(self.m.asyncio, 'sleep', recover):
            ctx._mailbox.post(self.m._SetCmd(26, 122))
            await self.until(lambda: len(self.resources) == 6 and ctx.state['connected'])
        self.assertEqual(delays, [2])
        self.assertFalse(ctx.state['simulating'])
        self.assertEqual(self.sets, [('A', 25, 121)])
        self.assertEqual(self.m._cruise.phase, 'stopped')
        self.assertEqual(self.m._cruise.target_udids, [])
        ctx._mailbox.post(self.m._SetCmd(24, 120))
        await self.until(lambda: ctx.state['simulating'])
        self.assertEqual(self.sets[-1], ('A', 24, 120))

    async def test_unknown_scan_keeps_all_three_connections_and_cruise(self):
        contexts = [await self.device(u, i) for i, u in enumerate('ABC')]
        await self.m._cruise.start('all', [{'lat': 25, 'lon': 121}, {'lat': 26, 'lon': 122}], 5)
        scanned = asyncio.Event()
        async def scan():
            scanned.set()
            return None
        with patch.object(self.m, 'scan_usb_devices', scan):
            task = asyncio.create_task(self.m.device_scanner())
            self.tasks.append(task)
            await scanned.wait()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(all(c.state['connected'] and not c.closing for c in contexts))
        self.assertEqual(self.m._cruise.phase, 'running')
        self.assertEqual(set(self.m._cruise.target_udids), set('ABC'))
        self.terminate.assert_not_awaited()

    async def test_three_devices_unplug_and_reinsert_matrix(self):
        # 不靠計時 tick 推進位置，才能精確比對「重插後立即收到的目前巡航位置」。
        self.m._cruise.tick_interval = 3600
        for cruise in (False, True):
            for removed in ('A', 'BC', 'ABC'):
                for reverse in (False, True):
                    with self.subTest(cruise=cruise, removed=removed, reverse=reverse):
                        await self.m._cruise.stop()
                        for old in list(self.m._devices.values()):
                            await self.m.teardown_device(old)
                        self.m._devices.clear()
                        self.m._miss_counts.clear()
                        self.present = set('ABC')
                        self.sets.clear()
                        contexts = {u: await self.device(u, i) for i, u in enumerate('ABC')}
                        if cruise:
                            await self.m._cruise.start('all', [{'lat': 25, 'lon': 121}, {'lat': 26, 'lon': 122}], 5)
                        else:
                            for ctx in contexts.values():
                                ctx._mailbox.post(self.m._SetCmd(25, 121))
                        await self.until(lambda: len(self.sets) == 3)
                        queue = asyncio.Queue()
                        async def scan():
                            return await queue.get()
                        async def setup(ctx):
                            ctx.worker_task = asyncio.create_task(self.m.gps_worker(ctx))
                            self.tasks.append(ctx.worker_task)
                        with patch.object(self.m, 'scan_usb_devices', scan), patch.object(self.m, 'setup_device', setup), patch.object(self.m, 'SCAN_SEC', 0):
                            scanner = asyncio.create_task(self.m.device_scanner())
                            self.tasks.append(scanner)
                            for udid in removed:
                                self.present.remove(udid)
                            def listing():
                                return [{'udid': u, 'name': u, 'ios': '18'} for u in sorted(self.present)]
                            await queue.put(listing())
                            await self.until(lambda: all(contexts[u].closing and contexts[u].worker_task is None for u in removed))
                            for u in set('ABC') - set(removed):
                                self.assertTrue(contexts[u].state['connected'])
                                self.assertTrue(contexts[u].state['simulating'])
                            if cruise:
                                self.assertEqual(set(self.m._cruise.target_udids), set('ABC') - set(removed))
                                self.assertEqual(self.m._cruise.phase, 'running' if len(removed) < 3 else 'stopped')
                            # 巡航仍在進行時，重插的手機（新 ctx、新 idx）自動加回，並立即
                            # 收到『目前』巡航位置；全部拔光時巡航已停止，重插不會帶回巡航。
                            rejoins = cruise and len(removed) < 3
                            baseline = len(self.sets)
                            for u in removed[::-1] if reverse else removed:
                                self.present.add(u)
                                await queue.put(listing())
                                await self.until(lambda: u in self.m._devices and self.m._devices[u] is not contexts[u] and self.m._devices[u].state['connected'])
                                fresh = self.m._devices[u]
                                self.assertNotEqual(fresh.idx, contexts[u].idx)
                                if rejoins:
                                    await self.until(lambda: fresh.state['simulating'])
                                    self.assertIn(u, self.m._cruise.target_udids)
                                    position = self.m._cruise.position
                                    self.assertIn((u, position['lat'], position['lon']), self.sets[baseline:])
                                else:
                                    self.assertFalse(fresh.state['simulating'])
                                    self.assertIsNone(fresh._mailbox._cmd)
                                    self.assertNotIn(u, self.m._cruise.target_udids if cruise else [])
                            if rejoins:
                                # 只送目前位置一次，不重播舊指令。
                                self.assertEqual(sorted(u for u, _, _ in self.sets[baseline:]), sorted(removed))
                                self.assertEqual(set(self.m._cruise.target_udids), set('ABC'))
                                self.assertEqual(self.m._cruise.phase, 'running')
                            else:
                                self.assertEqual(len(self.sets), baseline)
                            if cruise and self.m._cruise.target_udids:
                                baseline = len(self.sets)
                                self.m._cruise._post_position()
                                await self.until(lambda: len(self.sets) >= baseline + 3)
                                self.assertEqual({u for u, _, _ in self.sets[baseline:]}, set('ABC'))
                            for u in removed:
                                self.m._devices[u]._mailbox.post(self.m._SetCmd(24, 120))
                            await self.until(lambda: all(self.m._devices[u].state['simulating'] for u in removed))
                            scanner.cancel()
                            await asyncio.gather(scanner, return_exceptions=True)


ROUTE = [{'lat': 25, 'lon': 121}, {'lat': 26, 'lon': 122}]


class CruiseRejoinUsbTests(LifecycleHarness):
    """斷線重接自動跟上進行中的巡航（USB 路徑）。"""
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.m._cruise.tick_interval = 3600  # 位置只在 start/pause/rejoin 時送出，便於精確比對
        self.present = {'A', 'B'}

    async def test_a_transient_usb_error_rejoins_all_cruise_with_current_position(self):
        a = await self.device('A', 0)
        b = await self.device('B', 1)
        await self.m._cruise.start('all', ROUTE, 5)
        await self.quiesce()
        baseline = len(self.sets)
        original_sleep = asyncio.sleep
        delays = []
        async def recover(delay):
            if delay:
                delays.append(delay)
                self.fail_stage = None
            await original_sleep(0)
        self.fail_stage = 'set'
        with patch.object(self.m.asyncio, 'sleep', recover):
            a._mailbox.post(self.m._SetCmd(26, 122))  # 只有 A 送出失敗 → USB 暫時錯誤
            await self.until(lambda: len(self.resources) == 9 and a.state['connected']
                             and a.state['simulating'])
        self.assertEqual(delays, [2])
        self.assertIs(self.m._devices['A'], a)  # 同一個 ctx 重試，不是拔插
        self.assertEqual(self.m._cruise.phase, 'running')
        self.assertEqual(sorted(self.m._cruise.target_udids), ['A', 'B'])
        position = self.m._cruise.position
        # 只收到一筆『目前』巡航位置，失敗的舊指令 (26,122) 不重播。
        self.assertEqual(self.sets[baseline:], [('A', position['lat'], position['lon'])])
        self.assertTrue(b.state['connected'])

    async def test_b_unplug_replug_new_ctx_rejoins_running_or_paused_cruise(self):
        for phase in ('running', 'paused'):
            with self.subTest(phase=phase):
                await self.m._cruise.stop()
                for old in list(self.m._devices.values()):
                    await self.m.teardown_device(old)
                self.m._devices.clear()
                self.m._miss_counts.clear()
                self.present = {'A', 'B'}
                a = await self.device('A', 0)
                await self.device('B', 1)
                await self.m._cruise.start('all', ROUTE, 5)
                if phase == 'paused':
                    await self.m._cruise.pause()
                await self.quiesce()
                await self.unplug('A')
                self.assertTrue(a.closing)
                self.assertEqual(self.m._cruise.target_udids, ['B'])
                self.assertEqual(self.m._cruise.phase, phase)
                baseline = len(self.sets)
                fresh = await self.replug('A')
                self.assertIsNot(fresh, a)
                self.assertNotEqual(fresh.idx, a.idx)
                await self.until(lambda: fresh.state['simulating'])
                position = self.m._cruise.position
                self.assertEqual(self.sets[baseline:], [('A', position['lat'], position['lon'])])
                self.assertEqual(sorted(self.m._cruise.target_udids), ['A', 'B'])
                self.assertEqual(self.m._cruise.phase, phase)  # 暫停中仍維持暫停
                self.assertEqual(self.m._cruise.status()['deviceIdx'], 'all')

    async def test_f_sync_all_cruise_late_device_joins_with_current_position(self):
        # 同步全部：巡航開始後才連上的手機，也自動加入並送出目前巡航位置（不另加輪詢）。
        for phase in ('running', 'paused'):
            with self.subTest(phase=phase):
                await self.m._cruise.stop()
                for old in list(self.m._devices.values()):
                    await self.m.teardown_device(old)
                self.m._devices.clear()
                self.m._miss_counts.clear()
                self.present = {'A', 'C'}
                await self.device('A', 0)
                await self.m._cruise.start('all', ROUTE, 5)
                if phase == 'paused':
                    await self.m._cruise.pause()
                await self.quiesce()
                self.assertEqual(self.m._cruise.target_udids, ['A'])
                baseline = len(self.sets)
                late = await self.device('C', 2)
                await self.until(lambda: late.state['simulating'])
                position = self.m._cruise.position
                self.assertIn(('C', position['lat'], position['lon']), self.sets[baseline:])
                self.assertEqual(sorted(self.m._cruise.target_udids), ['A', 'C'])
                self.assertEqual(self.m._cruise.phase, phase)
                self.assertEqual(self.m._cruise.status()['deviceIdx'], 'all')

    async def test_f_single_device_cruise_late_device_does_not_join(self):
        # 單機巡航：只控制選定的那支，晚連上的其他手機不加入。
        self.present = {'A', 'B'}
        await self.device('A', 0)
        await self.m._cruise.start(0, ROUTE, 5)
        await self.quiesce()
        late = await self.device('B', 1)
        await self.quiesce()
        self.assertEqual(self.m._cruise.target_udids, ['A'])
        self.assertFalse(late.state['simulating'])
        self.assertEqual(self.m._cruise.status()['deviceIdx'], 0)

    async def test_e_single_device_cruise_replug_stays_stopped(self):
        # 單機巡航只有一支手機：拔線即「全部斷線」→ 巡航自動停止（保留既有行為），
        # 所以重插不會帶回巡航，deviceIdx 也不必更新。
        await self.device('A', 0)
        b = await self.device('B', 1)
        await self.m._cruise.start(0, ROUTE, 5)
        self.assertEqual(self.m._cruise.status()['deviceIdx'], 0)
        await self.quiesce()
        await self.unplug('A')
        self.assertEqual(self.m._cruise.status()['state'], 'stopped')
        baseline = len(self.sets)
        fresh = await self.replug('A')
        await self.quiesce()
        self.assertNotEqual(fresh.idx, 0)
        status = self.m._cruise.status()
        self.assertEqual(status['state'], 'stopped')
        self.assertEqual(status['target_udids'], [])
        self.assertEqual(self.sets[baseline:], [])
        self.assertFalse(fresh.state['simulating'])
        self.assertFalse(b.state['simulating'])

    async def test_e_single_device_rejoin_updates_device_idx_white_box(self):
        # 白箱：直接驗證 rejoin 對單機巡航的 idx 同步（實務上會先因全部斷線而停止）。
        a = await self.device('A', 0)
        await self.m._cruise.start(0, ROUTE, 5)
        await self.quiesce()
        self.m._cruise.target_udids.remove('A')  # 模擬已暫時移出、巡航尚未停止
        fresh = self.m.DeviceCtx(7, 'A', 'A', '18')
        fresh.state['connected'] = True
        self.m._devices['A'] = fresh
        self.assertFalse(await self.m._cruise.rejoin(a))      # 舊 ctx 已被取代，不可加回
        self.assertEqual(self.m._cruise.target_udids, [])
        self.assertTrue(await self.m._cruise.rejoin(fresh))
        self.assertEqual(self.m._cruise.status()['deviceIdx'], 7)
        self.assertEqual(self.m._cruise.target_udids, ['A'])
        cmd = fresh._mailbox._cmd
        self.assertEqual((cmd.lat, cmd.lon), (self.m._cruise.position['lat'], self.m._cruise.position['lon']))
        self.assertFalse(await self.m._cruise.rejoin(fresh))  # 已在名單內不重複加入
        self.assertEqual(self.m._cruise.target_udids, ['A'])


if __name__ == '__main__':
    unittest.main()
