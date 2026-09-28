// Execute unchanged scripts in a minimal Scriptable API stub; compare every rendered text.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');

async function run(direction, family, refresh, withArrival, end) {
  const texts = [], requests = [], links = [];
  let completed = 0, closed = 0, presented = 0, widget;
  class Stack {
    addText(value) { texts.push(value); const item = {}; Object.defineProperty(item, 'url', { set: url => links.push(url) }); return item; }
    addStack() { return new Stack(); }
    addSpacer() {}
    centerAlignContent() {}
    setPadding() {}
    async presentSmall() { presented++; }
    async presentMedium() { presented++; }
    async presentLarge() { presented++; }
  }
  const buses = ['08:00', '09:00', '10:00'].map((time, i) => ({ time, line: '(33) 寺地', line_number: '33',
    stop: 'C', stop_name: '四十万方向', minutes_until: 30 + i * 60,
    ...(withArrival ? { arrival_time: i === 1 ? null : '10:31' } : {}) }));
  const payload = end ? { status: 'end', next_service: { date: '2026-09-26', days_ahead: 1,
    day_type: 'weekend', bus: buses[0] } } : { status: 'success', buses };
  const sandbox = { console: { error: error => { throw error; } }, Date,
    config: { widgetFamily: family, runsInApp: refresh }, args: { queryParameters: refresh ? { action: 'refresh' } : {} },
    ListWidget: Stack, Color: class {}, Font: { boldSystemFont: () => ({}), systemFont: () => ({}) },
    Request: class {
      constructor(url) { requests.push(url); this.response = { statusCode: 200 }; }
      async loadString() { return JSON.stringify(payload); }
    },
    Script: { name: () => `bus_${direction}`, setWidget: value => { widget = value; }, complete: () => { completed++; } },
    App: { close: () => { closed++; } }
  };
  const code = fs.readFileSync(path.join(root, 'Scriptable_Scripts', `bus_${direction}.js`), 'utf8');
  await new vm.Script(`(async () => {${code}\n})()`).runInNewContext(sandbox);
  assert.equal(completed, 1); assert.equal(closed, refresh ? 1 : 0); assert.equal(presented, 0);
  assert.equal(requests.length, 1); assert.equal(new URL(requests[0]).searchParams.get('dir'), direction);
  assert.ok(widget.refreshAfterDate instanceof Date);
  if (family !== 'small') assert.ok(links.some(url => url.includes('&action=refresh')));
  return texts;
}

(async () => {
  let cases = 0;
  for (const direction of ['to_uni', 'to_station', 'to_nakahashi'])
    for (const family of ['small', 'medium', 'large'])
      for (const refresh of [false, true]) for (const end of [false, true]) {
        assert.deepEqual(await run(direction, family, refresh, true, end), await run(direction, family, refresh, false, end));
        cases++;
      }
  console.log(`PASS: ${cases} Scriptable compatibility cases (72 executions), 3 directions × 3 sizes × widget/refresh × success/end; arrival field ignored`);
})().catch(error => { console.error(error); process.exitCode = 1; });
