"""裝置開關隔離測試：設定一律使用 tempdir，不觸碰現場手機。"""
import asyncio
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_usb_lifecycle import LifecycleHarness


class DeviceEnabledTests(LifecycleHarness):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'device_preferences.json'
        self.m._preferences = self.m.DevicePreferences(self.path)
        self.events = []
        self.clear_error = None
        self.block_set = False
        self.set_entered = asyncio.Event()
        self.release_set = asyncio.Event()
        self.block_clear = False
        self.clear_entered = asyncio.Event()
        self.release_clear = asyncio.Event()
        owner = self

        class LOC:
            def __init__(self, dvt):
                self.udid = dvt.rsd.udid
                self.close = AsyncMock()
            async def __aenter__(self):
                return self
            async def set(self, lat, lon):
                owner.events.append(('set-start', self.udid))
                owner.set_entered.set()
                if owner.block_set:
                    await owner.release_set.wait()
                owner.sets.append((self.udid, lat, lon))
                owner.events.append(('set-end', self.udid))
            async def clear(self):
                owner.events.append(('clear', self.udid))
                owner.clear_entered.set()
                if owner.block_clear:
                    await owner.release_clear.wait()
                if owner.clear_error:
                    raise owner.clear_error
        self.mock('pymobiledevice3.services.dvt.instruments.location_simulation.LocationSimulation', LOC)
        self.mock_object(self.m, '_wifi_health', AsyncMock())
        self.trigger = self.mock_object(self.m, '_trigger_shortcut', AsyncMock(return_value=False))

    async def asyncTearDown(self):
        self.release_set.set()
        self.release_clear.set()
        for ctx in list(self.m._devices.values()):
            for task in list(ctx._enabled_tasks):
                await task
        await super().asyncTearDown()
        self.temp.cleanup()

    def request(self, idx=0, data=None, query=None):
        return SimpleNamespace(match_info={'idx': str(idx)}, json=AsyncMock(return_value=data),
            rel_url=SimpleNamespace(query=query or {'token': self.m.STEPS_TOKEN, 'idx': str(idx)}))

    def body(self, response):
        return json.loads(response.text)

    async def enabled(self, value, idx=0):
        return await self.m.route_device_enabled(self.request(idx, {'enabled': value}))

    async def test_unknown_default_and_strict_api_boolean(self):
        ctx = await self.device()
        self.assertTrue(ctx.to_dict()['enabled'])
        for value in (0, 1, 'false', None, [], {}):
            self.assertEqual((await self.enabled(value)).status, 400)
        self.assertFalse(self.path.exists())
        self.assertTrue(ctx.enabled)

    async def test_real_persistence_new_module_new_index_and_interface(self):
        await self.device()
        response = await self.enabled(False)
        self.assertEqual(self.body(response), {'ok': True, 'enabled': False})
        data = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(data, {'version': 1, 'devices': {'A': False}})
        copied = Path(self.temp.name) / 'gps_launcher.py'
        shutil.copy2(Path(self.m.__file__), copied)
        spec = importlib.util.spec_from_file_location('fresh_enabled_launcher', copied)
        fresh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fresh)
        self.assertFalse(fresh.DeviceCtx(19, 'A', '新名稱', '18', 'wifi').enabled)
        self.assertTrue(fresh.DeviceCtx(20, 'B', 'B', '18').enabled)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    async def test_save_permission_failure_preserves_state_and_original_file(self):
        ctx = await self.device()
        self.m._preferences.save('A', True)
        before = self.path.read_bytes()
        with patch.object(self.m.os, 'replace', side_effect=PermissionError('不可寫入')):
            response = await self.enabled(False)
        self.assertEqual(response.status, 500)
        self.assertTrue(ctx.enabled)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(self.events, [])
        self.assertEqual(list(self.path.parent.glob('.device_preferences.*.tmp')), [])

    async def test_corrupt_preferences_fail_closed_without_overwrite(self):
        for raw in ('{壞檔', '{"version":1,"devices":{"A":0}}',
                    '{"version":true,"devices":{}}', '{"version":2,"devices":{}}'):
            with self.subTest(raw=raw):
                self.path.write_text(raw, encoding='utf-8')
                self.m._preferences = self.m.DevicePreferences(self.path)
                ctx = self.m.DeviceCtx(0, 'A', 'A', '18')
                self.m._devices['A'] = ctx
                self.assertFalse(ctx.enabled)
                self.assertIn('preference_error', ctx.to_dict())
                self.assertEqual((await self.enabled(True)).status, 500)
                self.assertEqual(self.path.read_text(encoding='utf-8'), raw)

    async def test_corruption_after_load_not_overwritten(self):
        ctx = await self.device()
        self.path.write_text('不能覆寫', encoding='utf-8')
        self.assertEqual((await self.enabled(False)).status, 500)
        self.assertTrue(ctx.enabled)
        self.assertEqual(self.path.read_text(encoding='utf-8'), '不能覆寫')

    async def test_clear_failure_warns_keeps_connection_and_saved_disabled(self):
        ctx = await self.device()
        ctx.state['simulating'] = True
        self.clear_error = OSError('手機拒絕清除')
        response = await self.enabled(False)
        self.assertFalse(ctx.enabled)
        self.assertTrue(ctx.state['connected'])
        self.assertTrue(ctx.state['simulating'])
        self.assertIn('未能確認手機定位已清除', self.body(response)['warning'])
        self.assertFalse(self.m.DevicePreferences(self.path).enabled('A'))
        self.terminate.assert_not_awaited()

    async def test_offline_clear_warns(self):
        ctx = self.m.DeviceCtx(0, 'A', 'A', '18')
        self.m._devices['A'] = ctx
        response = await self.enabled(False)
        self.assertEqual(response.status, 200)
        self.assertIn('未能確認手機定位已清除', self.body(response)['warning'])
        self.assertFalse(ctx.enabled)

    async def test_clear_timeout_warns_without_false_recovery_or_teardown(self):
        ctx = await self.device()
        ctx.state['simulating'] = True
        self.block_clear = True
        with patch.object(self.m, 'WIFI_HEALTH_TIMEOUT', 0.01):
            response = await self.enabled(False)
        self.assertIn('未能確認手機定位已清除', self.body(response)['warning'])
        self.assertTrue(ctx.state['connected'])
        self.assertTrue(ctx.state['simulating'])
        self.assertFalse(ctx.enabled)
        self.terminate.assert_not_awaited()

    async def test_disabled_dtx_drop_recovers_same_tunnel_without_replay(self):
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.dtx import DTXConnection
        connections = {'A': [], 'B': []}
        class DVT(DvtProvider):
            async def __aenter__(self):
                await super().__aenter__()
                closed = asyncio.Event()
                self._dtx.wait_disconnected = lambda: DTXConnection.wait_disconnected(
                    SimpleNamespace(_disconnected=closed))
                connections[self.rsd.udid].append(closed)
                return self
        self.mock('pymobiledevice3.services.dvt.instruments.dvt_provider.DvtProvider', DVT)
        a = await self.device('A', 0, 'wifi')
        b = await self.device('B', 1, 'wifi')
        await self.enabled(False)
        self.assertTrue(a.state['connected'])
        baseline = list(self.events)
        connections['A'][0].set()  # lockdown 心跳仍成功，僅 DTX reader 真正關閉。
        await self.until(lambda: len(connections['A']) == 2 and a.state['connected'])
        self.assertIs(self.m._devices['A'], a)
        self.assertFalse(a.enabled)
        self.assertFalse(a.closing)
        self.assertTrue(b.state['connected'])
        self.assertEqual(len(connections['B']), 1)
        self.assertEqual(self.events, baseline)
        self.assertEqual(self.sets, [])
        self.assertEqual(self.start.await_count, 2)
        self.terminate.assert_not_awaited()
        self.assertFalse(self.m.DevicePreferences(self.path).enabled('A'))

    async def test_cancelled_http_request_still_finishes_saved_disable(self):
        ctx = await self.device()
        self.block_clear = True
        request = asyncio.create_task(self.enabled(False))
        await self.clear_entered.wait()
        request.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await request
        self.assertFalse(ctx.enabled)
        self.assertFalse(self.m.DevicePreferences(self.path).enabled('A'))
        self.release_clear.set()
        await self.until(lambda: not ctx._enabled_tasks)
        self.assertFalse(ctx.state['simulating'])

    async def test_unreadable_preferences_fail_closed(self):
        with patch.object(Path, 'read_text', side_effect=PermissionError('禁止讀取')):
            self.m._preferences = self.m.DevicePreferences(self.path)
        ctx = self.m.DeviceCtx(0, 'A', 'A', '18')
        self.m._devices['A'] = ctx
        self.assertFalse(ctx.enabled)
        self.assertEqual((await self.enabled(True)).status, 500)
        self.assertFalse(self.path.exists())

    async def test_three_devices_disable_one_continues_others_and_clear_is_local(self):
        contexts = [await self.device(u, i) for i, u in enumerate('ABC')]
        await self.m._cruise.start('all', [{'lat': 25, 'lon': 121}, {'lat': 26, 'lon': 122}], 5)
        await self.until(lambda: len(self.sets) == 3)
        await self.enabled(False)
        self.assertEqual(self.m._cruise.target_udids, ['B', 'C'])
        self.assertEqual(self.m._cruise.phase, 'running')
        self.assertTrue(all(c.state['connected'] for c in contexts))
        self.assertFalse(contexts[0].state['simulating'])
        await self.m.route_clear(self.request())
        self.assertEqual(self.m._cruise.target_udids, ['B', 'C'])
        baseline = len(self.sets)
        self.m._cruise._post_position()
        await self.until(lambda: len(self.sets) >= baseline + 2)
        self.assertEqual({u for u, _, _ in self.sets[baseline:]}, {'B', 'C'})
        await self.enabled(False, 1)
        self.assertEqual(self.m._cruise.phase, 'running')
        await self.enabled(False, 2)
        self.assertEqual(self.m._cruise.phase, 'stopped')

    async def test_direct_disabled_routes_do_not_pause_other_cruise(self):
        await self.device('A', 0)
        await self.device('B', 1)
        await self.enabled(False)
        await self.m._cruise.start(1, [{'lat': 25, 'lon': 121}, {'lat': 26, 'lon': 122}], 5)
        for handler, data in ((self.m.route_set, {'lat': 1, 'lon': 2}),
                              (self.m.route_steps_add, {'count': 5}),
                              (self.m.route_steps_remove, {'count': 5}),
                              (self.m.route_steps_reset, {})):
            self.assertEqual((await handler(self.request(data=data))).status, 403)
        response = await self.m.route_cruise_start(self.request(data={
            'deviceIdx': 0, 'waypoints': [{'lat': 1, 'lon': 2}, {'lat': 2, 'lon': 3}], 'speed': 5}))
        self.assertNotEqual(response.status, 200)
        self.assertEqual(self.m._cruise.phase, 'running')
        self.assertEqual(self.m._cruise.target_udids, ['B'])
        self.trigger.assert_not_awaited()

    async def test_broadcast_and_cruise_resume_filter_disabled(self):
        contexts = [await self.device(u, i) for i, u in enumerate('ABC')]
        await self.enabled(False)
        response = await self.m.route_set_all(self.request(data={'lat': 1, 'lon': 2}))
        self.assertEqual(self.body(response)['idxs'], [1, 2])
        await self.until(lambda: len(self.sets) == 2)
        response = await self.m.route_steps_add_all(self.request(data={'count': 3}))
        self.assertEqual(self.body(response)['count'], 2)
        self.assertEqual(contexts[0].state['steps'], 0)
        response = await self.m.route_steps_remove_all(self.request(data={'count': 1}))
        self.assertEqual(self.body(response)['count'], 2)
        await self.m._cruise.start('all', [{'lat': 25, 'lon': 121}, {'lat': 26, 'lon': 122}], 5)
        self.assertEqual(self.m._cruise.target_udids, ['B', 'C'])
        await self.m._cruise.pause()
        await self.enabled(False, 1)
        await self.m.route_cruise_resume(self.request())
        self.assertEqual(self.m._cruise.target_udids, ['C'])
        await self.enabled(True)
        self.assertNotIn('A', self.m._cruise.target_udids)

    async def test_d_disabled_or_cleared_device_never_rejoins_cruise_after_reconnect(self):
        self.m._cruise.tick_interval = 3600
        for intent in ('disable', 'clear'):
            with self.subTest(intent=intent):
                await self.m._cruise.stop()
                for old in list(self.m._devices.values()):
                    await self.m.teardown_device(old)
                self.m._devices.clear()
                self.m._miss_counts.clear()
                self.present = {'A', 'B'}
                await self.device('A', 0)
                await self.device('B', 1)
                await self.m._cruise.start('all', [{'lat': 25, 'lon': 121}, {'lat': 26, 'lon': 122}], 5)
                await self.quiesce()
                if intent == 'disable':
                    await self.enabled(False)
                    await self.enabled(True)  # 重新啟用也不自動加回（使用者意圖）
                else:
                    await self.m.route_clear(self.request())
                self.assertEqual(self.m._cruise.target_udids, ['B'])
                self.assertNotIn('A', self.m._cruise._members)
                await self.quiesce()
                baseline = len(self.sets)
                await self.unplug('A')
                fresh = await self.replug('A')
                self.assertTrue(fresh.enabled)
                await self.quiesce()
                self.assertNotIn('A', self.m._cruise.target_udids)
                self.assertEqual(self.sets[baseline:], [])
                self.assertFalse(fresh.state['simulating'])
                self.assertIsNone(fresh._mailbox._cmd)
                self.assertEqual(self.m._cruise.phase, 'running')

    async def test_fast_off_on_waits_inflight_set_then_clear_without_replay(self):
        ctx = await self.device()
        self.block_set = True
        ctx._mailbox.post(self.m._SetCmd(1, 2))
        await self.set_entered.wait()
        ctx._mailbox.post(self.m._SetCmd(3, 4))
        off = asyncio.create_task(self.enabled(False))
        await self.until(lambda: not ctx.enabled)
        on = asyncio.create_task(self.enabled(True))
        await asyncio.sleep(0)
        self.assertFalse(off.done())
        self.assertFalse(on.done())
        self.release_set.set()
        self.assertEqual(self.body(await off), {'ok': True, 'enabled': False})
        self.assertEqual(self.body(await on), {'ok': True, 'enabled': True})
        self.assertEqual(self.events, [('set-start', 'A'), ('set-end', 'A'), ('clear', 'A')])
        self.assertEqual(self.sets, [('A', 1, 2)])
        self.assertIsNone(ctx._mailbox._cmd)
        self.assertFalse(ctx.state['simulating'])

    async def test_pending_shortcut_cancelled_and_late_ack_cannot_drain_new_steps(self):
        ctx = await self.device()
        entered = asyncio.Event()
        async def blocked(context, count):
            context._shortcut_ever_sent = True
            entered.set()
            await asyncio.Event().wait()
        self.trigger.side_effect = blocked
        await self.m.route_steps_add(self.request(data={'count': 5}))
        await entered.wait()
        response = await self.enabled(False)
        self.assertIn('捷徑無法撤回', self.body(response)['warning'])
        self.assertFalse(ctx._shortcut_tasks)
        self.assertNotIn('A', self.m._pending_steps)
        self.assertEqual((await self.m.route_shortcut_sync(self.request())).status, 403)
        self.assertEqual((await self.m.route_shortcut_ack(self.request(data={'idx': 0, 'count': 5}))).status, 403)
        await self.enabled(True)
        self.m._pending_steps['A'] = 7
        for data in ({'idx': 0, 'count': 5}, {'idx': 0, 'count': 5, 'generation': 0}):
            self.assertEqual((await self.m.route_shortcut_ack(self.request(data=data))).status, 409)
            self.assertEqual(self.m._pending_steps['A'], 7)
        sync = self.body(await self.m.route_shortcut_sync(self.request()))
        response = await self.m.route_shortcut_ack(self.request(data={
            'idx': 0, 'count': 5, 'generation': sync['generation']}))
        self.assertEqual(response.status, 200)
        self.assertEqual(self.m._pending_steps['A'], 2)

    async def test_background_queued_before_disable_never_dispatches_after_enable(self):
        ctx = await self.device()
        generation = ctx._control_generation
        await self.enabled(False)
        await self.enabled(True)
        await self.m._trigger_shortcut_bg(ctx, 9, generation)
        self.trigger.assert_not_awaited()

    async def test_legacy_shortcut_works_after_off_on_and_new_sync_with_delivery_cap(self):
        ctx = await self.device()
        self.m._pending_steps['A'] = 9
        await self.m.route_shortcut_sync(self.request())
        await self.enabled(False)
        self.assertIsNone(ctx._sync_generation)
        self.assertEqual(ctx._sync_remaining, 0)
        await self.enabled(True)
        self.m._pending_steps['A'] = 5
        old_ack = self.request(data={'idx': 0, 'count': 99})
        self.assertEqual((await self.m.route_shortcut_ack(old_ack)).status, 409)
        sync = self.body(await self.m.route_shortcut_sync(self.request()))
        self.assertEqual(sync['steps'], 5)
        self.m._pending_steps['A'] += 7  # 尚未交付的新待辦不可被舊 ack 扣掉。
        response = await self.m.route_shortcut_ack(old_ack)
        self.assertEqual(response.status, 200)
        self.assertEqual(self.body(response)['confirmed'], 5)
        self.assertEqual(self.m._pending_steps['A'], 7)
        response = await self.m.route_shortcut_ack(old_ack)
        self.assertEqual(self.body(response)['confirmed'], 0)
        await self.m.route_shortcut_sync(self.request())
        response = await self.m.route_shortcut_ack(self.request(data={'idx': 0, 'count': 7}))
        self.assertEqual(self.body(response)['confirmed'], 7)
        self.assertEqual(self.m._pending_steps['A'], 0)

    async def test_generation_acks_remain_strict_after_new_sync(self):
        ctx = await self.device()
        await self.enabled(False)
        await self.enabled(True)
        self.m._pending_steps['A'] = 4
        await self.m.route_shortcut_sync(self.request())
        for generation in (0, None, True, '1'):
            response = await self.m.route_shortcut_ack(self.request(data={
                'idx': 0, 'count': 4, 'generation': generation}))
            self.assertEqual(response.status, 409)
            self.assertEqual(self.m._pending_steps['A'], 4)
        response = await self.m.route_shortcut_ack(self.request(data={
            'idx': 0, 'count': 99, 'generation': ctx._control_generation}))
        self.assertEqual(self.body(response)['confirmed'], 4)

    async def test_inflight_http_body_cannot_replay_across_off_on(self):
        await self.device('A', 0)
        await self.device('B', 1)
        await self.m._cruise.start(1, [{'lat': 25, 'lon': 121}, {'lat': 26, 'lon': 122}], 5)
        for handler, data in ((self.m.route_set, {'lat': 1, 'lon': 2}),
                              (self.m.route_steps_add, {'count': 5})):
            entered, release = asyncio.Event(), asyncio.Event()
            async def body():
                entered.set()
                await release.wait()
                return data
            request = self.request()
            request.json = body
            task = asyncio.create_task(handler(request))
            await entered.wait()
            await self.enabled(False)
            await self.enabled(True)
            release.set()
            self.assertEqual((await task).status, 403)
            self.assertEqual(self.m._cruise.phase, 'running')
            self.assertEqual(self.m._cruise.target_udids, ['B'])

    async def test_shortcut_process_is_reaped_on_cancellation(self):
        entered = asyncio.Event()
        async def communicate():
            entered.set()
            await asyncio.Event().wait()
        proc = SimpleNamespace(returncode=None, communicate=communicate, kill=Mock(), wait=AsyncMock())
        with patch('asyncio.create_subprocess_exec', AsyncMock(return_value=proc)):
            task = asyncio.create_task(self.m._run_cmd(['隔離程序']))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        proc.kill.assert_called_once()
        proc.wait.assert_awaited_once()

    async def test_http_api_and_options(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        await self.device()
        app = web.Application(middlewares=[self.m.cors_middleware])
        app.router.add_post('/device/{idx}/enabled', self.m.route_device_enabled)
        app.router.add_get('/devices', self.m.route_devices)
        app.router.add_route('OPTIONS', '/device/{idx}/enabled', lambda r: web.Response())
        async with TestClient(TestServer(app)) as client:
            response = await client.options('/device/0/enabled')
            self.assertEqual(response.status, 200)
            response = await client.post('/device/0/enabled', json={'enabled': False})
            self.assertEqual(await response.json(), {'ok': True, 'enabled': False})
            response = await client.get('/devices')
            self.assertFalse((await response.json())[0]['enabled'])


if __name__ == '__main__':
    unittest.main()
