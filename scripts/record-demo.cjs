// Real browser acceptance and continuous video; no API mocks or fabricated UI states.
const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
process.env.PLAYWRIGHT_BROWSERS_PATH ??= path.join(root, '.build', 'playwright-browsers');
const { chromium } = require(path.join(root, 'frontend', 'node_modules', 'playwright'));

async function main() {
  const ui = process.env.ACCEPTANCE_UI_URL ?? 'http://127.0.0.1:3001';
  const api = process.env.ACCEPTANCE_API_URL ?? 'http://127.0.0.1:8002/api/v1';
  const out = path.join(root, 'output', 'hardening', 'browser');
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 },
    recordVideo: { dir: out, size: { width: 1440, height: 900 } } });
  const page = await context.newPage();
  page.setDefaultTimeout(180000);
  const evidence = { status: 'running', checks: {}, console_errors: [] };
  page.on('pageerror', error => evidence.console_errors.push(error.message));
  async function json(pathname) {
    const response = await context.request.get(api + pathname);
    assert.equal(response.status(), 200);
    return response.json();
  }
  async function screenshot(name) {
    const workbench = name.startsWith('02-') || name.startsWith('03-');
    if (!workbench) await page.evaluate(() => {
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
      window.scrollTo(0, 0);
    });
    await page.waitForTimeout(150);
    // Native viewport captures avoid Chromium's caret-resize/fixed-header artefact.
    await page.screenshot({ path: path.join(out, name + '.png'), fullPage: !workbench });
  }
  async function clickAndWait(button, endpoint) {
    // Attach both error handlers immediately; a click failure must not leave an unhandled wait promise.
    const [finished] = await Promise.all([
      page.waitForResponse(r => r.url().endsWith(endpoint) && r.request().method() === 'POST', { timeout: 30000 }),
      button.click({ timeout: 30000 }),
    ]);
    assert.equal(finished.status(), 200);
    return finished.json();
  }
  try {
    const privateText = fs.readFileSync(path.join(root, '.env.auth'), 'utf8');
    const credentials = JSON.parse(privateText.match(/^AUTH_BOOTSTRAP_USERS='(.*)'$/m)[1]).find(item => item.role === 'admin');
    await page.goto(ui + '/login');
    await page.getByLabel('用户名').fill(credentials.username);
    await page.getByLabel('密码', { exact: true }).fill(credentials.password);
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page.waitForURL(ui + '/');
    await page.goto(ui + '/customer');
    await page.getByLabel('您的称呼').fill('双端录屏验收客户');
    await page.getByLabel('问题类型').selectOption('退款与售后');
    await page.getByLabel('订单编号（选填）').fill('ORD-VIDEO-232');
    await page.getByLabel('涉及金额（选填）').fill('180');
    await page.getByLabel('问题描述').fill('商品有质量问题。我明确申请退款180元，不是只询问政策，请给出180元的待人工审批退款方案。本次为项目模拟资金流程，尚未执行退款，请按正常流程审批。');
    await screenshot('01-customer-submit');
    const createResponse = page.waitForResponse(r => r.url().endsWith('/customer/tickets') && r.request().method() === 'POST');
    await page.getByRole('button', { name: '提交问题', exact: true }).click();
    const created = await (await createResponse).json();
    evidence.ticket_id = created.id;
    evidence.ticket_no = created.ticket_no;
    await page.getByRole('link', { name: /进入客服工作台/ }).click();
    assert.equal(context.pages().length, 1);
    evidence.checks.same_window_navigation = true;
    await page.goto(ui + '/tickets/' + created.id);
    await page.getByRole('button', { name: '运行 Agent 分析', exact: true }).click();
    await page.getByRole('button', { name: '批准执行', exact: true }).waitFor();
    await screenshot('02-real-agent-approval');
    const pending = await json('/tickets/' + created.id);
    assert.equal(pending.assessment.customer_context.tool_source, 'mcp-server');
    assert.ok(pending.assessment.model_trace.every(call => call.skipped || (call.provider === 'deepseek' && !call.fallback)));
    evidence.checks.real_deepseek_and_mcp = true;
    assert.equal(pending.approval.amount, 180, 'General manual review is not authorization for a specific refund amount');
    assert.ok(pending.approval.action.includes('退款'), 'A general review cannot be treated as a refund approval');
    await clickAndWait(page.getByRole('button', { name: '批准执行', exact: true }), '/tickets/' + created.id + '/decision');
    await clickAndWait(page.getByRole('button', { name: '执行退款', exact: true }), '/tickets/' + created.id + '/tools/execute_refund/execute');
    await page.getByRole('button', { name: '退款已执行', exact: true }).waitFor();
    evidence.checks.approved_simulated_refund = true;
    await clickAndWait(page.getByRole('button', { name: '改写', exact: true }), '/tickets/' + created.id + '/draft/rewrite');
    await page.getByRole('button', { name: '采用', exact: true }).click();
    assert.ok((await page.locator('.composer textarea').inputValue()).length > 20);
    await screenshot('03-adopt-rewrite-and-send');
    await clickAndWait(page.getByRole('button', { name: '发送回复', exact: true }), '/tickets/' + created.id + '/draft/send');
    assert.equal((await json('/tickets/' + created.id)).status, 'awaiting_customer');
    evidence.checks.adopt_rewrite_send_no_auto_resolve = true;
    await page.goto(ui + '/customer');
    await page.getByRole('button', { name: '问题已解决', exact: true }).waitFor();
    assert.equal(await page.locator('.customer-message-row.user').count(), 1);
    assert.ok(decodeURIComponent(await page.locator('.customer-support-avatar img').first().getAttribute('src')).includes('customer-mascot-original.jpg'));
    evidence.checks.user_right_support_original_avatar = true;
    await page.locator('.customer-chat-composer textarea').fill('请说明退款到账时间，我只是查询进度，不申请新的退款。');
    await clickAndWait(page.getByRole('button', { name: '发送消息', exact: true }), '/customer/tickets/' + created.id + '/messages');
    await screenshot('04-customer-follow-up');
    await page.goto(ui + '/tickets/' + created.id);
    await page.getByRole('button', { name: /运行 Agent/ }).click();
    await page.waitForFunction(() => document.querySelector('.draft-tag') && !document.querySelector('.background-task-progress'), null, { timeout: 180000 });
    const updated = await json('/tickets/' + created.id);
    assert.equal(updated.status, 'in_progress');
    await page.getByRole('button', { name: '采用', exact: true }).click();
    await clickAndWait(page.getByRole('button', { name: '发送回复', exact: true }), '/tickets/' + created.id + '/draft/send');
    await page.goto(ui + '/customer');
    await page.getByRole('button', { name: '问题已解决', exact: true }).waitFor();
    assert.equal(await page.locator('.customer-message-row.user').count(), 2);
    await clickAndWait(page.getByRole('button', { name: '问题已解决', exact: true }), '/customer/tickets/' + created.id + '/confirmation');
    await page.getByText('本次咨询已完成', { exact: true }).waitFor();
    await screenshot('05-customer-confirmed');
    assert.equal((await json('/tickets/' + created.id)).status, 'resolved');
    evidence.checks.multiple_turns_customer_confirmed_resolution = true;
    assert.equal(evidence.console_errors.length, 0);
    evidence.status = 'passed';
    await page.waitForTimeout(1500);
  } catch (error) {
    evidence.status = 'failed'; evidence.error = error.message;
    await screenshot('failure').catch(() => {});
    throw error;
  } finally {
    const video = page.video();
    await context.close();
    if (video) { await video.saveAs(path.join(out, 'resolveai-demo.webm')); evidence.video = 'resolveai-demo.webm'; }
    await browser.close();
    fs.writeFileSync(path.join(out, 'browser-acceptance.json'), JSON.stringify(evidence, null, 2));
    console.log(JSON.stringify(evidence, null, 2));
  }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
