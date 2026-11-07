"""Wi-Fi 隔離驗收：不碰現場手機、tunnel 或 launcher。"""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_usb_lifecycle import LifecycleHarness, ROUTE


class WifiLifecycleTests(LifecycleHarness):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.real_health = self.m._wifi_health
        self.health = self.mock_object(self.m, '_wifi_health', AsyncMock())
        self.m._connection_mode = 'wifi'
        self.probes = []
        self.probe_block = False
        self.probe_error = False
        owner = self

        class Pairing:
            def __init__(self, identifier, host, port):
                self.identifier = identifier
                # 實機普通 Wi-Fi handshake 沒有 peerDeviceInfo.identifier。
                self.handshake_info = {'wireProtocolVersion': 26, 'deviceOptions': {}}
                self.close = AsyncMock()
                owner.probes.append(self)

            async def connect(self, autopair=True):
                owner.assertFalse(autopair)
                if owner.probe_block:
                    owner.entered.set()
                    await asyncio.Event().wait()
                if owner.probe_error:
                    raise OSError('驗證失敗')

        self.mock('pymobiledevice3.remote.tunnel_service.RemotePairingTunnelService', Pairing)
        self.paired = self.mock('pymobiledevice3.remote.tunnel_service.iter_remote_paired_identifiers', return_value=iter(['A']))
        self.real_browse = self.m._browse_wifi_endpoints
        self.browse = self.mock_object(self.m, '_browse_wifi_endpoints', AsyncMock(return_value=[
            ('::1', 123), ('::2', 123)]))

    async def mode(self, value):
        return await self.m.route_connection_mode(SimpleNamespace(method='POST', json=AsyncMock(return_value=value)))

    def listing(self, udids):
        return [{'udid': u, 'name': u, 'ios': '18'} for u in udids]

    async def setup_now(self, ctx):
        ctx.worker_task = asyncio.create_task(self.m.gps_worker(ctx))
        self.tasks.append(ctx.worker_task)

    async def test_scan_deduplicates_and_closes_every_probe(self):
        result = await self.m.scan_wifi_devices()
        self.assertEqual([r['udid'] for r in result], ['A'])
        self.assertEqual(len(self.probes), 2)
        for probe in self.probes:
            probe.close.assert_awaited_once()

    async def test_no_pairing_does_not_browse_or_connect(self):
        self.paired.return_value = iter([])
        self.assertEqual(await self.m.scan_wifi_devices(), [])
        self.browse.assert_not_awaited()
        self.assertEqual(self.probes, [])

    async def test_pair_verify_rejection_never_adds_untrusted_device(self):
        self.probe_error = True
        self.assertIsNone(await self.m.scan_wifi_devices())
        for probe in self.probes:
            probe.close.assert_awaited_once()

    async def test_total_deadline_keeps_complete_unambiguous_candidate(self):
        self.paired.return_value = iter(['A', 'B'])
        self.browse.return_value = [('::1', 123), ('::2', 123)]
        owner = self
        class Pairing:
            def __init__(self, identifier, *endpoint):
                self.identifier = identifier
                self.endpoint = endpoint
                self.close = AsyncMock()
                owner.probes.append(self)
            async def connect(self, autopair=True):
                owner.assertFalse(autopair)
                if self.endpoint[0] == '::2':
                    await asyncio.Event().wait()
                if self.identifier == 'B':
                    raise OSError('配對不符')
        with patch('pymobiledevice3.remote.tunnel_service.RemotePairingTunnelService', Pairing), \
                patch.object(self.m, 'WIFI_SCAN_TIMEOUT', 0.02):
            result = await self.m.scan_wifi_devices()
        self.assertEqual([r['udid'] for r in result], ['A'])
        for probe in self.probes:
            probe.close.assert_awaited_once()

    async def test_same_endpoint_multiple_ids_is_ambiguous(self):
        self.paired.return_value = iter(['A', 'B'])
        self.browse.return_value = [('::1', 123)]
        result = await self.m.scan_wifi_devices()
        self.assertEqual(result, [])
        await self.m._apply_scan(result, 'wifi')
        self.assertEqual(self.m._devices, {})
        self.start.assert_not_awaited()

    async def test_unfinished_endpoint_not_mistaken_for_unique_identity(self):
        self.paired.return_value = iter(['A', 'B'])
        self.browse.return_value = [('::1', 123)]
        class Pairing:
            def __init__(self, identifier, *endpoint):
                self.identifier = identifier
                self.close = AsyncMock()
            async def connect(self, autopair=False):
                if self.identifier == 'B':
                    await asyncio.Event().wait()
        with patch('pymobiledevice3.remote.tunnel_service.RemotePairingTunnelService', Pairing), \
                patch.object(self.m, 'WIFI_SCAN_TIMEOUT', 0.02):
            self.assertIsNone(await self.m.scan_wifi_devices())

    async def test_candidate_disconnected_until_rsd_and_mismatch_not_rebuilt(self):
        ctx = self.m.DeviceCtx(0, 'A', 'A', '18', 'wifi')
        self.m._devices['A'] = ctx
        self.assertFalse(ctx.to_dict()['connected'])
        response = await self.m.route_set(SimpleNamespace(match_info={'idx': '0'}))
        self.assertEqual(response.status, 503)
        async def wrong_tunnel(context):
            context.rsd_host, context.rsd_port = 'B', 123
            return True
        self.start.side_effect = wrong_tunnel
        await self.m.gps_worker(ctx)
        self.assertTrue(ctx.closing)
        self.assertFalse(ctx.state['connected'])
        self.assertEqual(self.sets, [])
        self.assertEqual(len(self.resources), 1)  # RSD 之後不可建立 DVT 或定位服務。
        self.assertIn('A', self.m._wifi_rejected_udids)
        for _ in range(3):
            await self.m._apply_scan(self.listing('A'), 'wifi')
        self.assertIs(self.m._devices['A'], ctx)
        self.assertEqual(self.start.await_count, 1)
        for mode in ('usb', 'wifi'):
            await self.mode({'mode': mode})
        await self.m._apply_scan(self.listing('A'), 'wifi')
        self.assertNotIn('A', self.m._devices)

    async def test_failure_distinct_from_empty(self):
        self.browse.side_effect = OSError('探索故障')
        self.assertIsNone(await self.m.scan_wifi_devices())
        self.browse.side_effect = None
        self.paired.return_value = iter(['A'])
        self.browse.return_value = []
        self.assertEqual(await self.m.scan_wifi_devices(), [])

    async def test_failed_verification_closes_partial_connection(self):
        self.probe_error = True
        self.assertIsNone(await self.m.scan_wifi_devices())
        for probe in self.probes:
            probe.close.assert_awaited_once()

    async def test_cancel_partial_connect_closes_all(self):
        self.probe_block = True
        task = asyncio.create_task(self.m.scan_wifi_devices())
        await self.entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        for probe in self.probes:
            probe.close.assert_awaited_once()

    async def test_cancel_mdns_acquisition_reaps_owned_process(self):
        async def communicate():
            self.entered.set()
            await asyncio.Event().wait()
        proc = SimpleNamespace(returncode=None, communicate=communicate, kill=Mock(), wait=AsyncMock())
        with patch('asyncio.create_subprocess_exec', AsyncMock(return_value=proc)):
            task = asyncio.create_task(self.real_browse())
            await self.entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        proc.kill.assert_called_once()
        proc.wait.assert_awaited_once()

    async def test_probe_timeout_is_bounded_and_closes(self):
        self.probe_block = True
        with patch.object(self.m, 'WIFI_PROBE_TIMEOUT', 0.01):
            self.assertIsNone(await self.m.scan_wifi_devices())
        for probe in self.probes:
            probe.close.assert_awaited_once()

    async def test_wifi_does_not_use_usb_and_empty_mdns_keeps_health(self):
        self.present.clear()
        ctx = await self.device(connection_type='wifi')
        await self.until(lambda: self.health.await_count > 0)
        await self.m._apply_scan([], 'wifi')
        await self.m._apply_scan(None, 'wifi')
        self.assertTrue(ctx.state['connected'])
        self.assertFalse(ctx.closing)
        self.list_usb.assert_not_awaited()
        self.assertEqual(ctx.to_dict()['connection_type'], 'wifi')

    async def test_health_timeout_removes_cruise_and_old_mailbox(self):
        async def blocked(dvt):
            await asyncio.Event().wait()
        self.health.side_effect = blocked
        with patch.object(self.m, 'WIFI_HEALTH_TIMEOUT', 0.01):
            ctx = await self.device(connection_type='wifi')
            self.m._cruise.target_udids = ['A']
            self.m._cruise.phase = 'running'
            # 初次健康檢查尚未完成時就必須能偵測斷線。
            await self.until(lambda: ctx.closing)
        await self.until(lambda: ctx.worker_task is None)
        self.assertFalse(ctx.state['connected'])
        self.assertIsNone(ctx._mailbox._cmd)
        self.assertEqual(self.m._cruise.phase, 'stopped')

    async def test_health_uses_real_query_type_request_each_time(self):
        from pymobiledevice3.lockdown import RemoteLockdownClient
        client = object.__new__(RemoteLockdownClient)
        client.label = '隔離心跳測試'
        client.service = SimpleNamespace(send_recv_plist=AsyncMock(return_value={
            'Request': 'QueryType', 'Type': 'com.apple.mobile.lockdown'}))
        rsd = SimpleNamespace(lockdown=client)
        await self.real_health(rsd)
        await self.real_health(rsd)
        self.assertEqual(client.service.send_recv_plist.await_count, 2)
        client.service.send_recv_plist.assert_awaited_with({
            'Label': '隔離心跳測試', 'Request': 'QueryType'})

    async def test_health_rejects_missing_lockdown_and_wrong_response(self):
        with self.assertRaises(ConnectionError):
            await self.real_health(SimpleNamespace(lockdown=None))
        for response in (None, '錯誤服務'):
            with self.assertRaises(ConnectionError):
                await self.real_health(SimpleNamespace(lockdown=SimpleNamespace(
                    query_type=AsyncMock(return_value=response))))

    def use_live_lockdown_health(self):
        from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
        queries = {}
        class RSD(RemoteServiceDiscoveryService):
            def __init__(self, address):
                super().__init__(address)
                self.lockdown.query_type = AsyncMock(return_value='com.apple.mobile.lockdown')
                queries[self.udid] = self.lockdown.query_type
        self.mock('pymobiledevice3.remote.remote_service_discovery.RemoteServiceDiscoveryService', RSD)
        self.health.side_effect = self.real_health
        self.m.WIFI_HEALTH_INTERVAL = 0.01
        return queries

    async def test_unavailable_system_information_does_not_reconnect_then_hangup_isolated(self):
        queries = self.use_live_lockdown_health()
        with patch('pymobiledevice3.services.dvt.instruments.device_info.DeviceInfo.system_information',
                   AsyncMock(side_effect=RuntimeError('it does not respond to selector'))) as unsupported:
            a = await self.device('A', 0, 'wifi')
            b = await self.device('B', 1, 'wifi')
            await self.until(lambda: all(q.await_count >= 2 for q in queries.values()))
            self.assertTrue(a.state['connected'] and b.state['connected'])
            self.assertEqual(self.start.await_count, 2)
            self.terminate.assert_not_awaited()
            unsupported.assert_not_awaited()
            self.assertEqual(self.sets, [])
            self.m._cruise.target_udids = ['A', 'B']
            self.m._cruise.phase = 'running'
            queries['A'].side_effect = ConnectionResetError('remote lockdown 真掛斷')
            await self.until(lambda: a.closing and a.worker_task is None)
            self.assertFalse(a.state['connected'])
            self.assertTrue(b.state['connected'])
            self.assertEqual(self.m._cruise.target_udids, ['B'])
            self.assertEqual(self.m._cruise.phase, 'running')
            self.assertIsNone(a._mailbox._cmd)
            self.resources[0].lockdown.close.assert_awaited_once()
            self.resources[0].service.close.assert_awaited_once()

    async def test_live_query_type_timeout_triggers_cleanup(self):
        queries = self.use_live_lockdown_health()
        ctx = await self.device(connection_type='wifi')
        await self.until(lambda: queries['A'].await_count >= 1)
        async def hang():
            await asyncio.Event().wait()
        queries['A'].side_effect = hang
        with patch.object(self.m, 'WIFI_HEALTH_TIMEOUT', 0.01):
            await self.until(lambda: ctx.closing and ctx.worker_task is None)
        self.assertFalse(ctx.state['connected'])
        self.resources[0].lockdown.close.assert_awaited_once()
        self.resources[0].service.close.assert_awaited_once()
        self.assertEqual(self.sets, [])

    async def test_auto_usb_first_and_healthy_wifi_not_replaced(self):
        self.m._connection_mode = 'auto'
        with patch.object(self.m, 'setup_device', AsyncMock()):
            await self.m._apply_scan(self.listing('A'), 'usb')
            first = self.m._devices['A']
            await self.m._apply_scan(self.listing('A'), 'wifi')
            self.assertIs(self.m._devices['A'], first)
            self.assertEqual(first.connection_type, 'usb')
            await self.m.teardown_device(first)
            self.m._devices.clear()
            await self.m._apply_scan([], 'usb')
            await self.m._apply_scan(self.listing('A'), 'wifi')
            wifi = self.m._devices['A']
            await self.m._apply_scan(self.listing('A'), 'usb')
            self.assertIs(self.m._devices['A'], wifi)
            self.assertEqual(wifi.connection_type, 'wifi')

    async def test_mode_api_http_and_selective_cleanup(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        usb = await self.device('A', 0)
        wifi = await self.device('B', 1, 'wifi')
        app = web.Application()
        app.router.add_get('/connection-mode', self.m.route_connection_mode)
        app.router.add_post('/connection-mode', self.m.route_connection_mode)
        async with TestClient(TestServer(app)) as client:
            response = await client.get('/connection-mode')
            self.assertEqual(await response.json(), {'ok': True, 'mode': 'wifi'})
            for data in ({'mode': 'bad'}, [], {'mode': []}, {}):
                response = await client.post('/connection-mode', json=data)
                self.assertEqual(response.status, 400)
            response = await client.post('/connection-mode', data='{', headers={'Content-Type': 'application/json'})
            self.assertEqual(response.status, 400)
            response = await client.post('/connection-mode', json={'mode': 'auto'})
            self.assertEqual(await response.json(), {'ok': True, 'mode': 'auto'})
            self.assertFalse(usb.closing or wifi.closing)
            response = await client.post('/connection-mode', json={'mode': 'usb'})
            self.assertEqual(await response.json(), {'ok': True, 'mode': 'usb'})
            self.assertTrue(wifi.closing)
            self.assertFalse(usb.closing)
            self.assertEqual(set(self.m._devices), {'A'})

    async def test_mode_change_cancels_probe_and_rejects_stale_generation(self):
        self.probe_block = True
        self.m._wifi_scan_task = asyncio.create_task(self.m.scan_wifi_devices())
        await self.entered.wait()
        await self.mode({'mode': 'usb'})
        self.assertTrue(self.m._wifi_scan_task.cancelled())
        for probe in self.probes:
            probe.close.assert_awaited_once()
        await self.mode({'mode': 'wifi'})
        await self.m._apply_scan(self.listing('A'), 'wifi', 0)
        self.assertEqual(self.m._devices, {})

    async def test_blocked_wifi_scan_does_not_block_usb(self):
        self.m._connection_mode = 'auto'
        self.probe_block = True
        with patch.object(self.m, 'SCAN_SEC', 0.001), patch.object(self.m, 'setup_device', AsyncMock()):
            task = asyncio.create_task(self.m.device_scanner())
            self.tasks.append(task)
            await self.entered.wait()
            await self.until(lambda: len(self.m._devices) == 3)
            self.present.remove('A')
            await self.until(lambda: 'A' not in self.m._devices)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        for probe in self.probes:
            probe.close.assert_awaited_once()

    async def test_wifi_subprocess_explicit_interface_udid_no_stdin(self):
        ctx = self.m.DeviceCtx(0, 'A', 'A', '18', 'wifi')
        proc = SimpleNamespace(returncode=1, stdout=SimpleNamespace(readline=AsyncMock(return_value=b'')))
        with patch('asyncio.create_subprocess_exec', AsyncMock(return_value=proc)) as spawn:
            self.assertFalse(await self.m._try_start_tunnel(ctx))
            args = spawn.call_args.args
            self.assertEqual(args[args.index('--connection-type') + 1], 'wifi')
            self.assertEqual(args[args.index('--udid') + 1], 'A')
            self.assertEqual(spawn.call_args.kwargs['stdin'], asyncio.subprocess.DEVNULL)

    async def test_three_wifi_disconnect_reconnect_rejoins_cruise_with_current_position(self):
        self.m.WIFI_HEALTH_INTERVAL = 0.01
        # 不靠計時 tick 推進位置，才能精確比對重接後立即收到的目前巡航位置。
        self.m._cruise.tick_interval = 3600
        for removed in ('A', 'BC', 'ABC'):
            with self.subTest(removed=removed):
                for ctx in list(self.m._devices.values()):
                    await self.m.teardown_device(ctx)
                self.m._devices.clear()
                self.health.side_effect = None
                contexts = {u: await self.device(u, i, 'wifi') for i, u in enumerate('ABC')}
                await self.m._cruise.start('all', [{'lat': 25, 'lon': 121}, {'lat': 26, 'lon': 122}], 5)
                await self.until(lambda: all(c.state['simulating'] for c in contexts.values()))
                # 模擬通道失敗，健康的其他手機必須繼續。
                async def health(rsd):
                    if rsd.udid in removed:
                        raise OSError('Wi-Fi 斷線')
                self.health.side_effect = health
                await self.until(lambda: all(contexts[u].closing and contexts[u].worker_task is None for u in removed))
                self.health.side_effect = None
                self.assertEqual(set(self.m._cruise.target_udids), set('ABC') - set(removed))
                self.assertEqual(self.m._cruise.phase, 'stopped' if removed == 'ABC' else 'running')
                for u in set('ABC') - set(removed):
                    self.assertTrue(contexts[u].state['connected'])
                baseline = len(self.sets)
                with patch.object(self.m, 'setup_device', self.setup_now):
                    await self.m._apply_scan(self.listing('ABC'), 'wifi')
                    await self.until(lambda: all(c.state['connected'] for c in self.m._devices.values()))
                # 巡航仍在進行：重接的手機自動加回，且立即收到『目前』巡航位置（只一次，
                # 不重播舊指令）。全部斷線時巡航已停止，重接不會帶回巡航。
                rejoins = removed != 'ABC'
                for u in removed:
                    fresh = self.m._devices[u]
                    self.assertIsNot(fresh, contexts[u])
                    self.assertNotEqual(fresh.idx, contexts[u].idx)
                    if rejoins:
                        await self.until(lambda: fresh.state['simulating'])
                        self.assertIn(u, self.m._cruise.target_udids)
                        position = self.m._cruise.position
                        self.assertIn((u, position['lat'], position['lon']), self.sets[baseline:])
                    else:
                        self.assertFalse(fresh.state['simulating'])
                        self.assertIsNone(fresh._mailbox._cmd)
                        self.assertNotIn(u, self.m._cruise.target_udids)
                if rejoins:
                    self.assertEqual(sorted(u for u, _, _ in self.sets[baseline:]), sorted(removed))
                    self.assertEqual(set(self.m._cruise.target_udids), set('ABC'))
                    self.assertEqual(self.m._cruise.phase, 'running')
                else:
                    self.assertEqual(self.sets[baseline:], [])
                    self.assertEqual(self.m._cruise.phase, 'stopped')

    async def test_c_wifi_heartbeat_drop_and_rediscovery_rejoins_cruise(self):
        self.m.WIFI_HEALTH_INTERVAL = 0.01
        self.m._cruise.tick_interval = 3600
        a = await self.device('A', 0, 'wifi')
        b = await self.device('B', 1, 'wifi')
        await self.m._cruise.start('all', ROUTE, 5)
        await self.until(lambda: a.state['simulating'] and b.state['simulating'])
        await self.quiesce()
        async def health(rsd):
            if rsd.udid == 'A':
                raise OSError('Wi-Fi 斷線')
        self.health.side_effect = health
        await self.until(lambda: a.closing and a.worker_task is None)
        self.health.side_effect = None
        self.assertEqual(self.m._cruise.target_udids, ['B'])
        self.assertEqual(self.m._cruise.phase, 'running')
        baseline = len(self.sets)
        with patch.object(self.m, 'setup_device', self.setup_now):
            await self.m._apply_scan(self.listing('AB'), 'wifi')
        fresh = self.m._devices['A']
        self.assertIsNot(fresh, a)
        self.assertNotEqual(fresh.idx, a.idx)
        await self.until(lambda: fresh.state['simulating'])
        position = self.m._cruise.position
        self.assertEqual(self.sets[baseline:], [('A', position['lat'], position['lon'])])
        self.assertEqual(sorted(self.m._cruise.target_udids), ['A', 'B'])
        self.assertTrue(b.state['connected'])

    async def test_dtx_drop_same_ctx_recovery_rejoins_cruise(self):
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
        self.m._cruise.tick_interval = 3600
        a = await self.device('A', 0, 'wifi')
        await self.device('B', 1, 'wifi')
        await self.m._cruise.start('all', ROUTE, 5)
        await self.quiesce()
        baseline = len(self.sets)
        connections['A'][0].set()  # 只有 DTX 關閉，tunnel 仍在：同一個 ctx 原地恢復
        await self.until(lambda: len(connections['A']) == 2 and a.state['simulating']
                         and 'A' in self.m._cruise.target_udids)
        self.assertIs(self.m._devices['A'], a)
        self.assertEqual(self.start.await_count, 2)
        self.terminate.assert_not_awaited()
        position = self.m._cruise.position
        self.assertEqual(self.sets[baseline:], [('A', position['lat'], position['lon'])])
        self.assertEqual(sorted(self.m._cruise.target_udids), ['A', 'B'])
        self.assertEqual(len(connections['B']), 1)


if __name__ == '__main__':
    unittest.main()
