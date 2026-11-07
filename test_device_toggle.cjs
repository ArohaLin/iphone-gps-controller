// 執行 node test_device_toggle.cjs；所有請求均由 mock 接管，不使用網路。
const assert = require('node:assert/strict');
const { setup, reply, deferred } = require('./test_connection_mode.cjs');
const devices = () => [
  { idx: 0, udid: '甲', name: '手機甲', enabled: true, connected: true, connection_type: 'usb' },
  { idx: 1, udid: '乙', name: '手機乙', enabled: false, connected: true, connection_type: 'wifi' },
  { idx: 2, udid: '丙', name: '手機丙', enabled: true, connected: false, connection_type: 'wifi' },
];
function fixture(selected = 0) {
  const t = setup();
  t.run(`state.devices = ${JSON.stringify(devices())}; state.deviceIdx = ${JSON.stringify(selected)};`);
  t.context.renderDeviceList(t.run('state.devices'));
  return t;
}
const enabled = (t, udid) => t.run(`state.devices.find(d => d.udid === ${JSON.stringify(udid)}).enabled`);
async function main() {
  let t = fixture(1);
  let write = deferred(); t.queue.push(write.promise);
  const input = { checked: false };
  let saving = t.context.toggleDeviceEnabled(0, input);
  assert.equal(input.checked, true);
  assert.equal(enabled(t, '甲'), true);
  assert.match(t.element('device-list').innerHTML, /device-enabled-0[\s\S]*?checked disabled/);
  const count = t.calls.length;
  await t.context.toggleDeviceEnabled(0, { checked: false });
  assert.equal(t.calls.length, count);
  assert.deepEqual(JSON.parse(t.calls[0].options.body), { enabled: false });
  // 輪詢期間 idx 改變且舊 idx 被另一手機使用，回應只能更新原 UDID。
  const changed = devices(); changed[0].idx = 8; changed[0].enabled = false; changed[2].idx = 0;
  t.queue.push(reply(changed)); await t.context.pollDevices();
  assert.equal(enabled(t, '甲'), true, 'pending 期間舊輪詢不可覆寫');
  write.resolve(reply({ ok: true, enabled: false })); await saving;
  assert.equal(enabled(t, '甲'), false); assert.equal(enabled(t, '丙'), true);
  assert.equal(t.run('state.deviceIdx'), 1);
  assert.equal(t.run('state.lat'), 25.033);
  assert.equal(t.calls.length, 2, '不得自動發送定位或巡航');
  console.log('通過：成功才更新、UDID 防重送、pending 輪詢、idx 重用不誤套回應、不切選擇或定位');

  t = fixture(); const stale = deferred(); t.queue.push(stale.promise);
  const polling = t.context.pollDevices();
  t.queue.push(reply({ ok: true, enabled: false }));
  await t.context.toggleDeviceEnabled(0, { checked: false });
  stale.resolve(reply(devices())); await polling;
  assert.equal(enabled(t, '甲'), false);
  console.log('通過：切換完成後才返回的舊輪詢不可覆寫');

  t = fixture(); const a = deferred(), b = deferred(); t.queue.push(a.promise, b.promise);
  const pa = t.context.toggleDeviceEnabled(0, { checked: false });
  t.context.selectDevice(1, false);
  const pb = t.context.toggleDeviceEnabled(1, { checked: true });
  b.resolve(reply({ ok: true, enabled: true })); await pb;
  assert.equal(t.run('_deviceTogglePending.has("甲")'), true);
  a.resolve(reply({ ok: true, enabled: false })); await pa;
  assert.equal(enabled(t, '甲'), false); assert.equal(enabled(t, '乙'), true);
  assert.equal(t.run('state.deviceIdx'), 1);
  assert.equal(t.calls.length, 2);
  console.log('通過：切手機及不同手機並行儲存互不覆寫，啟用不重播');

  for (const failure of [reply({ ok: true, enabled: false }, 500), reply({ ok: false, error: '儲存失敗' }), new TypeError('Load failed'), reply({}, 404)]) {
    t = fixture(); t.queue.push(failure);
    await t.context.toggleDeviceEnabled(0, { checked: false });
    assert.equal(enabled(t, '甲'), true);
    assert.equal(t.run('_deviceTogglePending.size'), 0);
    assert.equal(t.toasts.length, 1);
  }
  t = fixture(); t.queue.push(reply({ ok: true, enabled: false, warning: '手機清除失敗，位置可能仍模擬中' }));
  await t.context.toggleDeviceEnabled(0, { checked: false });
  assert.equal(enabled(t, '甲'), false);
  assert.match(t.element('device-list').innerHTML, /警告：手機清除失敗/);
  assert.doesNotMatch(t.toasts[0], /已恢復|已清除/);
  console.log('通過：非 2xx／儲存失敗維持原狀，warning 不假稱恢復定位');

  t = fixture(); t.run('delete state.devices[0].enabled');
  t.context.renderDeviceList(t.run('state.devices'));
  assert.match(t.element('device-list').innerHTML, /id="device-enabled-0"[\s\S]*?disabled/);
  assert.match(t.element('device-list').innerHTML, /請重新啟動新版 Launcher/);
  await t.context.toggleDeviceEnabled(0, { checked: true });
  assert.equal(t.calls.length, 0);
  const markup = t.element('device-list').innerHTML;
  assert.match(markup, /type="checkbox"/);
  assert.match(markup, /aria-label="手機甲：啟用控制"/);
  assert.match(markup, /onclick="event.stopPropagation\(\)" onkeydown="event.stopPropagation\(\)"/);
  console.log('通過：舊後端開關停用、具名稱的原生鍵盤開關、點擊及鍵盤停止冒泡');

  t = fixture('all');
  assert.match(t.element('device-list').innerHTML, /2 \/ 3 台已連線 · 1 台可控制/);
  assert.equal(t.run('JSON.stringify(controllableDevices().map(d => d.udid))'), '["甲"]');
  const moved = []; t.context.recordMove = idx => moved.push(idx);
  t.run('getOrCreateMarkers = idx => ({phone: {setLatLng() {recordMove(idx)}}, pulse: {setLatLng() {}}})');
  t.queue.push(reply({ ok: true, count: 1 })); t.context.gotoCoord(20, 120);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(moved, [0]); assert.match(t.calls[0].url, /\/devices\/set$/);
  t.run('state.devices[0].enabled = false');
  const before = t.calls.length;
  t.context.gotoCoord(30, 130); t.context.pan('N'); t.context.clearLocation();
  await t.context.playCruise(); await t.context.cruiseCommand('resume');
  await t.context.addSteps(10); await t.context.deleteSteps(); await t.context.resetSteps();
  t.context.startWalking();
  assert.equal(t.calls.length, before);
  assert.equal(t.run('state.lat'), 20);
  t.context.renderDeviceList(t.run('state.devices'));
  assert.equal(t.element('btn-play').disabled, true);
  t.run('state.deviceIdx = 1');
  t.context.gotoCoord(40, 140); await t.context.addSteps(20);
  assert.equal(t.calls.length, before);
  console.log('通過：同步全部過濾連線且啟用者，零可控／單機停用時擋住定位、方向、巡航、步數');
  console.log('全部通過；沒有真實網路或手機操作。');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
