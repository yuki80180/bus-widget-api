// Run against local Flask: BUS_BASE_URL=http://127.0.0.1:5001 node tests/test_web_arrivals.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const template = fs.readFileSync(path.join(root, 'templates/index.html'), 'utf8');
const script = [...template.matchAll(/<script>([\s\S]*?)<\/script>/g)][0][1];
new vm.Script(script); // All embedded JavaScript must parse.
JSON.parse(fs.readFileSync(path.join(root, 'static/manifest.webmanifest'), 'utf8'));
const baseURL = process.env.BUS_BASE_URL || 'http://127.0.0.1:5000';
const output = path.join(root, 'monitor/generated/web-arrival');
fs.mkdirSync(output, { recursive: true });
const chrome = process.env.CHROME_PATH || (process.platform === 'win32'
  ? 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' : undefined);
const bus = (time, arrival_time, minutes_until = 18) => ({ time, arrival_time, minutes_until,
  line: '(33) とても長い路線名・経由地・行き先の表示確認用テキスト'.repeat(3),
  line_number: '33', stop: 'C', stop_name: '四十万方向' });
const buses = [bus('15:41', '16:12'), bus('15:56', null, 33), bus('16:11', '16:42', 48)];
const details = {
  to_uni: { from: '金沢駅・中橋方面', to: 'KIT', destination: '金沢工業大学行' },
  to_station: { from: 'KIT', to: '金沢駅', destination: '金沢駅行' },
  to_nakahashi: { from: 'KIT', to: '中橋', destination: '中橋方面行' }
};

async function noOverflow(page) {
  const overflow = await page.evaluate(() => {
    const bad = [];
    if (document.documentElement.scrollWidth > innerWidth) bad.push('document');
    for (const element of document.querySelectorAll('.departure-time,.following-time,.timetable-time,.timetable-details')) {
      if (element.getClientRects().length && element.scrollWidth > element.clientWidth + 1) bad.push(element.className);
    }
    return bad;
  });
  assert.deepEqual(overflow, []);
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: chrome });
  let cases = 0;
  try {
    for (const width of [320, 375, 390, 768, 1280]) for (const colorScheme of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme });
      const page = await context.newPage();
      await page.addInitScript(() => {
        window.testFetches = [];
        const original = window.fetch;
        window.fetch = (url, options) => {
          window.testFetches.push({ url, cache: options?.cache, abortable: !!options?.signal });
          return original(url, options);
        };
      });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      let mode = 'success';
      await page.route('**/api/**', async route => {
        const url = new URL(route.request().url());
        const direction = url.searchParams.get('dir');
        const response = { status: 'success', date: '2026-09-25', current_time: '15:23',
          day_type: 'weekday', direction, direction_detail: details[direction], buses };
        if (url.pathname.endsWith('timetable')) response.buses = [bus('14:00', '14:32'), ...buses];
        else if (mode.startsWith('end')) {
          response.status = 'end'; delete response.buses;
          response.next_service = { date: '2026-09-26', days_ahead: 1, day_type: 'weekend',
            bus: bus('07:10', mode === 'end' ? '07:42' : null) };
        } else if (mode === 'null') response.buses = [bus('15:41', null), ...buses.slice(1)];
        await route.fulfill({ json: response });
      });
      await page.goto(baseURL);
      await page.waitForFunction(() => document.querySelector('#next-time').textContent === '15:41 発 → 16:12 着');
      assert.deepEqual(await page.locator('.following-time').allTextContents(), ['15:56 発', '16:11 発 → 16:42 着']);
      const countdowns = await page.evaluate(() => [0, 1, 2, 59, 60, 82, 90, 120].map(formatMinutesUntil));
      assert.deepEqual(countdowns, ['まもなく', 'まもなく', 'あと2分', 'あと59分', 'あと1時間', 'あと1時間22分', 'あと1時間30分', 'あと2時間']);
      await page.locator('#timetable-toggle').click();
      await page.waitForFunction(() => document.querySelectorAll('.timetable-item').length === 4);
      assert.deepEqual(await page.locator('.timetable-time').allTextContents(),
        ['14:00 発 → 14:32 着', '15:41 発 → 16:12 着', '15:56 発', '16:11 発 → 16:42 着']);
      assert.equal(await page.locator('.timetable-item.is-past').count(), 1);
      assert.equal(await page.locator('.timetable-item.is-next[aria-current=true]').count(), 1);
      await noOverflow(page);
      if ([375, 1280].includes(width)) await page.screenshot({ path: path.join(output, `${width}-${colorScheme}.png`), fullPage: true });
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#timetable-toggle').getAttribute('aria-expanded'), 'false');
      assert.equal(await page.evaluate(() => document.activeElement.id), 'timetable-toggle');
      for (const direction of ['to_station', 'to_nakahashi', 'to_uni']) {
        await page.locator(`[data-direction="${direction}"]`).click();
        await page.waitForFunction(d => localStorage.getItem('kit-bus-direction') === d, direction);
        await page.waitForFunction(() => !document.querySelector('#refresh-button').disabled);
        assert.equal(await page.locator('#route-to').textContent(), details[direction].to);
        assert.ok((await page.locator('#next-stop').textContent()).startsWith(direction === 'to_uni' ? '着:' : '発:'));
      }
      await page.locator('[data-direction="to_station"]').click();
      await page.reload();
      await page.waitForFunction(() => document.querySelector('[data-direction=to_station]').getAttribute('aria-pressed') === 'true');
      mode = 'null';
      await page.locator('#refresh-button').click();
      await page.waitForFunction(() => document.querySelector('#next-time').textContent === '15:41 発');
      for (const endMode of ['end', 'end-null']) {
        mode = endMode;
        await page.locator('#refresh-button').click();
        await page.waitForFunction(() => !document.querySelector('#next-service-preview').hidden);
        await page.waitForFunction(expected => document.querySelector('#next-service-time').textContent === expected,
          endMode === 'end' ? '07:10 発 → 07:42 着' : '07:10 発');
        assert.equal(await page.locator('#next-service-date').textContent(), '明日');
        await noOverflow(page);
      }
      if (width === 320 && colorScheme === 'light') {
        await page.clock.install();
        // Restart the existing interval under the controlled browser clock.
        await page.evaluate(() => startAutoRefresh());
        const count = await page.evaluate(() => testFetches.length);
        await page.clock.fastForward(60000);
        await page.waitForFunction(n => testFetches.length > n, count);
        await page.waitForFunction(() => !document.querySelector('#refresh-button').disabled);
        await page.evaluate(() => {
          Object.defineProperty(document, 'hidden', { configurable: true, value: true });
          document.dispatchEvent(new Event('visibilitychange'));
        });
        const hiddenCount = await page.evaluate(() => testFetches.length);
        await page.clock.fastForward(120000);
        assert.equal(await page.evaluate(() => testFetches.length), hiddenCount);
        await page.evaluate(() => {
          delete document.hidden;
          document.dispatchEvent(new Event('visibilitychange'));
        });
        await page.waitForFunction(n => testFetches.length > n, hiddenCount);
      }
      assert.ok(await page.evaluate(() => testFetches.every(r => r.cache === 'no-store' && r.abortable)));
      assert.deepEqual(errors, []);
      cases++;
      await context.close();
    }
    // Real, unmocked production data smoke test for every API and direction.
    const page = await browser.newPage();
    await page.goto(baseURL);
    for (const endpoint of ['/healthz', ...Object.keys(details).flatMap(dir =>
      [`/api/next_bus?dir=${dir}`, `/api/timetable?dir=${dir}`])]) {
      const response = await page.request.get(baseURL + endpoint);
      assert.equal(response.status(), 200, endpoint);
      const data = await response.json();
      for (const row of data.buses || (data.next_service ? [data.next_service.bus] : [])) assert.ok('arrival_time' in row);
    }
    console.log(`PASS: ${cases} viewport/theme cases, 3 directions, arrival/null/end/timetable, countdowns, storage, focus, overflow, JS/manifest syntax, real local API smoke`);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
