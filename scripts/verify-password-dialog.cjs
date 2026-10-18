/* Password dialog acceptance with mocked APIs: never changes a real password. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn, spawnSync} = require('node:child_process');
const {randomUUID} = require('node:crypto');

const root = path.resolve(__dirname, '..');
const frontend = path.join(root, 'frontend');
process.env.PLAYWRIGHT_BROWSERS_PATH ??= path.join(root, '.build', 'playwright-browsers');
const {chromium} = require(path.join(frontend, 'node_modules', 'playwright'));
const port = process.env.RESOLVEAI_PASSWORD_TEST_PORT || '3317';
const webUrl = `http://127.0.0.1:${port}`;
const runId = randomUUID();
const output = path.join(root, 'output', 'password-dialog', runId);
fs.mkdirSync(path.join(root, 'tmp'), {recursive: true});
fs.mkdirSync(output, {recursive: true});
const app = fs.mkdtempSync(path.join(root, 'tmp', 'password-dialog-ui-'));
// Copy only source/assets and public configuration, never .env or a user DB.
for (const name of ['app', 'components', 'lib', 'public', 'package.json', 'next.config.ts', 'postcss.config.mjs', 'tsconfig.json', 'next-env.d.ts']) {
  fs.cpSync(path.join(frontend, name), path.join(app, name), {recursive: true});
}
fs.symlinkSync(path.join(frontend, 'node_modules'), path.join(app, 'node_modules'), process.platform === 'win32' ? 'junction' : 'dir');

let server;
let browser;
let logs = '';
const results = [];
async function ready() {
  for (let attempt = 0; attempt < 120; attempt++) {
    if (server.exitCode !== null) throw new Error('Isolated frontend exited: ' + logs.slice(-3000));
    try { if ((await fetch(webUrl + '/login', {signal: AbortSignal.timeout(3000)})).ok) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error('Isolated frontend did not become ready: ' + logs.slice(-3000));
}

async function openDialog(page, role) {
  if (role === 'admin' && !await page.getByRole('button', {name: '修改密码', exact: true}).isVisible()) {
    await page.getByRole('button', {name: '打开导航', exact: true}).click();
  }
  const trigger = page.getByRole('button', {name: '修改密码', exact: true});
  await trigger.click();
  const dialog = page.getByRole('dialog', {name: '修改密码', exact: true});
  await dialog.waitFor({state: 'visible'});
  await page.waitForFunction(() => document.querySelector('.password-dialog')?.matches(':modal'));
  return {dialog, trigger};
}

async function verifyPage(route, role, viewport, detailed) {
  const user = {id: `ui-${role}`, username: `ui_${role}`, display_name: role === 'admin' ? '界面测试管理员' : '界面测试客户', role, workspace_id: 'ui-test', active: true};
  const context = await browser.newContext({viewport});
  const page = await context.newPage();
  const errors = [];
  let mode = 'error';
  let passwordRequests = 0;
  let release;
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/api/v1/**', async route => {
    const request = route.request();
    const suffix = new URL(request.url()).pathname.replace('/api/v1', '');
    const reply = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (suffix === '/auth/me') return reply({user, csrf_token: 'ui-only-csrf'});
    if (suffix === '/auth/password' && request.method() === 'POST') {
      passwordRequests++;
      const body = request.postDataJSON();
      assert.ok(body.current_password && body.new_password, 'Both password fields must be submitted');
      if (mode === 'pending') await new Promise(resolve => { release = resolve; });
      if (mode === 'error') return reply({detail: '当前密码不正确，请重新输入'}, 400);
      return reply({user, csrf_token: 'ui-only-renewed-csrf'});
    }
    assert.equal(request.method(), 'GET', 'Unexpected write request during UI test');
    if (suffix === '/health') return reply({status: 'ok', agent_mode: 'demo', mcp_enabled: false});
    return reply([]);
  });
  try {
    await page.goto(webUrl + route);
    await page.locator('.session-actions').waitFor({state: 'attached'});
    const {dialog, trigger} = await openDialog(page, role);
    const geometry = await dialog.evaluate(element => {
      const rect = element.getBoundingClientRect();
      const top = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
      return {x: rect.x, y: rect.y, width: rect.width, height: rect.height,
        portal: element.parentElement === document.body, modal: element.matches(':modal'),
        topmost: top === element || element.contains(top), overflow: document.body.style.overflow};
    });
    assert.ok(geometry.portal && geometry.modal && geometry.topmost, 'Dialog must be topmost and outside navigation');
    assert.equal(geometry.overflow, 'hidden');
    assert.ok(geometry.x >= 8 && geometry.y >= 8 && geometry.x + geometry.width <= viewport.width - 8 && geometry.y + geometry.height <= viewport.height - 8, 'Dialog must fit inside viewport');
    assert.ok(Math.abs(geometry.x + geometry.width / 2 - viewport.width / 2) < 2 && Math.abs(geometry.y + geometry.height / 2 - viewport.height / 2) < 2, 'Dialog must be centered');
    for (const label of ['当前密码', '新密码']) {
      await dialog.getByLabel(label, {exact: true}).scrollIntoViewIfNeeded();
      assert.ok(await dialog.getByLabel(label, {exact: true}).isVisible());
    }
    const save = dialog.locator('.password-dialog-save');
    await save.scrollIntoViewIfNeeded();
    const saveBounds = await save.boundingBox();
    assert.ok(saveBounds && saveBounds.y >= 0 && saveBounds.y + saveBounds.height <= viewport.height, 'Save button must remain reachable in a short window');
    if (route === '/customer' && viewport.width === 1500 || route === '/accounts' && viewport.width === 390) {
      await page.screenshot({path: path.join(output, `${role}-${viewport.width}.png`)});
    }
    await page.keyboard.press('Escape');
    await dialog.waitFor({state: 'detached'});
    assert.equal(await page.evaluate(() => document.body.style.overflow), '');
    assert.ok(await trigger.evaluate(element => element === document.activeElement), 'Return focus to the trigger');

    if (detailed) {
      await openDialog(page, role);
      assert.equal(await dialog.getByLabel('当前密码', {exact: true}).inputValue(), '');
      assert.ok(await dialog.getByLabel('当前密码', {exact: true}).evaluate(element => element === document.activeElement));
      for (let count = 0; count < 12; count++) {
        await page.keyboard.press('Tab');
        assert.ok(await dialog.evaluate(element => document.activeElement === document.body || element.contains(document.activeElement)), 'Keyboard must not focus background controls');
      }
      await save.click();
      assert.equal(passwordRequests, 0, 'Empty form must not call password API');
      await dialog.getByLabel('当前密码', {exact: true}).fill('ui-only-current-password');
      await dialog.getByLabel('新密码', {exact: true}).fill('ui-only-new-password');
      await save.click();
      await dialog.getByRole('alert').waitFor();
      assert.equal(await dialog.getByRole('alert').textContent(), '当前密码不正确，请重新输入');
      assert.equal(passwordRequests, 1);
      await dialog.getByRole('button', {name: '取消', exact: true}).click();
      await dialog.waitFor({state: 'detached'});
      await openDialog(page, role);
      assert.equal(await dialog.getByLabel('当前密码', {exact: true}).inputValue(), '');
      assert.equal(await dialog.getByLabel('新密码', {exact: true}).inputValue(), '');
      assert.equal(await dialog.getByRole('alert').count(), 0);
      await dialog.getByRole('button', {name: '关闭修改密码', exact: true}).click();
      await dialog.waitFor({state: 'detached'});
      await openDialog(page, role);
      await page.mouse.click(3, 3);
      await dialog.waitFor({state: 'detached'});
      await openDialog(page, role);
      await dialog.getByLabel('当前密码', {exact: true}).fill('ui-only-current-password');
      await dialog.getByLabel('新密码', {exact: true}).fill('ui-only-new-password');
      mode = 'pending';
      await save.click();
      await page.waitForFunction(() => document.querySelector('.password-dialog-save')?.disabled);
      await page.keyboard.press('Escape');
      assert.ok(await dialog.isVisible(), 'Submitting must not dismiss on Escape');
      assert.equal(passwordRequests, 2, 'Submitting must not duplicate requests');
      assert.ok(await dialog.getByRole('button', {name: '取消', exact: true}).isDisabled());
      for (let attempt = 0; !release && attempt < 40; attempt++) await new Promise(resolve => setTimeout(resolve, 50));
      assert.equal(typeof release, 'function', 'Mock request must reach the test handler');
      mode = 'success';
      release();
      await dialog.waitFor({state: 'detached'});
      assert.equal(await page.evaluate(() => document.body.style.overflow), '');
      await openDialog(page, role);
      assert.equal(await dialog.getByLabel('新密码', {exact: true}).inputValue(), '');
      await page.keyboard.press('Escape');
    }
    assert.deepEqual(errors, [], 'No browser runtime errors');
    results.push({route, role, viewport, interactions: detailed ? 'layout, keyboard, cancel, backdrop, failure, saving, success' : 'layout, Escape, focus restoration'});
    console.log(`PASS ${role} ${route} ${viewport.width}x${viewport.height}`);
  } finally {
    if (release) release();
    await context.close();
  }
}

(async () => {
  try {
    // Do not reuse a user's existing frontend or backend service.
    try { const response = await fetch(webUrl + '/login', {signal: AbortSignal.timeout(1000)}); if (response) throw new Error('Test port already in use; choose RESOLVEAI_PASSWORD_TEST_PORT'); }
    catch (error) { if (error.message.includes('already in use')) throw error; }
    server = spawn(process.execPath, [path.join(frontend, 'node_modules/next/dist/bin/next'), 'dev', '--webpack', '--hostname', '127.0.0.1', '--port', port], {
      cwd: app, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
      env: {...process.env, NEXT_PUBLIC_API_URL: webUrl + '/api/v1', NEXT_DIST_DIR: '.next', NEXT_TELEMETRY_DISABLED: '1'},
    });
    server.stdout.on('data', data => { logs = (logs + data).slice(-12000); });
    server.stderr.on('data', data => { logs = (logs + data).slice(-12000); });
    await ready();
    browser = await chromium.launch({headless: true, ...(fs.existsSync(chromium.executablePath()) ? {} : {channel: 'msedge'})});
    for (const viewport of [{width:1500,height:1000},{width:1024,height:768},{width:800,height:600},{width:390,height:844},{width:320,height:568},{width:844,height:390}]) {
      for (const [route, role] of [['/customer','customer'], ['/customer/shop','customer'], ['/customer/orders','customer'], ['/accounts','admin']]) {
        await verifyPage(route, role, viewport, viewport.width === 1500 || viewport.width === 390);
      }
    }
    console.log(JSON.stringify({status: 'passed', cases: results.length, actual_password_changes: 0, screenshots: output}, null, 2));
  } catch (error) {
    console.error(error.stack || error.message);
    console.error(logs.slice(-3500));
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    if (server?.pid) {
      if (process.platform === 'win32') spawnSync('taskkill', ['/pid', String(server.pid), '/t', '/f'], {windowsHide:true, stdio:'ignore'});
      else server.kill('SIGTERM');
    }
  }
})();
