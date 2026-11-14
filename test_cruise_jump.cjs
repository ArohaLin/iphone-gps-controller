// 執行：node test_cruise_jump.cjs；只用模擬 fetch／假地圖，不連線手機、不連真的 Launcher。
// 驗證巡航「⚡ 跳躍」模式：送出參數、即時更新、上限 19、路線存讀、進度文字、舊版 Launcher、記住設定。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { reply } = require('./test_connection_mode.cjs');
const html = fs.readFileSync(path.join(__dirname, 'gps_map.html'), 'utf8');
const source = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)]
  .map(match => match[1]).join('\n');
new vm.Script(source); // 檢查完整頁面 JavaScript 語法。

function memoryStorage(initial = {}) {
  const data = new Map(Object.entries(initial).map(([k, v]) => [k, typeof v === 'string' ? v : JSON.stringify(v)]));
  return { getItem: k => (data.has(k) ? data.get(k) : null), setItem: (k, v) => { data.set(k, String(v)); }, removeItem: k => { data.delete(k); } };
}
function makeElement(id) {
  const classes = new Set(), attrs = {};
  return {
    id, value: '', disabled: false, textContent: '', innerHTML: '', style: {},
    classList: {
      add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c),
      toggle: (c, on) => { const v = on === undefined ? !classes.has(c) : !!on; if (v) classes.add(c); else classes.delete(c); return v; },
    },
    setAttribute: (k, v) => { attrs[k] = String(v); }, getAttribute: k => (k in attrs ? attrs[k] : null),
  };
}
function setup(storage = {}) {
  const elements = new Map(), calls = [], queue = [], toasts = [], confirms = [], layers = new Set();
  const element = id => { if (!elements.has(id)) elements.set(id, makeElement(id)); return elements.get(id); };
  const layer = (kind, extra) => { const l = { kind, ...extra, addTo() { layers.add(l); return l; } }; return l; };
  const document = { getElementById: element, addEventListener() {}, querySelectorAll: () => [], activeElement: null };
  const context = vm.createContext({
    window: {}, document, AbortController, setTimeout, clearTimeout, console,
    localStorage: memoryStorage(storage), requestAnimationFrame: () => {}, prompt: () => null,
    confirm: message => { confirms.push(message); return true; },
    L: {
      circle: (latlng, opts) => layer('circle', { latlng, opts }),
      polyline: (pts, opts) => layer('polyline', { pts, opts }),
      marker: latlng => layer('marker', { latlng, on() {}, bindTooltip() {}, setIcon() {} }),
      divIcon: o => o, latLngBounds: x => x,
    },
    fetch: async (url, options) => {
      calls.push({ url, options });
      assert.ok(queue.length, `未預期的請求：${url}`);
      const result = queue.shift();
      if (result instanceof Error) throw result;
      return result;
    },
    toast: text => toasts.push(text),
  });
  vm.runInContext(source, context);
  vm.runInContext(`
    map = { removeLayer: l => removeFakeLayer(l), fitBounds() {}, setView() {} };
    refreshDevices = async () => {};
    showToast = toast;
    scheduleTzUpdate = () => {};
    selectDevice = idx => { state.deviceIdx = idx; };
    getOrCreateMarkers = () => ({phone: {setLatLng() {}}, pulse: {setLatLng() {}}});
    state.deviceIdx = 0;
    state.devices = [{ idx: 0, udid: 'test-a', name: '測試甲', connected: true, enabled: true }];
  `, Object.assign(context, { removeFakeLayer: l => layers.delete(l) }));
  const run = code => vm.runInContext(code, context);
  return {
    context, element, calls, queue, toasts, confirms, run, document,
    json: expr => JSON.parse(run(`JSON.stringify(${expr})`)),
    circles: () => [...layers].filter(l => l.kind === 'circle'),
    body: i => JSON.parse(calls.at(i).options.body),
    hidden: id => element(id).classList.contains('hidden'),
    pressed: id => element(id).getAttribute('aria-pressed'),
  };
}
const WPS = [{ lat: 25, lon: 121 }, { lat: 25.001, lon: 121.001 }, { lat: 25.002, lon: 121.002 }];
const addWps = t => t.run(WPS.map(w => `addWaypoint(${w.lat}, ${w.lon});`).join(''));
const status = (over = {}) => ({
  ok: true, id: 'run-1', state: 'running', active: true, paused: false, deviceIdx: 0,
  waypoints: WPS, speed: 5, loop: false, position: { lat: 25, lon: 121 }, progress: 0.4, target_index: 1,
  mode: 'jump', dwellSec: 15, radiusM: 30, circleSpeed: 19, stopIndex: 1, dwellRemaining: 12.3, ...over,
});
const walkStatus = (over = {}) => status({ mode: 'walk', stopIndex: null, dwellRemaining: null, ...over });
const oldStatus = (over = {}) => {   // 舊版 Launcher：完全沒有跳躍相關欄位
  const s = status(over);
  ['mode', 'dwellSec', 'radiusM', 'circleSpeed', 'stopIndex', 'dwellRemaining'].forEach(k => delete s[k]);
  return s;
};
const tick = () => new Promise(resolve => setImmediate(resolve));

async function main() {
  // 1. 切到跳躍：按鈕 aria-pressed、欄位顯示、航點畫圈、路線資訊；開始時送出 mode＋三個參數
  let t = setup();
  addWps(t);
  assert.equal(t.pressed('cruise-mode-walk'), null, '尚未切換前保持 HTML 原狀');
  assert.equal(t.circles().length, 0, '等速移動不畫圈');
  assert.notEqual(t.element('ri-dist').textContent, '—');
  await t.context.setCruiseMode('jump');
  assert.equal(t.calls.length, 0, '未巡航時不送更新');
  assert.equal(t.pressed('cruise-mode-jump'), 'true'); assert.equal(t.pressed('cruise-mode-walk'), 'false');
  assert.equal(t.hidden('cruise-walk-fields'), true); assert.equal(t.hidden('cruise-jump-fields'), false);
  assert.equal(t.circles().length, 3);
  t.circles().forEach(c => { assert.equal(c.opts.radius, 30); assert.equal(c.opts.color, '#e8af34'); assert.ok(c.opts.dashArray); });
  assert.equal(t.element('ri-dist').textContent, '—');
  assert.equal(t.element('ri-eta').textContent, '45s', '3 點 × 15 秒');
  t.context.updateDwell('20');
  assert.equal(t.element('ri-eta').textContent, '1m 0s');
  t.queue.push(reply(status({ dwellSec: 20 })));
  await t.context.playCruise();
  assert.match(t.calls.at(-1).url, /\/cruise\/start$/);
  assert.deepEqual(t.body(-1), { deviceIdx: 0, waypoints: WPS, speed: 5, loop: false, mode: 'jump', dwellSec: 20, radiusM: 30, circleSpeed: 19 });
  assert.equal(t.element('play-status-text').textContent, '第 2/3 點 · 還停留 13 秒');
  assert.equal(t.element('progress-text').textContent, '進度 40%');
  assert.equal(t.toasts.length, 0);
  console.log('通過：切換跳躍（aria-pressed、欄位、畫圈、路線資訊），開始時送出 mode 與參數');

  // 2. 巡航中改欄位 → /cruise/update 只帶該欄位；切回等速移動 → 帶 mode＋參數、移除圈圈、文字恢復
  t.queue.push(reply(status({ dwellSec: 20, radiusM: 50 })));
  await t.context.updateRadius('50');
  assert.match(t.calls.at(-1).url, /\/cruise\/update$/);
  assert.deepEqual(t.body(-1), { radiusM: 50 });
  assert.equal(t.circles().length, 3); t.circles().forEach(c => assert.equal(c.opts.radius, 50));
  t.queue.push(reply(status({ dwellSec: 30, radiusM: 50 })));
  await t.context.updateDwell('30');
  assert.deepEqual(t.body(-1), { dwellSec: 30 });
  const before = t.calls.length;
  await t.context.updateDwell('30');
  assert.equal(t.calls.length, before, 'oninput＋onchange 同值不重送');
  // 打字中途不送出：打「1」再打「12」，停手 0.8 秒後只送一次 12
  const typing = { id: 'dwell-input', value: '1' };
  t.context._jumpInputLater(typing, t.context.updateDwell);
  typing.value = '12';
  t.context._jumpInputLater(typing, t.context.updateDwell);
  await new Promise(resolve => setTimeout(resolve, 300));
  assert.equal(t.calls.length, before, '停手前不送出');
  t.queue.push(reply(status({ dwellSec: 12, radiusM: 50 })));
  await new Promise(resolve => setTimeout(resolve, 700));
  assert.equal(t.calls.length, before + 1, '停手後只送一次');
  assert.deepEqual(t.body(-1), { dwellSec: 12 });
  await t.context._jumpInputNow(typing, t.context.updateDwell);
  assert.equal(t.calls.length, before + 1, '離開欄位同值不重送');
  t.queue.push(reply(status({ dwellSec: 30, radiusM: 50 })));
  await t.context._jumpInputNow({ id: 'dwell-input', value: '30' }, t.context.updateDwell);
  assert.deepEqual(t.body(-1), { dwellSec: 30 }, 'Enter／離開欄位立刻送出');
  t.queue.push(reply(walkStatus({ dwellSec: 30, radiusM: 50 })));
  await t.context.setCruiseMode('walk');
  assert.deepEqual(t.body(-1), { mode: 'walk', dwellSec: 30, radiusM: 50, circleSpeed: 19 });
  assert.equal(t.circles().length, 0);
  assert.equal(t.hidden('cruise-walk-fields'), false); assert.equal(t.hidden('cruise-jump-fields'), true);
  assert.equal(t.element('play-status-text').textContent, '前往航點 2');
  assert.notEqual(t.element('ri-dist').textContent, '—');
  console.log('通過：巡航中改停留／半徑／方式立即送 /cruise/update，圈圈與文字跟著更新');

  // 3. 繞圈速度上限 19；無效、空白、0 直接忽略
  t = setup(); addWps(t); t.context.setCruiseMode('jump');
  t.element('circle-speed-input').value = '25';
  t.context.updateCircleSpeed('25');
  assert.equal(t.run('state.circleSpeed'), 19);
  assert.equal(t.element('circle-speed-input').value, 19);
  assert.match(t.toasts.at(-1), /最高 19 km\/h/);
  t.context.updateCircleSpeed('10');
  for (const bad of ['', 'abc', '0', '-3']) t.context.updateCircleSpeed(bad);
  assert.equal(t.run('state.circleSpeed'), 10);
  for (const bad of ['', '0', '0.5']) t.context.updateDwell(bad);
  t.context.updateRadius('-1');
  assert.deepEqual(t.json('_cruiseParams()'), { mode: 'jump', dwellSec: 15, radiusM: 30, circleSpeed: 10 });
  t.context.updateDwell('9999'); t.context.updateRadius('800');
  assert.deepEqual(t.json('[state.dwellSec, state.radiusM]'), [3600, 500]);
  t.context.updateRadius('0');
  assert.equal(t.circles().length, 0, '半徑 0 不畫圈');
  t.run('state.cruiseActive = true');
  t.queue.push(reply(status({ circleSpeed: 19 })));
  await t.context.updateCircleSpeed('40');
  assert.deepEqual(t.body(-1), { circleSpeed: 19 }, '巡航中送出的是夾住後的 19');
  console.log('通過：繞圈速度超過 19 改成 19 並提示，無效／空白忽略，停留與半徑也有上下限');

  // 4. 等速移動：開始 payload 只多了四個欄位，舊版或新版 Launcher 都照舊
  for (const [label, response] of [['新版', walkStatus()], ['舊版', oldStatus()]]) {
    t = setup(); addWps(t);
    t.queue.push(reply(response));
    await t.context.playCruise();
    assert.deepEqual(t.body(-1), { deviceIdx: 0, waypoints: WPS, speed: 5, loop: false, mode: 'walk', dwellSec: 15, radiusM: 30, circleSpeed: 19 });
    assert.equal(t.element('play-status-text').textContent, '前往航點 2', label);
    assert.equal(t.toasts.length, 0, label);
    assert.equal(t.circles().length, 0);
    assert.equal(t.run('state.cruiseActive'), true);
  }
  console.log('通過：等速移動開始 payload 不變（只多 mode 等欄位），新舊 Launcher 都照常');

  // 5. 路線存讀：快照帶跳躍參數、清單與覆蓋確認顯示跳躍摘要、讀取還原；舊路線讀成等速移動
  t = setup(); addWps(t);
  t.context.setCruiseMode('jump'); t.context.updateDwell('20'); t.context.updateRadius('40'); t.context.updateCircleSpeed('10');
  t.context.openSaveCruiseModal();
  assert.equal(t.element('csm-info').textContent, '3 個航點 · ⚡ 跳躍 · 停 20 秒 · 半徑 40 m');
  t.element('csm-name').value = '公園跳點';
  t.context.confirmSaveCruise();
  const saved = t.json('_cruiseRoutes[0]');
  assert.deepEqual([saved.mode, saved.dwellSec, saved.radiusM, saved.circleSpeed, saved.speed, saved.loop], ['jump', 20, 40, 10, 5, false]);
  t.run(`_cruiseRoutes.push({ id: 7, name: '舊路線', waypoints: ${JSON.stringify(WPS.slice(0, 2))}, speed: 8, loop: false, savedAt: '2026-11-01T00:00:00.000Z' })`);
  t.context.renderCruiseRouteList();
  const list = t.element('cruise-route-list').innerHTML;
  assert.match(list, /3 個航點 · ⚡ 跳躍 · 停 20 秒 · 半徑 40 m/);
  assert.match(list, /2 個航點 · 8 km\/h · 11\/01/);
  t.context.overwriteCruiseRoute(saved.id);
  assert.match(t.confirms.at(-1), /用目前的 3 個航點（⚡ 跳躍 · 停 20 秒 · 半徑 40 m）覆蓋「公園跳點」/);
  t.context.setCruiseMode('walk');
  t.context.overwriteCruiseRoute(7);
  assert.match(t.confirms.at(-1), /（速度 5 km\/h）覆蓋「舊路線」/, '等速移動維持原本文字');
  t.run(`_cruiseRoutes[1] = { id: 7, name: '舊路線', waypoints: ${JSON.stringify(WPS.slice(0, 2))}, speed: 8, loop: false }`);
  t.context.updateDwell('99');
  assert.equal(await t.context.applyCruiseRoute(saved.id), true);
  assert.deepEqual(t.json('_cruiseParams()'), { mode: 'jump', dwellSec: 20, radiusM: 40, circleSpeed: 10 });
  assert.equal(t.element('dwell-input').value, 20);
  assert.equal(t.pressed('cruise-mode-jump'), 'true');
  assert.equal(t.circles().length, 3);
  assert.equal(await t.context.applyCruiseRoute(7), true);
  assert.deepEqual(t.json('_cruiseParams()'), { mode: 'walk', dwellSec: 15, radiusM: 30, circleSpeed: 19 });
  assert.equal(t.run('state.speed'), 8);
  assert.equal(t.circles().length, 0);
  assert.equal(t.pressed('cruise-mode-walk'), 'true');
  // 備份匯出／匯入原樣帶過新欄位（金鑰不匯出）
  t.run('_mapSettings.googleApiKey = "FAKE-KEY-FOR-TEST"');
  const backup = JSON.parse(JSON.stringify(t.context.buildBackupData()));
  assert.equal(backup.mapSettings.googleApiKey, undefined);
  assert.deepEqual(backup.mapSettings.cruise, { mode: 'walk', dwellSec: 15, radiusM: 30, circleSpeed: 19 });
  const t2 = setup();
  await t2.context.applyBackupData(t2.context.parseBackupData(backup));
  const restored = t2.json('_cruiseRoutes[0]');
  assert.deepEqual([restored.mode, restored.dwellSec, restored.radiusM, restored.circleSpeed], ['jump', 20, 40, 10]);
  assert.equal(t2.json('_cruiseRoutes[1]').mode, undefined, '舊路線匯入後不被改寫');
  assert.deepEqual(t2.json('_mapSettings.cruise'), backup.mapSettings.cruise);
  console.log('通過：路線快照帶跳躍參數、清單／覆蓋／儲存視窗文字、讀取還原、舊路線讀成等速移動、備份原樣保留');

  // 6. 進度文字：跳躍中「第 x/n 點 · 還停留 y 秒」、暫停仍顯示原本暫停標示；重新整理時跟著 Launcher
  t = setup(); addWps(t);
  t.queue.push(reply(status({ stopIndex: 0, dwellRemaining: 4.2, dwellSec: 25, radiusM: 60 })));
  await t.context.pollCruiseStatus();
  assert.equal(t.element('play-status-text').textContent, '第 1/3 點 · 還停留 5 秒');
  assert.deepEqual(t.json('_cruiseParams()'), { mode: 'jump', dwellSec: 25, radiusM: 60, circleSpeed: 19 }, '頁面重新整理 → 以 Launcher 為準');
  assert.equal(t.element('dwell-input').value, 25);
  assert.equal(t.pressed('cruise-mode-jump'), 'true');
  t.circles().forEach(c => assert.equal(c.opts.radius, 60));
  t.queue.push(reply(status({ state: 'paused', paused: true, dwellSec: 25, radiusM: 60 })));
  await t.context.pollCruiseStatus();
  assert.equal(t.element('play-status-text').textContent, '⏸');
  assert.equal(t.hidden('pause-label'), false);
  assert.equal(t.element('btn-play').textContent, '▶ 繼續');
  t.queue.push(reply(status({ stopIndex: 2, dwellRemaining: 0, dwellSec: 25, radiusM: 60 })));
  await t.context.pollCruiseStatus();
  assert.equal(t.element('play-status-text').textContent, '第 3/3 點 · 還停留 0 秒');
  // 另一個瀏覽器改了半徑 → 跟著改；使用者正在輸入的停留欄位不被蓋掉
  t.document.activeElement = t.element('dwell-input');
  t.queue.push(reply(status({ dwellSec: 99, radiusM: 70 })));
  await t.context.pollCruiseStatus();
  assert.deepEqual(t.json('[state.dwellSec, state.radiusM]'), [25, 70]);
  t.document.activeElement = null;
  // 使用者剛改的值：較早送出的狀態查詢晚回來時不可蓋回去
  let resolveOld; t.queue.push(new Promise(r => { resolveOld = r; }));
  const stale = t.context.pollCruiseStatus();
  t.queue.push(reply(status({ dwellSec: 40, radiusM: 70 })));
  const editing = t.context.updateDwell('40');
  resolveOld(reply(status({ dwellSec: 25, radiusM: 70 }))); await stale; await editing;
  assert.equal(t.run('state.dwellSec'), 40);
  console.log('通過：跳躍進度文字、暫停標示、重新整理／別處修改時同步，不蓋掉正在輸入或剛送出的值');

  // 7. 舊版 Launcher：開始回應沒有 mode → 提示、立刻停止、不顯示跳躍進度；之後直接擋下
  t = setup(); addWps(t); t.context.setCruiseMode('jump');
  t.queue.push(reply(oldStatus()), reply(oldStatus({ state: 'stopped', active: false, progress: 0 })));
  await t.context.playCruise();
  assert.match(t.calls[0].url, /\/cruise\/start$/);
  assert.match(t.calls[1].url, /\/cruise\/stop$/);
  assert.ok(t.toasts.includes('跳躍模式需要重新啟動新版 Launcher'));
  assert.equal(t.run('state.cruiseActive'), false);
  assert.equal(t.hidden('progress-section'), true);
  assert.doesNotMatch(t.element('play-status-text').textContent, /第 .* 點/);
  const count = t.calls.length;
  await t.context.playCruise();
  assert.equal(t.calls.length, count, '已知是舊版 → 不再送出開始');
  assert.equal(t.toasts.at(-1), '跳躍模式需要重新啟動新版 Launcher');
  // 舊版正在等速移動時想切到跳躍 → 不切換、不送更新
  t = setup(); addWps(t);
  t.queue.push(reply(oldStatus()));
  await t.context.pollCruiseStatus();
  t.context.setCruiseMode('jump');
  assert.equal(t.calls.length, 1);
  assert.equal(t.run('state.cruiseMode'), 'walk');
  assert.equal(t.toasts.at(-1), '跳躍模式需要重新啟動新版 Launcher');
  // 記住的設定是跳躍、但舊版 Launcher 正在等速移動 → 畫面改回等速移動並提示
  t = setup({ gps_map_settings_v1: { cruise: { mode: 'jump', dwellSec: 15, radiusM: 30, circleSpeed: 19 } } });
  addWps(t); t.run('_udReadCache(); _udRefreshUI();');
  assert.equal(t.run('state.cruiseMode'), 'jump');
  t.queue.push(reply(oldStatus()));
  await t.context.pollCruiseStatus();
  assert.equal(t.run('state.cruiseMode'), 'walk');
  assert.equal(t.element('play-status-text').textContent, '前往航點 2');
  assert.equal(t.toasts.at(-1), '跳躍模式需要重新啟動新版 Launcher');
  console.log('通過：舊版 Launcher 時提示重新啟動、立刻停止、不假裝跳躍；等速移動照常');

  // 8. 記住設定：存在 mapSettings.cruise，合併保留 provider／googleApiKey 等其他設定；重新開頁還原
  const remoteSettings = { provider: 'osm', googleApiKey: 'FAKE-KEY-FOR-TEST', lastCruiseRouteId: 3,
    cruise: { mode: 'jump', dwellSec: 40, radiusM: 80, circleSpeed: 12 } };
  t = setup({ gps_userdata_synced: 'x' });
  t.queue.push(reply({ ok: true, exists: true, data: { version: 1, favorites: [], cruiseRoutes: [], mapSettings: remoteSettings } }));
  assert.equal(await t.context.initUserData(), true);
  assert.deepEqual(t.json('_cruiseParams()'), remoteSettings.cruise, '重新開頁還原上次的方式與參數');
  assert.equal(t.element('radius-input').value, 80);
  assert.equal(t.pressed('cruise-mode-jump'), 'true');
  assert.equal(t.calls.length, 1, '只是還原，不回存');
  t.queue.push(reply({ ok: true }));
  t.context.updateDwell('45');
  await t.run('_udFlushing || Promise.resolve()');
  const post = t.body(-1);
  assert.match(t.calls.at(-1).url, /\/userdata$/);
  assert.deepEqual(post, { mapSettings: { ...remoteSettings, cruise: { ...remoteSettings.cruise, dwellSec: 45 } } });
  assert.deepEqual(JSON.parse(t.context.localStorage.getItem('gps_map_settings_v1')), post.mapSettings);
  // 巡航中重新整理：Launcher 狀態優先，晚到的記憶設定不覆蓋
  t = setup({ gps_userdata_synced: 'x' });
  t.queue.push(reply(walkStatus()));
  await t.context.pollCruiseStatus();
  t.queue.push(reply({ ok: true, exists: true, data: { version: 1, favorites: [], cruiseRoutes: [], mapSettings: remoteSettings } }));
  await t.context.initUserData();
  assert.equal(t.run('state.cruiseMode'), 'walk');
  console.log('通過：mapSettings.cruise 合併存檔（保留 provider／googleApiKey）、開頁還原、巡航中以 Launcher 為準');
  console.log('全部通過；未使用真實網路或手機。');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
