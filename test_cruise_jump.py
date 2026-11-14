"""跳點巡航（jump 模式）隔離測試：時間由假時鐘驅動，不睡眠、不連手機、不送系統訊號。"""
import asyncio
import json
import math
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from test_usb_lifecycle import LifecycleHarness, ROUTE


# 三個航點、間距約 1.1 公里（0→1 往北、1→2 往東），方便驗證落點方向。
SHORT = [{'lat': 25.0, 'lon': 121.0}, {'lat': 25.01, 'lon': 121.0},
         {'lat': 25.01, 'lon': 121.01}]
OLD_STATUS_KEYS = {'ok', 'id', 'state', 'active', 'paused', 'deviceIdx', 'target_udids',
                   'waypoints', 'speed', 'loop', 'position', 'progress', 'target_index'}


class _ClockLoop:
    """把 loop.time() 換成測試控制的假時鐘，其餘全部轉給真正的 event loop。"""
    def __init__(self, loop, owner):
        self._loop = loop
        self._owner = owner

    def time(self):
        return self._owner.now

    def __getattr__(self, name):
        return getattr(self._loop, name)


class CruiseJumpTests(LifecycleHarness):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.m._cruise.tick_interval = 3600   # 背景 tick 不會自己觸發，由 tick() 推進
        self.now = 1000.0
        proxy = _ClockLoop(asyncio.get_running_loop(), self)
        self.mock_object(self.m.asyncio, 'get_running_loop', lambda: proxy)
        self.c = self.m._cruise

    # ── 小工具 ──────────────────────────────────────────────────────
    async def tick(self, seconds=1.0):
        """假時鐘前進 seconds 秒並喚醒真正的 _run 迴圈跑一次 tick（推進＋送位置）。"""
        self.now += seconds
        self.c._wake.set()
        await self.quiesce()

    async def start_jump(self, route=SHORT, devices='A', loop=False, **params):
        for i, udid in enumerate(devices):
            await self.device(udid, i)
        kwargs = {'dwell_sec': 10, 'radius_m': 30, 'circle_speed': 19}
        kwargs.update(params)
        status = await self.c.start('all', route, 5, loop, mode='jump', **kwargs)
        await self.quiesce()
        return status

    def dist(self, a, b):
        return self.m._distance_m(a, b)

    def bearing(self, origin, point):
        return self.m._bearing_rad(origin, point)

    def assertAngle(self, a, b, delta):
        diff = (a - b + math.pi) % (2 * math.pi) - math.pi
        self.assertLess(abs(diff), delta, f'角度 {a} 與 {b} 相差太多')

    def request(self, data):
        return SimpleNamespace(json=AsyncMock(return_value=data))

    def last_set(self, udid='A'):
        return next((s for s in reversed(self.sets) if s[0] == udid), None)

    # ── 落點與繞圈 ──────────────────────────────────────────────────
    async def test_first_stop_lands_north_on_circle_and_is_posted(self):
        status = await self.start_jump()
        pos = self.c.position
        self.assertAlmostEqual(self.dist(pos, SHORT[0]), 30, delta=0.01)
        self.assertAngle(self.bearing(SHORT[0], pos), 0.0, 1e-6)   # 第一站朝北
        self.assertGreater(pos['lat'], SHORT[0]['lat'])
        self.assertEqual((status['stopIndex'], status['target_index']), (0, 0))
        self.assertEqual(status['dwellRemaining'], 10)
        self.assertEqual(self.last_set('A'), ('A', pos['lat'], pos['lon']))

    async def test_next_stop_lands_on_circle_facing_previous_position(self):
        await self.start_jump()
        await self.tick(9.0)
        self.assertEqual(self.c.stop_index, 0)
        before = dict(self.c.position)
        await self.tick(1.0)
        landing = self.c.position
        self.assertEqual(self.c.stop_index, 1)
        self.assertAlmostEqual(self.dist(landing, SHORT[1]), 30, delta=0.01)
        # 朝向跳來之前的位置：落點在「前一位置→新中心」連線上、靠近前一位置那一側
        self.assertAngle(self.bearing(SHORT[1], landing), self.bearing(SHORT[1], before), 0.01)
        self.assertAlmostEqual(self.dist(before, landing),
                               self.dist(before, SHORT[1]) - 30, delta=0.1)
        self.assertLess(landing['lat'], SHORT[1]['lat'])   # 從南邊跳來 → 落在南側
        # 精確驗算：跳點當下的位置＝第 10 秒的圓周點
        v = 19 * 1000 / 3600
        prev = self.m._offset_point(SHORT[0], v * 10 / 30, 30)
        expected = self.m._offset_point(SHORT[1], self.bearing(SHORT[1], prev), 30)
        self.assertAlmostEqual(landing['lat'], expected['lat'], places=12)
        self.assertAlmostEqual(landing['lon'], expected['lon'], places=12)
        self.assertEqual(self.c.status()['dwellRemaining'], 10)

    async def test_circling_speed_is_constant_and_clockwise(self):
        for kmh in (19, 10, 3.6):
            with self.subTest(circleSpeed=kmh):
                await self.c.stop()
                for old in list(self.m._devices.values()):
                    await self.m.teardown_device(old)
                self.m._devices.clear()
                await self.start_jump(circle_speed=kmh, dwell_sec=60)
                expected = kmh * 1000 / 3600
                prev, prev_angle = dict(self.c.position), self.c.angle
                for _ in range(8):
                    await self.tick(1.0)
                    pos = self.c.position
                    self.assertAlmostEqual(self.dist(prev, pos), expected, delta=0.02)
                    self.assertAlmostEqual(self.dist(pos, SHORT[0]), 30, delta=0.01)
                    self.assertAngle(self.c.angle - prev_angle, expected / 30, 1e-9)  # 順時針
                    self.assertEqual(self.last_set('A'), ('A', pos['lat'], pos['lon']))
                    prev, prev_angle = dict(pos), self.c.angle

    async def test_moves_to_next_stop_only_after_dwell_sec(self):
        await self.start_jump()
        await self.tick(5.0)
        status = self.c.status()
        self.assertEqual(status['stopIndex'], 0)
        self.assertAlmostEqual(status['dwellRemaining'], 5.0)
        self.assertAlmostEqual(status['progress'], (0 + 0.5) / 3)
        await self.tick(4.5)
        self.assertEqual(self.c.stop_index, 0)
        await self.tick(0.5)
        status = self.c.status()
        self.assertEqual((status['stopIndex'], status['target_index']), (1, 1))
        self.assertAlmostEqual(status['dwellRemaining'], 10.0)
        self.assertAlmostEqual(status['progress'], 1 / 3)
        await self.tick(10.7)   # 超過的 0.7 秒留給下一站
        status = self.c.status()
        self.assertEqual(status['stopIndex'], 2)
        self.assertAlmostEqual(status['dwellRemaining'], 9.3)

    async def test_loop_off_completes_after_last_dwell(self):
        await self.start_jump(dwell_sec=2)
        await self.tick(2.0)
        await self.tick(2.0)
        self.assertEqual(self.c.stop_index, 2)
        await self.tick(1.5)
        self.assertEqual(self.c.phase, 'running')
        await self.tick(0.5)
        status = self.c.status()
        self.assertEqual(status['state'], 'completed')
        self.assertFalse(status['active'])
        self.assertEqual(status['stopIndex'], 2)
        self.assertEqual(status['dwellRemaining'], 0)
        self.assertEqual(status['progress'], 1.0)
        self.assertAlmostEqual(self.dist(self.c.position, SHORT[2]), 30, delta=0.01)

    async def test_loop_on_returns_to_first_waypoint(self):
        await self.start_jump(dwell_sec=2, loop=True)
        await self.tick(5.0)
        self.assertEqual(self.c.stop_index, 2)
        before = dict(self.c.position)
        await self.tick(1.5)   # 一次推進跨過最後一站 → 回到第 1 點，剩 0.5 秒接著算
        status = self.c.status()
        self.assertEqual(status['state'], 'running')
        self.assertEqual(status['stopIndex'], 0)
        self.assertAlmostEqual(status['dwellRemaining'], 1.5)
        self.assertAlmostEqual(self.dist(self.c.position, SHORT[0]), 30, delta=0.01)
        # 回到第 1 點時落點朝向最後一站（東北方），之後剩下的 0.5 秒順時針繞了一小段
        circled = 19 * 1000 / 3600 * 0.5 / 30
        self.assertAngle(self.bearing(SHORT[0], self.c.position),
                         self.bearing(SHORT[0], before) + circled, 0.01)

    async def test_pause_freezes_dwell_timer_and_position(self):
        await self.start_jump()
        await self.tick(3.0)
        status = await self.c.pause()
        self.assertEqual(status['state'], 'paused')
        frozen = dict(self.c.position)
        await self.quiesce()
        baseline = len(self.sets)
        await self.tick(100.0)
        status = self.c.status()
        self.assertEqual(self.c.position, frozen)
        self.assertAlmostEqual(status['dwellRemaining'], 7.0)
        self.assertEqual(status['stopIndex'], 0)
        self.assertEqual(self.sets[baseline:], [])
        await self.c.resume()
        await self.tick(6.5)
        self.assertEqual(self.c.stop_index, 0)
        self.assertAlmostEqual(self.c.status()['dwellRemaining'], 0.5)
        await self.tick(0.5)
        self.assertEqual(self.c.stop_index, 1)

    # ── 即時更新 ────────────────────────────────────────────────────
    async def test_live_dwell_update_shorter_than_elapsed_moves_on_now(self):
        await self.start_jump()
        await self.tick(6.0)
        status = await self.c.update({'dwellSec': 5})
        self.assertEqual(status['stopIndex'], 1)
        self.assertEqual(status['dwellSec'], 5)
        self.assertAlmostEqual(status['dwellRemaining'], 5.0)
        self.assertAlmostEqual(self.dist(self.c.position, SHORT[1]), 30, delta=0.01)
        await self.quiesce()
        pos = self.c.position
        self.assertEqual(self.last_set('A'), ('A', pos['lat'], pos['lon']))
        # 改長：目前這站也跟著延長
        await self.tick(3.0)
        status = await self.c.update({'dwellSec': 20})
        self.assertEqual(status['stopIndex'], 1)
        self.assertAlmostEqual(status['dwellRemaining'], 17.0)

    async def test_live_dwell_update_while_paused_moves_on_after_resume(self):
        await self.start_jump()
        await self.tick(6.0)
        await self.c.pause()
        status = await self.c.update({'dwellSec': 5})
        self.assertEqual((status['state'], status['stopIndex']), ('paused', 0))
        self.assertEqual(status['dwellRemaining'], 0)
        await self.c.resume()
        await self.quiesce()
        self.assertEqual(self.c.stop_index, 1)

    async def test_live_radius_update_keeps_angle_and_posts(self):
        await self.start_jump()
        await self.tick(3.0)
        angle = self.bearing(SHORT[0], self.c.position)
        stored_angle = self.c.angle
        for phase in ('running', 'paused'):
            with self.subTest(phase=phase):
                if phase == 'paused':
                    await self.c.pause()
                for radius in (100, 0, 45):
                    status = await self.c.update({'radiusM': radius})
                    pos = self.c.position
                    self.assertEqual(status['radiusM'], radius)
                    self.assertAlmostEqual(self.dist(pos, SHORT[0]), radius, delta=0.01)
                    self.assertEqual(self.c.angle, stored_angle)   # 角度不變
                    if radius:   # 平面近似換算與球面方位角在 100 公尺內差距 < 1 公分
                        self.assertAngle(self.bearing(SHORT[0], pos), angle, 1e-4)
                    self.assertAlmostEqual(status['dwellRemaining'], 7.0)
                    await self.quiesce()
                    self.assertEqual(self.last_set('A'), ('A', pos['lat'], pos['lon']))

    async def test_live_circle_speed_update_applies_from_now(self):
        await self.start_jump(dwell_sec=60)
        prev = dict(self.c.position)
        await self.tick(1.0)
        self.assertAlmostEqual(self.dist(prev, self.c.position), 19 / 3.6, delta=0.02)
        status = await self.c.update({'circleSpeed': 5})
        self.assertEqual(status['circleSpeed'], 5)
        for _ in range(3):
            prev = dict(self.c.position)
            await self.tick(1.0)
            self.assertAlmostEqual(self.dist(prev, self.c.position), 5 / 3.6, delta=0.01)

    async def test_waypoint_update_in_jump_mode(self):
        await self.start_jump(loop=False)
        await self.tick(12.0)
        self.assertEqual(self.c.stop_index, 1)
        angle = self.c.angle
        moved = [dict(SHORT[0]), {'lat': 25.02, 'lon': 121.0}, dict(SHORT[2])]
        status = await self.c.update({'waypoints': moved})   # 目前這站仍存在 → 留在原站
        self.assertEqual(status['stopIndex'], 1)
        self.assertAlmostEqual(status['dwellRemaining'], 8.0)
        self.assertAlmostEqual(self.dist(self.c.position, moved[1]), 30, delta=0.01)
        self.assertAlmostEqual(self.c.angle, angle)
        await self.tick(10.0)
        self.assertEqual(self.c.stop_index, 2)
        for loop in (True, False):
            with self.subTest(loop=loop):
                await self.c.start('all', SHORT, 5, loop, mode='jump', dwell_sec=10)
                await self.tick(21.0)
                self.assertEqual(self.c.stop_index, 2)
                status = await self.c.update({'waypoints': SHORT[:2]})   # 目前這站被刪掉
                if loop:
                    self.assertEqual((status['state'], status['stopIndex']), ('running', 0))
                    self.assertAlmostEqual(self.dist(self.c.position, SHORT[0]), 30, delta=0.01)
                else:
                    self.assertEqual((status['state'], status['progress']), ('completed', 1.0))

    # ── 模式切換 ────────────────────────────────────────────────────
    async def test_switch_walk_to_jump_mid_cruise(self):
        await self.device('A', 0)
        await self.c.start('all', SHORT, 36, False, dwell_sec=10, radius_m=30)
        await self.quiesce()
        await self.tick(10.0)   # 36 km/h 走 10 秒 ≈ 100 公尺
        walk_pos = dict(self.c.position)
        self.assertAlmostEqual(self.dist(SHORT[0], walk_pos), 100, delta=0.05)
        self.assertEqual(self.c.target_index, 1)
        status = await self.c.update({'mode': 'jump'})
        self.assertEqual(status['mode'], 'jump')
        self.assertEqual((status['stopIndex'], status['target_index']), (1, 1))
        self.assertAlmostEqual(status['dwellRemaining'], 10.0)
        pos = self.c.position
        self.assertAlmostEqual(self.dist(pos, SHORT[1]), 30, delta=0.01)
        self.assertAngle(self.bearing(SHORT[1], pos), self.bearing(SHORT[1], walk_pos), 1e-6)
        await self.quiesce()
        self.assertEqual(self.last_set('A'), ('A', pos['lat'], pos['lon']))
        await self.tick(10.0)
        self.assertEqual(self.c.stop_index, 2)

    async def test_switch_walk_to_jump_past_the_end_follows_loop_rules(self):
        await self.device('A', 0)
        for loop in (True, False):
            with self.subTest(loop=loop):
                await self.c.start('all', SHORT, 5, loop)
                self.c.target_index = len(SHORT)   # 白箱：剛走到終點、尚未換圈
                status = await self.c.update({'mode': 'jump'})
                if loop:
                    self.assertEqual((status['state'], status['stopIndex']), ('running', 0))
                    self.assertAlmostEqual(self.dist(self.c.position, SHORT[0]), 30, delta=0.01)
                else:   # 行走模式在終點本來就會先判定完成
                    self.assertEqual(status['state'], 'completed')

    async def test_switch_jump_to_walk_mid_cruise(self):
        await self.start_jump()
        await self.tick(3.0)
        circle_pos = dict(self.c.position)
        status = await self.c.update({'mode': 'walk'})
        self.assertEqual(status['mode'], 'walk')
        self.assertIsNone(status['stopIndex'])
        self.assertIsNone(status['dwellRemaining'])
        self.assertEqual(status['target_index'], 1)
        self.assertEqual(self.c.position, circle_pos)   # 從目前位置出發，不跳動
        await self.tick(10.0)   # 5 km/h × 10 秒 ≈ 13.89 公尺，直走向第 2 點
        walked = self.c.position
        self.assertAlmostEqual(self.dist(circle_pos, walked), 5 / 3.6 * 10, delta=0.02)
        self.assertAlmostEqual(self.dist(walked, SHORT[1]),
                               self.dist(circle_pos, SHORT[1]) - 5 / 3.6 * 10, delta=0.02)
        progress = self.c.status()['progress']
        self.assertGreater(progress, 0)
        self.assertLess(progress, 0.05)

    async def test_switch_jump_to_walk_at_last_stop_follows_loop_rules(self):
        await self.device('A', 0)
        for loop in (True, False):
            with self.subTest(loop=loop):
                await self.c.start('all', SHORT, 5, loop, mode='jump', dwell_sec=2)
                await self.tick(5.0)
                self.assertEqual(self.c.stop_index, 2)
                status = await self.c.update({'mode': 'walk'})
                if loop:   # 既有行走規則：到終點後回到第 1 點重新走
                    self.assertEqual(status['state'], 'running')
                    self.assertEqual(self.c.position, SHORT[0])
                    self.assertEqual(status['target_index'], 1)
                else:
                    self.assertEqual((status['state'], status['progress']), ('completed', 1.0))

    # ── 驗證與 API ──────────────────────────────────────────────────
    async def test_parameter_bounds(self):
        await self.device('A', 0)
        bad = [('circle_speed', 19.5, '最多 19 km/h'), ('circle_speed', 0, '大於 0'),
               ('circle_speed', 'x', '大於 0'), ('dwell_sec', 0.5, '停留'),
               ('dwell_sec', 3601, '停留'), ('dwell_sec', True, '停留'),
               ('dwell_sec', float('nan'), '停留'), ('radius_m', -1, '半徑'),
               ('radius_m', 500.1, '半徑'), ('radius_m', 'abc', '半徑'), ('mode', 'run', '模式')]
        for key, value, message in bad:
            with self.subTest(key=key, value=value):
                with self.assertRaisesRegex(ValueError, message):
                    await self.c.start('all', SHORT, 5, **{'mode': 'jump', key: value})
        good = [('circle_speed', 19), ('circle_speed', 0.1), ('dwell_sec', 1), ('dwell_sec', 3600),
                ('radius_m', 0), ('radius_m', 500)]
        for key, value in good:
            with self.subTest(key=key, value=value):
                status = await self.c.start('all', SHORT, 5, mode='jump', **{key: value})
                self.assertEqual(status['state'], 'running')
        # 不合法的開始請求不會中斷進行中的巡航
        run_id = self.c._run_id
        with self.assertRaises(ValueError):
            await self.c.start('all', SHORT, 5, mode='jump', circle_speed=19.5)
        self.assertEqual((self.c.phase, self.c._run_id), ('running', run_id))

    async def test_routes_return_400_for_invalid_and_accept_19(self):
        await self.device('A', 0)
        base = {'deviceIdx': 'all', 'waypoints': SHORT, 'speed': 5, 'mode': 'jump'}
        response = await self.m.route_cruise_start(self.request({**base, 'circleSpeed': 19.5}))
        self.assertEqual(response.status, 400)
        self.assertIn('繞圈速度最多 19 km/h', json.loads(response.text)['error'])
        response = await self.m.route_cruise_start(self.request(
            {**base, 'circleSpeed': 19, 'dwellSec': 20, 'radiusM': 50}))
        self.assertEqual(response.status, 200)
        body = json.loads(response.text)
        self.assertEqual((body['mode'], body['circleSpeed'], body['dwellSec'], body['radiusM']),
                         ('jump', 19, 20, 50))
        for data in ({'circleSpeed': 19.5}, {'dwellSec': 0}, {'radiusM': 501},
                     {'mode': 'fly'}, {'dwellSec': None}, {'circleSpeed': 10, 'radiusM': -5}):
            with self.subTest(data=data):
                response = await self.m.route_cruise_update(self.request(data))
                self.assertEqual(response.status, 400)
                self.assertEqual((self.c.circle_speed, self.c.radius_m), (19, 50))   # 整筆拒絕
        for data in ({'circleSpeed': 12}, {'mode': 'walk'}, {'dwellSec': 30}, {'radiusM': 10}):
            with self.subTest(data=data):
                response = await self.m.route_cruise_update(self.request(data))
                self.assertEqual(response.status, 200)
        status = json.loads((await self.m.route_cruise_status(None)).text)
        self.assertEqual((status['mode'], status['circleSpeed'], status['dwellSec'],
                          status['radiusM']), ('walk', 12, 30, 10))

    async def test_start_without_mode_is_old_walk_behavior(self):
        await self.device('A', 0)
        response = await self.m.route_cruise_start(self.request(
            {'deviceIdx': 'all', 'waypoints': ROUTE, 'speed': 5}))
        self.assertEqual(response.status, 200)
        status = json.loads(response.text)
        self.assertEqual(status['mode'], 'walk')
        self.assertEqual(status['position'], ROUTE[0])
        self.assertEqual((status['target_index'], status['progress']), (1, 0.0))
        self.assertEqual((status['dwellSec'], status['radiusM'], status['circleSpeed']),
                         (15, 30, 19))
        await self.tick(10.0)
        # 與原本行走演算法逐位相同：沿直線（經緯度線性內插）前進 5 km/h × 10 秒
        move = 5 * 1000 / 3600 * 10
        ratio = move / self.m._distance_m(ROUTE[0], ROUTE[1])
        expected = {'lat': 25 + (26 - 25) * ratio, 'lon': 121 + (122 - 121) * ratio}
        self.assertEqual(self.c.position, expected)
        self.assertAlmostEqual(self.c.status()['progress'],
                               move / self.m._distance_m(ROUTE[0], ROUTE[1]))
        # 只改行走欄位的更新也維持原樣
        status = await self.c.update({'speed': 10})
        self.assertEqual((status['mode'], status['speed']), ('walk', 10))
        self.assertEqual(self.c.position, expected)

    async def test_status_fields_per_mode(self):
        await self.device('A', 0)
        for mode in ('walk', 'jump'):
            with self.subTest(mode=mode):
                status = await self.c.start('all', SHORT, 5, mode=mode)
                await self.tick(4.0)
                status = self.c.status()
                self.assertTrue(OLD_STATUS_KEYS <= set(status))
                self.assertEqual(status['mode'], mode)
                for key in ('dwellSec', 'radiusM', 'circleSpeed'):
                    self.assertIsInstance(status[key], float)
                if mode == 'walk':
                    self.assertIsNone(status['stopIndex'])
                    self.assertIsNone(status['dwellRemaining'])
                else:
                    self.assertEqual((status['stopIndex'], status['target_index']), (0, 0))
                    self.assertAlmostEqual(status['dwellRemaining'], 11.0)
                    self.assertAlmostEqual(status['progress'], (4 / 15) / 3)
                json.dumps(status)

    # ── 多支手機 ────────────────────────────────────────────────────
    async def test_all_targets_get_same_circle_position(self):
        await self.start_jump(devices='AB')
        await self.tick(2.0)
        pos = self.c.position
        self.assertEqual(self.last_set('A'), ('A', pos['lat'], pos['lon']))
        self.assertEqual(self.last_set('B'), ('B', pos['lat'], pos['lon']))

    async def test_late_joining_phone_in_sync_all_jump_gets_circle_point(self):
        for phase in ('running', 'paused'):
            with self.subTest(phase=phase):
                await self.c.stop()
                for old in list(self.m._devices.values()):
                    await self.m.teardown_device(old)
                self.m._devices.clear()
                self.m._miss_counts.clear()
                self.present = {'A', 'C'}
                await self.start_jump(devices='A')
                await self.tick(3.0)
                if phase == 'paused':
                    await self.c.pause()
                await self.quiesce()
                baseline = len(self.sets)
                late = await self.device('C', 2)
                await self.until(lambda: late.state['simulating'])
                pos = self.c.position
                self.assertAlmostEqual(self.dist(pos, SHORT[0]), 30, delta=0.01)
                self.assertNotEqual(pos, SHORT[0])
                self.assertIn(('C', pos['lat'], pos['lon']), self.sets[baseline:])
                self.assertEqual(sorted(self.c.target_udids), ['A', 'C'])
                self.assertEqual(self.c.phase, phase)

    async def test_radius_zero_stays_at_center(self):
        await self.start_jump(radius_m=0, dwell_sec=5)
        self.assertEqual(self.c.position, SHORT[0])
        for _ in range(4):
            await self.tick(1.0)
            self.assertEqual(self.c.position, SHORT[0])
        await self.tick(1.0)
        self.assertEqual(self.c.stop_index, 1)
        self.assertEqual(self.c.position, SHORT[1])
        self.assertEqual(self.last_set('A'), ('A', SHORT[1]['lat'], SHORT[1]['lon']))


if __name__ == '__main__':
    unittest.main()
