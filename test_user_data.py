"""使用者資料（收藏點／巡航路線／地圖設定）隔離測試：一律使用 tempdir，不碰真正的 user_data.json。"""
import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, call, patch

from aiohttp.test_utils import TestClient, TestServer

from test_usb_lifecycle import LifecycleHarness


class UserDataTests(LifecycleHarness):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.path = self.dir / 'user_data.json'
        self.prev = self.dir / 'user_data.json.prev'
        self.m._user_data = self.m.UserDataStore(self.path)
        self.html = self.dir / 'gps_map.html'
        self.html.write_text('<!doctype html><title>測試地圖</title>', encoding='utf-8')
        self.m._MAP_HTML_PATH = self.html
        # 測試不得改到真正的 launcher 資料夾。
        self.real = Path(self.m.__file__).with_name('user_data.json')
        self.real_before = self.real.exists()
        self.client = TestClient(TestServer(self.m._build_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.assertEqual(self.real.exists(), self.real_before)
        await super().asyncTearDown()

    async def get(self):
        response = await self.client.get('/userdata')
        return response.status, await response.json()

    async def post(self, data=None, raw=None):
        if raw is not None:
            response = await self.client.post('/userdata', data=raw,
                                              headers={'Content-Type': 'application/json'})
        else:
            response = await self.client.post('/userdata', json=data)
        return response.status, await response.json()

    async def test_missing_file_returns_empty_data(self):
        status, body = await self.get()
        self.assertEqual(status, 200)
        self.assertEqual(body, {'ok': True, 'exists': False, 'data': {
            'version': 1, 'updatedAt': None,
            'favorites': [], 'cruiseRoutes': [], 'mapSettings': {}}})
        self.assertFalse(self.path.exists())

    async def test_partial_update_keeps_other_fields_and_ignores_unknown(self):
        favorites = [{'name': '台北 101', 'lat': 25.0339, 'lon': 121.5645}]
        status, body = await self.post({'favorites': favorites, 'evil': 1, 'version': 99})
        self.assertEqual(status, 200)
        self.assertTrue(body['ok'])
        self.assertTrue(body['updatedAt'].endswith('Z'))
        routes = [{'name': '河濱', 'waypoints': [{'lat': 25, 'lon': 121}]}]
        await self.post({'cruiseRoutes': routes})
        status, body = await self.post({'mapSettings': {'zoom': 15}})
        status, got = await self.get()
        self.assertEqual(got['ok'], True)
        self.assertTrue(got['exists'])
        self.assertEqual(got['data']['favorites'], favorites)
        self.assertEqual(got['data']['cruiseRoutes'], routes)
        self.assertEqual(got['data']['mapSettings'], {'zoom': 15})
        self.assertEqual(got['data']['version'], 1)
        self.assertEqual(got['data']['updatedAt'], body['updatedAt'])
        stored = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertNotIn('evil', stored)
        self.assertEqual(stored['version'], 1)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.dir.glob('.user_data*.tmp')), [])

    async def test_prev_keeps_one_previous_generation(self):
        await self.post({'favorites': [{'name': '第一版'}]})
        self.assertFalse(self.prev.exists())  # 第一次寫入前沒有舊檔
        first = self.path.read_bytes()
        await self.post({'favorites': []})  # 前端誤刪
        self.assertEqual(self.prev.read_bytes(), first)
        self.assertEqual(json.loads(self.prev.read_text(encoding='utf-8'))['favorites'], [{'name': '第一版'}])
        self.assertEqual(self.prev.stat().st_mode & 0o777, 0o600)
        second = self.path.read_bytes()
        await self.post({'mapSettings': {'layer': 'osm'}})
        self.assertEqual(self.prev.read_bytes(), second)  # 只保留一代

    async def test_corrupt_file_get_fails_and_post_never_overwrites(self):
        for raw in ('{壞檔', '[]', '{"version":2,"favorites":[]}', '{"version":true}',
                    '{"version":1,"favorites":{}}', '{"version":1,"cruiseRoutes":[1]}',
                    '{"version":1,"mapSettings":[]}', b'\xff\xfe'):
            with self.subTest(raw=raw):
                if isinstance(raw, bytes):
                    self.path.write_bytes(raw)
                else:
                    self.path.write_text(raw, encoding='utf-8')
                before = self.path.read_bytes()
                status, body = await self.get()
                self.assertEqual(status, 500)
                self.assertFalse(body['ok'])
                self.assertTrue(body['error'].startswith('使用者資料檔損壞'))
                status, body = await self.post({'favorites': []})
                self.assertEqual(status, 500)
                self.assertFalse(body['ok'])
                self.assertTrue(body['error'].startswith('使用者資料檔損壞'))
                self.assertEqual(self.path.read_bytes(), before)
                self.assertFalse(self.prev.exists())
                self.assertEqual(list(self.dir.glob('.user_data*.tmp')), [])

    async def test_type_errors_and_bad_bodies_return_ok_false(self):
        await self.post({'favorites': [{'name': '保留'}]})
        before = self.path.read_bytes()
        for data in ({'favorites': {}}, {'favorites': [1]}, {'favorites': ['a']},
                     {'cruiseRoutes': 'x'}, {'cruiseRoutes': [[]]}, {'mapSettings': []},
                     {'mapSettings': None}, [], 'x', 3, {}, {'unknown': 1}):
            with self.subTest(data=data):
                status, body = await self.post(data)
                self.assertEqual(status, 400)
                self.assertFalse(body['ok'])
                self.assertTrue(body['error'])
        for raw in ('{', b'\xff'):
            with self.subTest(raw=raw):
                status, body = await self.post(raw=raw)
                self.assertEqual(status, 400)
                self.assertFalse(body['ok'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.prev.exists())

    async def test_body_over_5mb_rejected(self):
        big = json.dumps({'favorites': [{'note': 'x' * (5 * 1024 * 1024)}]})
        status, body = await self.post(raw=big)
        self.assertEqual(status, 413)
        self.assertFalse(body['ok'])
        self.assertFalse(self.path.exists())
        # 接近上限但未超過仍可保存（不受 aiohttp 預設 1MB 限制）。
        ok = json.dumps({'favorites': [{'note': 'y' * (2 * 1024 * 1024)}]})
        status, body = await self.post(raw=ok)
        self.assertEqual(status, 200)
        self.assertTrue(body['ok'])

    async def test_replace_failure_preserves_original_and_cleans_temp(self):
        await self.post({'favorites': [{'name': '原檔'}]})
        before = self.path.read_bytes()
        with patch.object(self.m.os, 'replace', side_effect=PermissionError('不可寫入')):
            status, body = await self.post({'favorites': []})
        self.assertEqual(status, 500)
        self.assertFalse(body['ok'])
        self.assertIn('無法保存使用者資料', body['error'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.dir.glob('.user_data*.tmp')), [])

    async def test_unreadable_file_gives_clear_error_and_post_refuses(self):
        await self.post({'favorites': [{'name': '原檔'}]})
        before = self.path.read_bytes()
        original = Path.read_bytes
        def deny(path):
            if Path(path) == self.path:
                raise PermissionError('權限不足')
            return original(path)
        with patch.object(Path, 'read_bytes', deny):
            status, body = await self.get()
            self.assertEqual(status, 500)
            self.assertFalse(body['ok'])
            self.assertIn('沒有權限讀取 user_data.json，請用 sudo 啟動 launcher', body['error'])
            status, body = await self.post({'favorites': []})
            self.assertEqual(status, 500)
            self.assertFalse(body['ok'])
            self.assertIn('沒有權限讀取 user_data.json', body['error'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.prev.exists())

    async def test_concurrent_partial_updates_both_kept(self):
        await asyncio.gather(self.post({'favorites': [{'name': '甲'}]}),
                             self.post({'cruiseRoutes': [{'name': '乙'}]}),
                             self.post({'mapSettings': {'zoom': 3}}))
        _, got = await self.get()
        self.assertEqual(got['data']['favorites'], [{'name': '甲'}])
        self.assertEqual(got['data']['cruiseRoutes'], [{'name': '乙'}])
        self.assertEqual(got['data']['mapSettings'], {'zoom': 3})

    async def test_sudo_chown_hands_files_back_to_user(self):
        chown = Mock()
        with patch.dict(os.environ, {'SUDO_UID': '501', 'SUDO_GID': '20'}), \
                patch.object(self.m.os, 'geteuid', return_value=0), \
                patch.object(self.m.os, 'chown', chown):
            await self.post({'favorites': [{'name': '一'}]})
            self.assertEqual(chown.call_args_list, [call(self.path, 501, 20)])
            chown.reset_mock()
            await self.post({'favorites': [{'name': '二'}]})
            self.assertEqual(sorted(map(str, (c.args[0] for c in chown.call_args_list))),
                             sorted(map(str, (self.path, self.prev))))
            self.assertTrue(all(c.args[1:] == (501, 20) for c in chown.call_args_list))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.prev.stat().st_mode & 0o777, 0o600)

    async def test_no_chown_without_sudo_env_or_root(self):
        cases = ({'env': {}, 'euid': 0},                                   # 不是透過 sudo
                 {'env': {'SUDO_UID': '501', 'SUDO_GID': '20'}, 'euid': 501})  # 不是 root
        for case in cases:
            with self.subTest(case=case):
                chown = Mock()
                env = {k: v for k, v in os.environ.items() if k not in ('SUDO_UID', 'SUDO_GID')}
                env.update(case['env'])
                with patch.dict(os.environ, env, clear=True), \
                        patch.object(self.m.os, 'geteuid', return_value=case['euid']), \
                        patch.object(self.m.os, 'chown', chown):
                    status, body = await self.post({'mapSettings': {'case': case['euid']}})
                self.assertEqual(status, 200)
                chown.assert_not_called()
                self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    async def test_chown_or_chmod_failure_only_warns_and_save_succeeds(self):
        for env in ({'SUDO_UID': '501', 'SUDO_GID': '20'}, {'SUDO_UID': '不是數字', 'SUDO_GID': '20'}):
            with self.subTest(env=env):
                with patch.dict(os.environ, env), \
                        patch.object(self.m.os, 'geteuid', return_value=0), \
                        patch.object(self.m.os, 'chown', side_effect=PermissionError('不可 chown')), \
                        self.assertLogs('launcher', 'WARNING') as logs:
                    status, body = await self.post({'favorites': [{'name': str(env)}]})
                self.assertEqual(status, 200)
                self.assertTrue(body['ok'])
                self.assertTrue(any('不影響存檔' in line for line in logs.output))
                _, got = await self.get()
                self.assertEqual(got['data']['favorites'], [{'name': str(env)}])
        with patch.object(self.m.os, 'chmod', side_effect=OSError('不可 chmod')), \
                self.assertLogs('launcher', 'WARNING'):
            status, body = await self.post({'mapSettings': {'chmod': False}})
        self.assertEqual(status, 200)
        self.assertTrue(body['ok'])

    async def test_root_and_map_serve_only_the_map_html(self):
        for url in ('/', '/map'):
            with self.subTest(url=url):
                response = await self.client.get(url)
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers['Content-Type'], 'text/html; charset=utf-8')
                self.assertIn('測試地圖', await response.text())
        for url in ('/gps_launcher.py', '/map/../gps_launcher.py', '/%2e%2e/gps_launcher.py',
                    '/user_data.json', '/map/user_data.json', '/?file=gps_launcher.py'):
            with self.subTest(url=url):
                response = await self.client.get(url)
                text = await response.text()
                self.assertNotIn('import asyncio', text)
                self.assertNotIn('favorites', text)
                if url.startswith('/?'):
                    self.assertIn('測試地圖', text)  # 查詢參數被忽略，仍只回地圖頁
                else:
                    self.assertEqual(response.status, 404)
        self.html.unlink()
        response = await self.client.get('/')
        self.assertEqual(response.status, 404)
        self.assertFalse((await response.json())['ok'])

    async def test_allowed_origins_null_and_localhost_work_without_star(self):
        for origin in (None, 'null', 'http://localhost:8090', 'http://127.0.0.1:8090'):
            with self.subTest(origin=origin):
                headers = {} if origin is None else {'Origin': origin}
                response = await self.client.options('/userdata', headers=headers)
                self.assertEqual(response.status, 200)
                self.assertIn('POST', response.headers['Access-Control-Allow-Methods'])
                self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), origin)
                response = await self.client.post('/userdata', json={'mapSettings': {'o': origin}},
                                                  headers=headers)
                self.assertEqual(response.status, 200)
                self.assertTrue((await response.json())['ok'])
                self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), origin)
                response = await self.client.get('/userdata', headers=headers)
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())['data']['mapSettings'], {'o': origin})
                self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), origin)
                self.assertNotEqual(response.headers.get('Access-Control-Allow-Origin'), '*')

    async def test_evil_origin_or_rebound_host_is_rejected(self):
        await self.post({'favorites': [{'name': '住家', 'apiKey': '秘密'}]})
        before = self.path.read_bytes()
        cases = [{'Origin': o} for o in (
            'https://evil.example', 'http://localhost:8091', 'http://localhost',
            'http://127.0.0.1:8090.evil.example', 'https://localhost:8090', 'file://')]
        cases.append({'Host': 'evil.example:8090'})  # DNS rebinding：同源 GET 不帶 Origin
        cases.append({'Host': 'evil.example:8090', 'Origin': 'http://evil.example:8090'})
        for headers in cases:
            with self.subTest(headers=headers):
                for method in ('GET', 'POST', 'OPTIONS'):
                    kwargs = {'json': {'favorites': []}} if method == 'POST' else {}
                    response = await self.client.request(method, '/userdata', headers=headers, **kwargs)
                    self.assertEqual(response.status, 403)
                    self.assertNotIn('Access-Control-Allow-Origin', response.headers)
                    if method != 'OPTIONS':
                        body = await response.json()
                        self.assertEqual(body, {'ok': False, 'error': '不允許的來源'})
                        self.assertNotIn('住家', json.dumps(body, ensure_ascii=False))
        self.assertEqual(self.path.read_bytes(), before)

    async def test_post_requires_application_json_content_type(self):
        await self.post({'favorites': [{'name': '原檔'}]})
        before = self.path.read_bytes()
        body = json.dumps({'favorites': []})
        for content_type in ('text/plain', 'text/plain;charset=UTF-8',
                             'application/x-www-form-urlencoded', 'multipart/form-data; boundary=x', None):
            with self.subTest(content_type=content_type):
                headers = {'Origin': 'null'}
                if content_type is not None:
                    headers['Content-Type'] = content_type
                response = await self.client.post('/userdata', data=body.encode(), headers=headers,
                                                  skip_auto_headers=['Content-Type'])
                self.assertEqual(response.status, 415)
                self.assertFalse((await response.json())['ok'])
                self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), 'null')
        self.assertEqual(self.path.read_bytes(), before)
        response = await self.client.post('/userdata', data=body.encode(),
                                          headers={'Content-Type': 'application/json; charset=utf-8'})
        self.assertEqual(response.status, 200)

    async def test_other_endpoints_keep_star_cors(self):
        for method, url in (('GET', '/devices'), ('OPTIONS', '/cruise/start'), ('GET', '/cruise/status')):
            with self.subTest(url=url):
                response = await self.client.request(method, url, headers={'Origin': 'https://evil.example'})
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers['Access-Control-Allow-Origin'], '*')

    async def test_module_import_does_not_touch_real_file(self):
        store = self.m.UserDataStore(self.dir / 'never.json')
        self.assertFalse((self.dir / 'never.json').exists())
        self.assertEqual(store.read(), (False, store.empty()))


if __name__ == '__main__':
    unittest.main()
