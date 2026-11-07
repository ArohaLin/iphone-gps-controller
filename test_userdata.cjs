// 執行 node test_userdata.cjs；所有請求均由 mock 接管，不使用網路、不連手機。
// 驗證：最愛／巡航路線／地圖設定改存 Launcher（/userdata），localStorage 只當離線快取。
const assert = require('node:assert/strict');
const { setup, reply, deferred } = require('./test_connection_mode.cjs');

function memoryStorage(initial) {
  const data = new Map(Object.entries(initial));
  return {
    getItem: k => (data.has(k) ? data.get(k) : null),
    setItem: (k, v) => { data.set(k, String(v)); },
    removeItem: k => { data.delete(k); },
  };
}
function fixture(initial = {}) {
  const t = setup();
  const stored = Object.fromEntries(Object.entries(initial).map(([k, v]) => [k, typeof v === 'string' ? v : JSON.stringify(v)]));
  t.storage = memoryStorage(stored);
  Object.assign(t.context, {
    localStorage: t.storage, confirm: () => true, prompt: () => null, requestAnimationFrame: () => {},
  });
  t.json = expr => JSON.parse(t.run(`JSON.stringify(${expr})`));
  t.cached = key => JSON.parse(t.storage.getItem(key));
  const isUserdata = c => /\/userdata$/.test(c.url);
  t.posts = () => t.calls.filter(c => isUserdata(c) && c.options && c.options.method === 'POST').map(c => JSON.parse(c.options.body));
  t.gets = () => t.calls.filter(c => isUserdata(c) && !(c.options && c.options.method === 'POST'));
  t.settle = async () => { await t.run('_udSyncing || Promise.resolve()'); await t.run('_udFlushing || Promise.resolve()'); };
  return t;
}
const FAVS = [{ id: 1, name: '台北車站', lat: 25.0478, lon: 121.517, country: '' }];
const ROUTES = [{ id: 11, name: '河濱', waypoints: [{ lat: 25, lon: 121 }, { lat: 25.01, lon: 121.01 }], speed: 5, loop: false, savedAt: '2026-11-01T00:00:00.000Z' }];
// 回應內容一律深拷貝，避免測試之間共用同一個物件
const remote = (data, exists = true) => reply(JSON.parse(JSON.stringify({ ok: true, exists, data: { version: 1, updatedAt: '2026-11-07T00:00:00Z', favorites: [], cruiseRoutes: [], mapSettings: {}, ...data } })));
const okPost = () => reply({ ok: true, updatedAt: '2026-11-07T00:00:01Z' });
const offlineToasts = t => t.toasts.filter(x => /Launcher 未連線（或版本較舊）/.test(x)).length;

async function main() {
  // 1. 遷移：後端從沒存過＋瀏覽器有舊資料 → 全部上傳並提示
  let t = fixture({ gps_favorites_v1: FAVS, gps_cruise_routes_v1: ROUTES, gps_map_settings_v1: { provider: 'osm' } });
  t.queue.push(remote({}, false), okPost());
  assert.equal(await t.context.initUserData(), true);
  assert.equal(t.gets().length, 1);
  assert.equal(t.posts().length, 1);
  assert.deepEqual(t.posts()[0], { favorites: FAVS, cruiseRoutes: ROUTES, mapSettings: { provider: 'osm' } });
  assert.ok(t.toasts.includes('已把瀏覽器裡的資料搬到電腦檔案'));
  assert.equal(t.storage.getItem('gps_userdata_dirty'), null);
  assert.ok(t.storage.getItem('gps_userdata_synced'));
  assert.equal(t.run('_udState'), 'synced');
  console.log('通過：exists:false＋瀏覽器有資料 → POST 遷移、提示、清 dirty');

  // 2. 舊版 Launcher（404）→ 離線：資料留在記憶體與快取、記 dirty、只提示一次、不送 POST
  t = fixture({ gps_favorites_v1: FAVS, gps_cruise_routes_v1: ROUTES, gps_userdata_synced: '2026-11-01' });
  t.queue.push(reply({}, 404));
  assert.equal(await t.context.initUserData(), false);
  assert.equal(t.run('_udState'), 'offline');
  assert.equal(t.toasts.length, 0, '讀取失敗本身不跳提示');
  t.context.addFavorite(24.1477, 120.6736, '台中', '台灣');
  t.context.addFavorite(22.6273, 120.3014, '高雄', '台灣');
  assert.equal(t.calls.length, 1, '離線時存檔不送請求');
  assert.deepEqual(t.json('_favs.map(f => f.name)'), ['台北車站', '台中', '高雄']);
  assert.deepEqual(t.cached('gps_favorites_v1').map(f => f.name), ['台北車站', '台中', '高雄']);
  assert.deepEqual(t.cached('gps_userdata_dirty'), ['favorites']);
  assert.equal(offlineToasts(t), 1, '同一次開頁只提示一次');
  console.log('通過：後端 404 → 設 dirty、資料仍在記憶體與快取、提示一次、不 POST');

  // 3. dirty 且後端恢復 → 推送 dirty 區塊並清旗標；其餘區塊以後端為準
  t.queue.push(reply([]));
  await t.context.pollDevices();
  assert.equal(t.gets().length, 1, 'Launcher 一直在線（舊版）時，一分鐘內不重試');
  t.queue.push(new TypeError('Load failed'));
  await t.context.pollDevices();                        // Launcher 重啟中
  const remoteRoutes = [{ ...ROUTES[0], id: 12, name: '後端路線' }];
  t.queue.push(reply([]), remote({ favorites: [{ id: 99, name: '後端舊最愛', lat: 1, lon: 2, country: '' }], cruiseRoutes: remoteRoutes }), okPost());
  await t.context.pollDevices();                        // 搭現有輪詢恢復，不另開定時器
  await t.settle();
  assert.equal(t.gets().length, 2);
  assert.equal(t.posts().length, 1);
  assert.deepEqual(Object.keys(t.posts()[0]), ['favorites']);
  assert.deepEqual(t.posts()[0].favorites.map(f => f.name), ['台北車站', '台中', '高雄']);
  assert.deepEqual(t.json('_cruiseRoutes.map(r => r.name)'), ['後端路線']);
  assert.deepEqual(t.cached('gps_cruise_routes_v1').map(r => r.name), ['後端路線']);
  assert.equal(t.storage.getItem('gps_userdata_dirty'), null);
  assert.equal(t.run('_udState'), 'synced');
  assert.ok(t.toasts.includes('已把離線期間的變更同步到電腦檔案'));
  console.log('通過：dirty＋後端恢復 → 推送 dirty 區塊、清旗標、未改區塊以後端為準');

  // 3b. 從未和 Launcher 對齊過的瀏覽器離線改資料 → 保守合併，不洗掉後端既有資料
  t = fixture({ gps_favorites_v1: [{ id: 7, name: '本機新增', lat: 3, lon: 4, country: '' }], gps_userdata_dirty: ['favorites'] });
  t.queue.push(remote({ favorites: FAVS }), okPost());
  await t.context.initUserData();
  assert.deepEqual(t.posts()[0].favorites.map(f => f.name), ['台北車站', '本機新增']);
  console.log('通過：未對齊過的瀏覽器帶 dirty 上線 → 合併而非覆蓋後端');

  // 4. 後端 exists:true 且非 dirty → 以後端為準覆蓋快取，不 POST
  t = fixture({ gps_favorites_v1: FAVS, gps_cruise_routes_v1: ROUTES, gps_userdata_synced: 'x' });
  t.queue.push(remote({ favorites: [{ id: 5, name: '後端地點', lat: 10, lon: 20, country: '' }], cruiseRoutes: [], mapSettings: { provider: 'osm', googleApiKey: 'FAKE-KEY-FOR-TEST' } }));
  await t.context.initUserData();
  assert.equal(t.posts().length, 0);
  assert.deepEqual(t.json('_favs.map(f => f.name)'), ['後端地點']);
  assert.deepEqual(t.cached('gps_favorites_v1').map(f => f.name), ['後端地點']);
  assert.deepEqual(t.cached('gps_cruise_routes_v1'), []);
  assert.equal(t.run('_mapSettings.googleApiKey'), 'FAKE-KEY-FOR-TEST');
  assert.equal(t.element('cruise-fav-select').disabled, true);
  console.log('通過：exists:true 且非 dirty → 以後端為準覆蓋本地快取，不送 POST');

  // 5. 載入失敗（網路、ok:false、500、格式錯）絕不用空陣列或任何資料 POST
  for (const failure of [new TypeError('Load failed'), reply({ ok: false, error: '讀取失敗' }), reply({}, 500),
    reply({ ok: true, exists: true, data: { favorites: null, cruiseRoutes: [] } }), reply({ ok: true })]) {
    for (const initial of [{}, { gps_favorites_v1: FAVS, gps_userdata_synced: 'x' }]) {
      t = fixture(initial);
      t.queue.push(failure);
      await t.context.initUserData();
      assert.equal(t.run('_udState'), 'offline');
      assert.equal(t.posts().length, 0);
      assert.equal(t.storage.getItem('gps_favorites_v1'), initial.gps_favorites_v1 ? JSON.stringify(FAVS) : null, '快取不被改動');
      assert.equal(t.run('_favs.length'), initial.gps_favorites_v1 ? 1 : 0);
    }
  }
  t = fixture();                                         // 全新、什麼都沒有 → 後端 exists:false 也不送空資料
  t.queue.push(remote({}, false));
  await t.context.initUserData();
  assert.equal(t.posts().length, 0);
  t = fixture({ gps_favorites_v1: FAVS, gps_userdata_synced: 'x' });   // 使用者明確「清除全部」仍會存檔
  t.queue.push(remote({ favorites: FAVS }), okPost());
  await t.context.initUserData();
  t.context.clearAllFavs();
  await t.settle();
  assert.deepEqual(t.posts(), [{ favorites: [] }]);
  console.log('通過：載入失敗不 POST、不改快取；空資料不遷移；「清除全部」仍照常存檔');

  // 6. 巡航路線：改名／覆蓋／同名詢問／下拉套用／刪除 → POST 內容正確
  t = fixture({ gps_cruise_routes_v1: ROUTES, gps_userdata_synced: 'x' });
  t.queue.push(remote({ cruiseRoutes: ROUTES }));
  await t.context.initUserData();
  assert.match(t.element('cruise-fav-select').innerHTML, /我的巡航路線（1 條）[\s\S]*河濱（2 點）/);
  t.context.prompt = () => '  河濱夜跑  ';
  t.queue.push(okPost());
  t.context.renameCruiseRoute(11);
  await t.settle();
  assert.deepEqual(Object.keys(t.posts().at(-1)), ['cruiseRoutes']);
  assert.equal(t.posts().at(-1).cruiseRoutes[0].name, '河濱夜跑');
  assert.equal(t.posts().at(-1).cruiseRoutes[0].id, 11);
  assert.match(t.element('cruise-fav-select').innerHTML, /河濱夜跑/);

  t.run('wpData = [{lat: 1, lon: 2}, {lat: 3, lon: 4}, {lat: 5, lon: 6}]; state.speed = 12; state.loop = true;');
  t.queue.push(okPost());
  t.context.overwriteCruiseRoute(11);
  await t.settle();
  let saved = t.posts().at(-1).cruiseRoutes;
  assert.equal(saved.length, 1);
  assert.deepEqual(saved[0].waypoints, [{ lat: 1, lon: 2 }, { lat: 3, lon: 4 }, { lat: 5, lon: 6 }]);
  assert.equal(saved[0].speed, 12); assert.equal(saved[0].loop, true);
  assert.equal(saved[0].name, '河濱夜跑'); assert.equal(saved[0].id, 11);

  const postCount = t.posts().length;
  t.element('csm-name').value = '河濱夜跑';
  t.context.confirmSaveCruise();
  assert.equal(t.element('csm-dup').style.display, 'flex', '同名時先詢問');
  assert.match(t.element('csm-dup-msg').textContent, /已有名為「河濱夜跑」/);
  assert.equal(t.posts().length, postCount, '詢問時不存檔');
  t.queue.push(okPost());
  t.context.confirmSaveCruise('new');
  await t.settle();
  saved = t.posts().at(-1).cruiseRoutes;
  assert.deepEqual(saved.map(r => r.name), ['河濱夜跑', '河濱夜跑 (2)']);
  const newId = saved[1].id;
  t.run('wpData = [{lat: 7, lon: 8}, {lat: 9, lon: 10}]');
  t.queue.push(okPost());
  t.element('csm-name').value = '河濱夜跑';
  t.context.confirmSaveCruise('overwrite');
  await t.settle();
  saved = t.posts().at(-1).cruiseRoutes;
  assert.equal(saved.length, 2, '覆蓋不新增');
  assert.deepEqual(saved[0].waypoints, [{ lat: 7, lon: 8 }, { lat: 9, lon: 10 }]);

  t.context.L = { latLngBounds: x => x };
  t.run('map = { fitBounds() {} }; addWaypoint = (lat, lon) => wpData.push({ lat, lon });');
  const select = t.element('cruise-fav-select');
  select.value = String(newId);
  t.queue.push(okPost());
  assert.equal(await t.context.onCruiseFavSelect(select), undefined);
  await t.settle();
  assert.deepEqual(t.posts().at(-1), { mapSettings: { lastCruiseRouteId: newId } });
  assert.deepEqual(t.json('wpData'), [{ lat: 1, lon: 2 }, { lat: 3, lon: 4 }, { lat: 5, lon: 6 }]);
  assert.equal(select.value, String(newId), '下拉選單記住上次套用的路線');

  t.run('state.cruiseActive = true');
  t.context.confirm = () => false;
  const before = t.calls.length;
  select.value = '11';
  await t.context.onCruiseFavSelect(select);
  assert.equal(t.calls.length, before, '取消時不停止巡航、不存檔');
  assert.equal(select.value, String(newId), '取消後還原選取');
  t.run('state.cruiseActive = false');

  t.context.confirm = () => true;
  t.queue.push(okPost(), okPost());
  t.context.deleteCruiseRoute(newId);
  await t.settle();
  const [deleteMeta, deleteRoutes] = t.posts().slice(-2);
  assert.deepEqual(deleteMeta, { mapSettings: { lastCruiseRouteId: null } });
  assert.deepEqual(deleteRoutes.cruiseRoutes.map(r => r.id), [11]);
  assert.equal(select.value, '');
  console.log('通過：巡航改名／覆蓋／同名詢問（覆蓋或另存）／下拉套用與取消／刪除，POST 內容正確');

  // 7. 存檔依序送出：前一個 POST 未回來時，下一次只排隊，最後送出的是最新內容
  t = fixture({ gps_userdata_synced: 'x' });
  t.queue.push(remote({}));
  await t.context.initUserData();
  const slow = deferred();
  t.queue.push(slow.promise);
  t.context.addFavorite(1, 1, '甲', 'X');
  t.context.addFavorite(2, 2, '乙', 'X');
  assert.equal(t.posts().length, 1);
  t.queue.push(okPost());
  slow.resolve(okPost());
  await t.settle();
  assert.deepEqual(t.posts().map(p => p.favorites.map(f => f.name)), [['甲'], ['甲', '乙']]);
  assert.equal(t.storage.getItem('gps_userdata_dirty'), null);
  const postCall = t.calls.find(c => c.options && c.options.method === 'POST');
  assert.equal(postCall.options.headers['Content-Type'], 'application/json');
  const callsBefore = t.calls.length;
  assert.equal(await t.context.saveUserData(), false);
  assert.equal(await t.context.saveUserData('unknown'), false);
  assert.equal(t.calls.length, callsBefore, '不送空的 {} 更新');
  console.log('通過：連續存檔不並行、不亂序，最後一次是最新內容；帶 JSON Content-Type、不送空更新');

  // 7b. Launcher 在線但寫檔失敗（500 等）：顯示後端 error、保留 dirty、不當成舊版；下次存檔一併補送
  t = fixture({ gps_userdata_synced: 'x' });
  t.queue.push(remote({}));
  await t.context.initUserData();
  t.queue.push(reply({ ok: false, error: '寫入資料檔失敗：沒有權限' }, 500));
  t.context.addFavorite(1, 1, '甲', 'X');
  await t.settle();
  assert.match(t.toasts.at(-1), /存到電腦檔案失敗：寫入資料檔失敗：沒有權限/);
  assert.equal(offlineToasts(t), 0, '500 不可當成舊版／未連線');
  assert.equal(t.run('_udState'), 'synced');
  assert.deepEqual(t.cached('gps_userdata_dirty'), ['favorites']);
  t.queue.push(okPost());
  t.run('_cruiseRoutes = [{ id: 3, name: "乙路線", waypoints: [], speed: 5, loop: false }]');
  t.context.saveUserData('cruiseRoutes');
  await t.settle();
  assert.deepEqual(Object.keys(t.posts().at(-1)).sort(), ['cruiseRoutes', 'favorites'], '先前失敗的區塊一併補送');
  assert.equal(t.storage.getItem('gps_userdata_dirty'), null);
  for (const [status, error] of [[400, '欄位型別錯誤'], [403, '來源不允許'], [413, '資料超過 5MB']]) {
    t.queue.push(reply({ ok: false, error }, status));
    t.context.saveUserData('favorites');
    await t.settle();
    assert.match(t.toasts.at(-1), new RegExp(error));
  }

  // 7c. GET 500（資料檔損壞）：不可當成 exists:false 去覆寫；顯示 error；存檔只記 dirty
  t = fixture({ gps_favorites_v1: FAVS, gps_cruise_routes_v1: ROUTES });
  t.queue.push(reply({ ok: false, error: '資料檔損壞，請從備份還原' }, 500));
  await t.context.initUserData();
  assert.equal(t.posts().length, 0);
  assert.equal(t.run('_udState'), 'offline');
  assert.match(t.toasts.at(-1), /讀不到電腦裡的資料檔：資料檔損壞，請從備份還原/);
  t.context.addFavorite(5, 5, '丙', 'X');
  assert.equal(t.posts().length, 0);
  assert.deepEqual(t.cached('gps_userdata_dirty'), ['favorites']);
  assert.equal(t.toasts.filter(x => /資料檔損壞/.test(x)).length, 1, '同一錯誤只提示一次');
  console.log('通過：POST 4xx/500 顯示後端訊息並保留 dirty；GET 500 不當成舊版、不覆寫後端');

  // 8. 匯出不含 Google 金鑰；匯入讀得了舊版 _ver:1，且寫回後端
  t = fixture({ gps_favorites_v1: FAVS, gps_cruise_routes_v1: ROUTES, gps_userdata_synced: 'x',
    gps_map_settings_v1: { provider: 'google-hybrid', googleApiKey: 'FAKE-KEY-FOR-TEST', lastCruiseRouteId: 11 } });
  t.queue.push(reply({}, 404));
  await t.context.initUserData();
  const backup = t.json('buildBackupData()');
  assert.equal('googleApiKey' in backup.mapSettings, false);
  assert.deepEqual(backup.mapSettings, { provider: 'google-hybrid', lastCruiseRouteId: 11 });
  assert.deepEqual(backup.favorites, FAVS); assert.deepEqual(backup.cruiseRoutes, ROUTES);
  let blobText = '';
  t.context.Blob = class { constructor(parts) { blobText = parts.join(''); } };
  t.context.URL = { createObjectURL: () => 'blob:test', revokeObjectURL() {} };
  t.run('document.createElement = () => ({ click() {}, remove() {} }); document.body = { appendChild() {} };');
  t.context.exportBackup();
  assert.doesNotMatch(blobText, /FAKE-KEY|googleApiKey/);
  assert.match(blobText, /"_ver": 2/);

  t = fixture({ gps_userdata_synced: 'x', gps_map_settings_v1: { googleApiKey: 'FAKE-KEY-FOR-TEST' } });
  t.queue.push(remote({ mapSettings: { googleApiKey: 'FAKE-KEY-FOR-TEST' } }));
  await t.context.initUserData();
  const v1 = { _type: 'gps_map_backup', _ver: 1, gps_favorites_v1: JSON.stringify(FAVS), gps_cruise_routes_v1: null };
  const parsedV1 = t.run(`parseBackupData(${JSON.stringify(v1)})`);
  assert.equal(parsedV1.cruiseRoutes, null);
  t.queue.push(okPost());
  assert.equal(await t.context.applyBackupData(parsedV1), true);
  assert.deepEqual(t.posts().at(-1), { favorites: FAVS });
  const v2 = { _type: 'gps_map_backup', _ver: 2, favorites: [], cruiseRoutes: ROUTES, mapSettings: { provider: 'osm', googleApiKey: 'OTHER-KEY' } };
  t.queue.push(okPost());
  assert.equal(await t.context.applyBackupData(t.run(`parseBackupData(${JSON.stringify(v2)})`)), true);
  const imported = t.posts().at(-1);
  assert.deepEqual(imported.cruiseRoutes, ROUTES);
  assert.deepEqual(imported.mapSettings, { provider: 'osm', googleApiKey: 'FAKE-KEY-FOR-TEST' }, '備份檔裡的金鑰不帶入');
  assert.equal(t.run('parseBackupData({ _type: "other" })'), null);
  console.log('通過：匯出不含 googleApiKey；匯入相容 _ver:1 與 _ver:2 並寫回後端');

  // 9. 底圖：沒金鑰不壞頁面、選 OSM 不碰 Google、Google API 只載入一次、金鑰無效／載入失敗切回 OSM
  t = fixture();
  assert.equal(await t.context.setBaseMap('google-roadmap'), false);
  assert.match(t.element('gkey-msg').textContent, /請先貼上/);
  assert.equal(t.element('basemap-select').value, 'osm');
  assert.equal(t.calls.length, 0);
  t.run(`_mapSettings.googleApiKey = 'FAKE-KEY-FOR-TEST'`);
  assert.equal(await t.context.setBaseMap('google-roadmap'), false);
  assert.match(t.toasts.at(-1), /Google 圖層元件/);
  const scripts = [];
  t.context.recordScript = s => scripts.push(s);
  t.context.L = { gridLayer: { googleMutant: () => ({}) } };
  t.run('document.createElement = tag => { const s = { tag, remove() {} }; recordScript(s); return s; }; document.head = { appendChild() {} };');
  assert.equal(await t.context.setBaseMap('osm'), true);
  assert.equal(scripts.length, 0, '選 OSM 完全不載入 Google');
  const loading = t.context.setBaseMap('google-hybrid');
  assert.equal(scripts.length, 1);
  assert.match(scripts[0].src, /^https:\/\/maps\.googleapis\.com\/maps\/api\/js\?key=FAKE-KEY-FOR-TEST&loading=async&callback=__gpsGoogleMapsReady$/);
  t.run('window.__gpsGoogleMapsReady()');
  assert.equal(await loading, true);
  assert.equal(t.run('_mapSettings.provider'), 'google-hybrid');
  assert.equal(await t.context.setBaseMap('google-roadmap'), true);
  assert.equal(scripts.length, 1, 'Google Maps API 只載入一次');
  t.run('window.gm_authFailure()');
  assert.match(t.toasts.at(-1), /Google 金鑰無效或未啟用 Maps JavaScript API/);
  assert.equal(t.run('_mapSettings.provider'), 'osm');
  assert.equal(t.element('basemap-select').value, 'osm');
  assert.equal(await t.context.setBaseMap('google-roadmap'), false);
  assert.equal(t.element('basemap-select').value, 'osm');

  t = fixture({ gps_map_settings_v1: { provider: 'osm', googleApiKey: 'FAKE-KEY-FOR-TEST' } });
  t.run('_udReadCache()');
  t.context.L = { gridLayer: { googleMutant: () => ({}) } };
  const failedScripts = [];
  t.context.recordScript = s => failedScripts.push(s);
  t.run('document.createElement = tag => { const s = { tag, remove() {} }; recordScript(s); return s; }; document.head = { appendChild() {} };');
  const failing = t.context.setBaseMap('google-roadmap');
  failedScripts[0].onerror();
  assert.equal(await failing, false);
  assert.match(t.toasts.at(-1), /載入失敗，已切回 OpenStreetMap/);
  assert.equal(t.element('basemap-select').value, 'osm');
  assert.equal(t.run('_mapSettings.provider'), 'osm');
  console.log('通過：底圖沒金鑰開設定框、OSM 不連 Google、API 只載入一次、金鑰無效與載入失敗切回 OSM');
  console.log('全部通過；沒有真實網路或手機操作。');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
