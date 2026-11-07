// 執行：node test_connection_mode.cjs；只使用模擬 fetch，不會連線手機。
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, 'gps_map.html'), 'utf8');
const source = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)]
  .map(match => match[1]).join('\n');
new vm.Script(source); // 檢查完整頁面 JavaScript 語法。
const reply = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
function setup() {
  const elements = new Map();
  const calls = [], queue = [], toasts = [];
  const element = id => {
    if (!elements.has(id)) elements.set(id, { value: 'usb', disabled: true, textContent: '', innerHTML: '', style: {}, classList: { add() {}, remove() {}, toggle() {} } });
    return elements.get(id);
  };
  const context = vm.createContext({
    window: {}, document: { getElementById: element, addEventListener() {}, querySelectorAll: () => [] },
    AbortController, setTimeout, clearTimeout, console,
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
    refreshDevices = async () => {};
    pollCruiseStatus = async () => {};
    showToast = toast;
    scheduleTzUpdate = () => {};
    getOrCreateMarkers = () => ({phone: {setLatLng() {}}, pulse: {setLatLng() {}}});
  `, context);
  return { context, element, calls, queue, toasts, run: code => vm.runInContext(code, context) };
}
async function known(t, mode = 'usb') {
  t.queue.push(reply({ ok: true, mode }));
  await t.context.pollConnectionMode();
  assert.equal(t.element('connection-mode').value, mode);
  assert.equal(t.element('connection-mode').disabled, false);
}
async function main() {
  let t = setup();
  await known(t);
  const pending = deferred();
  t.queue.push(pending.promise);
  t.element('connection-mode').value = 'wifi';
  const changing = t.context.changeConnectionMode('wifi');
  assert.equal(t.element('connection-mode').value, 'usb');
  assert.equal(t.element('connection-mode').disabled, true);
  assert.match(t.element('connection-mode-status').title, /巡航將中止/);
  const count = t.calls.length;
  await t.context.pollConnectionMode();
  await t.context.changeConnectionMode('auto');
  assert.equal(t.calls.length, count, '切換期間不可另送模式請求');
  assert.deepEqual(JSON.parse(t.calls.at(-1).options.body), { mode: 'wifi' });
  pending.resolve(reply({ ok: true, mode: 'wifi' }));
  await changing;
  assert.equal(t.element('connection-mode').value, 'wifi');
  assert.equal(t.element('connection-mode').disabled, false);
  await known(t, 'auto');
  console.log('通過：預設、成功才更新、停用、防重送、定時反映後端');

  for (const failure of [reply({ ok: false, error: '切換被拒絕' }), reply({ ok: true, mode: 'wifi' }, 500), reply({ ok: true, mode: 'invalid' }), new Error('網路中斷'), reply({}, 404)]) {
    t = setup(); await known(t);
    t.queue.push(failure);
    await t.context.changeConnectionMode('wifi');
    assert.equal(t.element('connection-mode').value, 'usb');
    assert.equal(t.element('connection-mode').disabled, false);
    assert.equal(t.toasts.length, 1);
    if (failure.status === 404) assert.match(t.toasts[0], /重啟新版/);
  }
  t = setup(); t.queue.push(reply({}, 404));
  await t.context.pollConnectionMode();
  assert.equal(t.element('connection-mode').disabled, true);
  assert.match(t.element('connection-mode-status').title, /重啟新版/);
  await known(t);
  console.log('通過：應用錯誤、HTTP 錯誤、無效模式、網路失敗、GET/POST 404 與恢復');

  const networkHint = '無法取得連線模式，請確認 Launcher 已啟動；若剛更新，請重新啟動 Launcher。';
  t = setup(); t.queue.push(new TypeError('Load failed'));
  await t.context.pollConnectionMode();
  assert.equal(t.element('connection-mode-status').title, networkHint);
  assert.equal(t.element('connection-mode').disabled, true);
  await known(t);
  t.queue.push(new TypeError('Load failed'));
  await t.context.changeConnectionMode('wifi');
  assert.equal(t.element('connection-mode-status').title, networkHint);
  assert.equal(t.toasts.at(-1), networkHint);
  assert.equal(t.element('connection-mode').value, 'usb');
  assert.equal(t.element('connection-mode').disabled, false);
  console.log('通過：Safari GET/POST Load failed 顯示繁中重啟提示，不假成功');

  t = setup(); await known(t);
  const stale = deferred();
  t.queue.push(stale.promise);
  const polling = t.context.pollConnectionMode();
  const write = deferred(); t.queue.push(write.promise);
  const writing = t.context.changeConnectionMode('wifi');
  stale.resolve(reply({ ok: true, mode: 'auto' })); await polling;
  assert.equal(t.element('connection-mode').value, 'usb');
  assert.equal(t.element('connection-mode').disabled, true);
  write.resolve(reply({ ok: true, mode: 'wifi' })); await writing;
  assert.equal(t.element('connection-mode').value, 'wifi');
  console.log('通過：較早查詢不可覆蓋待處理切換');

  t = setup();
  t.context.renderDeviceList([
    { idx: 0, name: '測試甲', connected: true, connection_type: 'usb' },
    { idx: 1, name: '測試乙', connected: true, connection_type: 'wifi' },
    { idx: 2, name: '舊版裝置', connected: false },
  ]);
  const cards = t.element('device-list').innerHTML;
  assert.match(cards, /USB · 已連線/); assert.match(cards, /Wi-Fi · 已連線/);
  assert.match(cards, /連線方式未知/); assert.match(cards, /2 \/ 3 台已連線/);
  console.log('通過：多機各自標示 USB／Wi-Fi，缺欄位不誤報 USB');

  for (const selected of [0, 'all']) {
    t = setup();
    t.run(`state.deviceIdx = ${JSON.stringify(selected)}; state.devices = [{idx: 0, udid: 'test-usb', connected: true, enabled: true}];`);
    const oldDevices = [{ idx: 0, udid: 'test-usb', enabled: true, connected: true, last_lat: 1, last_lon: 2 }];
    const oldPoll = deferred(); t.queue.push(oldPoll.promise);
    const pollTask = t.context.pollDevices();
    const move = deferred(); t.queue.push(move.promise);
    t.context.gotoCoord(25, 121);
    oldPoll.resolve(reply(oldDevices)); await pollTask;
    assert.equal(t.run('state.lat'), 25);
    move.resolve(reply({ ok: true, count: 1 }));
    await new Promise(resolve => setImmediate(resolve));
    t.queue.push(reply(oldDevices)); await t.context.pollDevices();
    assert.equal(t.run('state.lat'), 25); assert.equal(t.run('state.lon'), 121);
    assert.equal(t.element('in-coord').value, '25.000000, 121.000000');
  }
  console.log('通過：單機及同步全部，手動座標不被舊查詢或後續輪詢拉回');
  assert.match(html, /const META\s*= 'http:\/\/localhost:8090'/);
  console.log('全部通過；未使用真實網路或手機。');
}
module.exports = { setup, reply, deferred };
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
