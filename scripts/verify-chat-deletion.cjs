/* Isolated customer chat UI checks. All APIs are mocked; no real records are removed. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawn, spawnSync} = require('node:child_process');
const {randomUUID} = require('node:crypto');

const root = path.resolve(__dirname, '..');
const frontend = path.join(root, 'frontend');
process.env.PLAYWRIGHT_BROWSERS_PATH ??= path.join(root, '.build', 'playwright-browsers');
const {chromium} = require(path.join(frontend, 'node_modules', 'playwright'));
const port = process.env.RESOLVEAI_CHAT_DELETE_TEST_PORT || '3318';
const webUrl = `http://127.0.0.1:${port}`;
const output = path.join(root, 'output', 'chat-deletion', randomUUID());
fs.mkdirSync(path.join(root, 'tmp'), {recursive: true});
fs.mkdirSync(output, {recursive: true});
const app = fs.mkdtempSync(path.join(root, 'tmp', 'chat-deletion-ui-'));
for (const name of ['app', 'components', 'lib', 'public', 'package.json', 'next.config.ts', 'postcss.config.mjs', 'tsconfig.json', 'next-env.d.ts']) {
  fs.cpSync(path.join(frontend, name), path.join(app, name), {recursive: true});
}
fs.symlinkSync(path.join(frontend, 'node_modules'), path.join(app, 'node_modules'), process.platform === 'win32' ? 'junction' : 'dir');
let server;
let browser;
let logs = '';

async function ready() {
  for (let attempt = 0; attempt < 120; attempt++) {
    if (server.exitCode !== null) throw new Error('Isolated frontend exited: ' + logs.slice(-2000));
    try { if ((await fetch(webUrl + '/login', {signal: AbortSignal.timeout(3000)})).ok) return; } catch {}
    await new Promise(resolve => setTimeout(resolve, 500));
  }
  throw new Error('Isolated frontend did not become ready: ' + logs.slice(-2000));
}

function chat(id, subject, number) {
  return {id, subject, ticket_no: number, customer_name: '界面测试客户', message: subject, stage: 'answered',
    handed_off: false, handoff_reasons: [], customer_confirmed: null,
    created_at: '2026-10-18T08:00:00Z', updated_at: '2026-10-18T08:00:01Z',
    messages: [{id: `${id}-question`, role: 'customer', content: subject, created_at: '2026-10-18T08:00:00Z'},
      {id: `${id}-answer`, role: 'support', content: `您好，这是${subject}的回复。`, created_at: '2026-10-18T08:00:01Z'}]};
}

async function verify(viewport) {
  const user = {id: 'chat-ui-customer', username: 'chat_ui_customer', display_name: '界面测试客户', role: 'customer', workspace_id: 'ui-test', active: true};
  const storageKey = `resolveai_customer_ticket_id:${user.id}`;
  const chats = [chat('chat-1', '蓝牙耳机咨询', '26-10-18-1'), chat('chat-2', '键盘选购咨询', '26-10-18-2')];
  const hidden = new Set();
  const deleted = [];
  let mode = 'error';
  let releaseDelete;
  let holdNextRead = false;
  let releaseRead;
  let creates = 0;
  const context = await browser.newContext({viewport});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/api/v1/**', async route => {
    const request = route.request();
    const suffix = new URL(request.url()).pathname.replace('/api/v1', '');
    const reply = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (suffix === '/auth/me') return reply({user, csrf_token: 'ui-only-csrf'});
    if (suffix === '/customer/tickets' && request.method() === 'GET') return reply(chats.filter(item => !hidden.has(item.id)));
    if (suffix === '/customer/tickets' && request.method() === 'POST') {
      creates++;
      const item = chat('new-chat', request.postDataJSON().message, '26-10-18-3');
      chats.push(item);
      return reply(item, 201);
    }
    const match = suffix.match(/^\/customer\/tickets\/([^/]+)$/);
    if (match && request.method() === 'DELETE') {
      assert.equal(request.headers()['x-csrf-token'], 'ui-only-csrf');
      deleted.push(match[1]);
      if (mode === 'error') return reply({detail: '删除失败，请稍后重试'}, 503);
      if (mode === 'pending') await new Promise(resolve => { releaseDelete = resolve; });
      hidden.add(match[1]);
      return reply({id: match[1], deleted: true});
    }
    if (match && request.method() === 'GET') {
      const snapshot = chats.find(item => item.id === match[1]);
      if (holdNextRead) {
        holdNextRead = false;
        await new Promise(resolve => { releaseRead = resolve; });
        return reply(snapshot); // Deliberately stale poll: must not resurrect a deleted chat.
      }
      return hidden.has(match[1]) ? reply({detail: '对话不存在'}, 404) : reply(snapshot);
    }
    assert.equal(request.method(), 'GET', 'Unexpected write request');
    return reply([]);
  });
  const row = id => page.locator(`[data-conversation-id="${id}"]`);
  const openDelete = async id => {
    await row(id).getByRole('button', {name: /^删除聊天：/}).click();
    const dialog = page.getByRole('dialog', {name: '删除这条聊天？'});
    await dialog.waitFor();
    return dialog;
  };
  try {
    await page.goto(webUrl + '/customer');
    await row('chat-1').waitFor();
    await row('chat-1').locator('.assistant-history-open').click();
    await page.getByRole('log').getByText('您好，这是蓝牙耳机咨询的回复。', {exact: true}).waitFor();
    await page.getByRole('textbox', {name: '聊天消息'}).fill('预算300元');
    let dialog = await openDelete('chat-2');
    assert.ok(await dialog.getByRole('button', {name: '取消', exact: true}).evaluate(element => element === document.activeElement));
    assert.equal(await dialog.evaluate(element => element.matches(':modal') && element.parentElement === document.body), true);
    const bounds = await dialog.boundingBox();
    assert.ok(bounds.x >= 8 && bounds.y >= 8 && bounds.x + bounds.width <= viewport.width - 8 && bounds.y + bounds.height <= viewport.height - 8);
    await page.screenshot({path: path.join(output, `confirmation-${viewport.width}.png`)});
    await page.keyboard.press('Escape');
    await dialog.waitFor({state: 'detached'});
    assert.equal(deleted.length, 0, 'Cancel must not send DELETE');
    assert.equal(await page.getByRole('textbox', {name: '聊天消息'}).inputValue(), '预算300元');
    dialog = await openDelete('chat-2');
    await dialog.getByRole('button', {name: '确认删除聊天', exact: true}).click();
    await dialog.getByRole('alert').waitFor();
    assert.equal(await row('chat-2').count(), 1);
    await dialog.getByRole('button', {name: '取消', exact: true}).click();
    mode = 'pending';
    dialog = await openDelete('chat-2');
    await dialog.getByRole('button', {name: '确认删除聊天', exact: true}).click();
    await dialog.getByRole('button', {name: '正在删除…', exact: true}).waitFor();
    assert.ok(await dialog.getByRole('button', {name: '取消', exact: true}).isDisabled());
    await page.keyboard.press('Escape');
    assert.ok(await dialog.isVisible());
    await dialog.locator('form').evaluate(form => form.requestSubmit());
    for (let attempt = 0; !releaseDelete && attempt < 40; attempt++) await new Promise(resolve => setTimeout(resolve, 50));
    assert.equal(deleted.length, 2, 'Busy state must prevent duplicate DELETE');
    releaseDelete();
    await dialog.waitFor({state: 'detached'});
    assert.equal(await row('chat-2').count(), 0);
    assert.equal(await page.getByRole('textbox', {name: '聊天消息'}).inputValue(), '预算300元');
    assert.equal(await page.evaluate(key => localStorage.getItem(key), storageKey), 'chat-1');
    await page.reload();
    await row('chat-1').waitFor();
    await page.getByRole('log').getByText('您好，这是蓝牙耳机咨询的回复。', {exact: true}).waitFor();
    assert.equal(await row('chat-2').count(), 0, 'Reload must not restore an inactive deleted chat');
    holdNextRead = true;
    for (let attempt = 0; !releaseRead && attempt < 100; attempt++) await new Promise(resolve => setTimeout(resolve, 50));
    assert.equal(typeof releaseRead, 'function', 'A pending conversation poll must be exercised');
    mode = 'success';
    dialog = await openDelete('chat-1');
    await dialog.getByRole('button', {name: '确认删除聊天', exact: true}).click();
    await dialog.waitFor({state: 'detached'});
    releaseRead();
    await page.locator('.assistant-welcome h1').waitFor();
    assert.equal(await page.locator('.assistant-history-item').count(), 0);
    assert.equal(await page.evaluate(key => localStorage.getItem(key), storageKey), null);
    await page.reload();
    await page.getByRole('textbox', {name: '聊天消息'}).waitFor();
    await page.waitForFunction(() => !document.querySelector('.assistant-composer textarea')?.disabled);
    assert.equal(await page.locator('.assistant-history-item').count(), 0);
    await page.screenshot({path: path.join(output, `empty-${viewport.width}.png`)});
    await page.getByRole('textbox', {name: '聊天消息'}).fill('帮我找一款水杯');
    await page.getByRole('button', {name: '发送消息', exact: true}).click();
    await row('new-chat').waitFor();
    assert.equal(creates, 1, 'A new message after deletion must create a fresh chat');
    assert.deepEqual(deleted, ['chat-2', 'chat-2', 'chat-1']);
    assert.deepEqual(errors, []);
    console.log(`PASS ${viewport.width}x${viewport.height}: cancel, failure, retry, busy, inactive/active deletion, stale poll, refresh, new chat`);
  } finally {
    releaseDelete?.();
    releaseRead?.();
    await context.close();
  }
}

(async () => {
  try {
    try { if (await fetch(webUrl + '/login', {signal: AbortSignal.timeout(1000)})) throw new Error('Test port already in use; choose RESOLVEAI_CHAT_DELETE_TEST_PORT'); }
    catch (error) { if (error.message.includes('already in use')) throw error; }
    server = spawn(process.execPath, [path.join(frontend, 'node_modules/next/dist/bin/next'), 'dev', '--webpack', '--hostname', '127.0.0.1', '--port', port], {
      cwd: app, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
      env: {...process.env, NEXT_PUBLIC_API_URL: webUrl + '/api/v1', NEXT_DIST_DIR: '.next', NEXT_TELEMETRY_DISABLED: '1'},
    });
    server.stdout.on('data', data => { logs = (logs + data).slice(-12000); });
    server.stderr.on('data', data => { logs = (logs + data).slice(-12000); });
    await ready();
    browser = await chromium.launch({headless: true, ...(fs.existsSync(chromium.executablePath()) ? {} : {channel: 'msedge'})});
    for (const viewport of [{width: 1500, height: 1000}, {width: 390, height: 844}, {width: 320, height: 568}]) await verify(viewport);
    console.log(JSON.stringify({status: 'passed', cases: 3, actual_record_deletions: 0, screenshots: output}, null, 2));
  } catch (error) {
    console.error(error.stack || error.message);
    console.error(logs.slice(-2500));
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    if (server?.pid) {
      if (process.platform === 'win32') spawnSync('taskkill', ['/pid', String(server.pid), '/t', '/f'], {windowsHide: true, stdio: 'ignore'});
      else server.kill('SIGTERM');
    }
  }
})();

