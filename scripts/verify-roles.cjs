// Browser roles + live requests; no mocked API responses and no exported cookies/passwords.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
process.env.PLAYWRIGHT_BROWSERS_PATH ??= path.join(root, '.build/playwright-browsers');
const { chromium } = require(path.join(root, 'frontend/node_modules/playwright'));

async function main() {
  const ui = 'http://127.0.0.1:3001', api = 'http://127.0.0.1:8002/api/v1';
  const credentials = JSON.parse(fs.readFileSync(path.join(root, '.env.auth'), 'utf8').match(/^AUTH_BOOTSTRAP_USERS='(.*)'$/m)[1]);
  const out = path.join(root, 'output/hardening/roles'); fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const report = { status: 'running', checks: {}, page_errors: [] };
  const clients = [];
  async function client(role, suffix = '') {
    const context = await browser.newContext({ viewport: { width: 1440, height: 960 } }); clients.push(context);
    const page = await context.newPage(); page.setDefaultTimeout(90000);
    page.on('pageerror', e => report.page_errors.push(e.message));
    await page.goto(ui + '/login');
    if (role === 'customer') {
      await page.getByRole('button', { name: '客户注册', exact: true }).click();
      await page.getByLabel('用户名').fill('accept_' + Date.now() + suffix);
      await page.getByLabel('您的称呼').fill('权限隔离验收客户' + suffix);
      await page.getByLabel('密码', { exact: true }).fill('Private-Role-Fixture-2026!');
      await page.getByRole('button', { name: '注册并登录', exact: true }).click();
      await page.waitForURL(ui + '/customer');
    } else {
      const user = credentials.find(item => item.role === role);
      await page.getByLabel('用户名').fill(user.username); await page.getByLabel('密码', { exact: true }).fill(user.password);
      await page.getByRole('button', { name: '登录', exact: true }).click(); await page.waitForURL(ui + '/');
    }
    const me = await (await context.request.get(api + '/auth/me')).json();
    return { context, page, csrf: me.csrf_token };
  }
  async function request(client, method, endpoint, payload, expected = 200) {
    const response = await client.context.request.fetch(api + endpoint, { method, headers: { 'X-CSRF-Token': client.csrf }, data: payload });
    assert.equal(response.status(), expected, endpoint + ': ' + (await response.text())); return response.json();
  }
  try {
    const customer = await client('customer'), other = await client('customer', 'b');
    const created = await request(customer, 'POST', '/customer/tickets', { customer_name: '权限验收客户', subject: '退款', message: '商品损坏，申请180元退款，请按正常流程审批。', amount: 180, order_id: 'ORD-ROLE-222' }, 201);
    report.ticket_no = created.ticket_no;
    await request(customer, 'GET', '/tickets', undefined, 403);
    await request(other, 'GET', '/customer/tickets/' + created.id, undefined, 404);
    await request(other, 'POST', '/customer/tickets/' + created.id + '/confirmation', { resolved: true }, 404);
    report.checks.customer_private_ownership = true;
    const agent = await client('agent'); await agent.page.goto(ui + '/tickets/' + created.id);
    await agent.page.getByRole('button', { name: '运行 Agent 分析', exact: true }).click();
    await agent.page.getByRole('button', { name: '批准执行', exact: true }).waitFor();
    assert.equal(await agent.page.getByRole('button', { name: '批准执行', exact: true }).isDisabled(), true);
    await request(agent, 'POST', '/tickets/' + created.id + '/decision', { decision: 'approve', reviewer: 'spoofed' }, 403);
    await agent.page.screenshot({ path: path.join(out, 'agent-cannot-approve.png') });
    report.checks.agent_cannot_approve = true;
    const reviewer = await client('reviewer'); await reviewer.page.goto(ui + '/tickets/' + created.id);
    assert.equal(await reviewer.page.getByRole('button', { name: '发送回复', exact: true }).isDisabled(), true);
    const decisionResponse = reviewer.page.waitForResponse(r => r.url().endsWith('/decision') && r.request().method() === 'POST');
    await reviewer.page.getByRole('button', { name: '批准执行', exact: true }).click();
    assert.equal((await decisionResponse).status(), 200);
    const approved = await request(agent, 'GET', '/tickets/' + created.id);
    assert.equal(approved.approval.state, 'approved'); assert.equal(approved.approval.reviewed_by, 'reviewer');
    report.checks.reviewer_identity_not_spoofable = true;
    const before = approved.conversation.length;
    await request(agent, 'POST', '/tickets/' + created.id + '/draft/send', { response: '我们已联系承运商，正在补发商品。' }, 422);
    assert.equal((await request(agent, 'GET', '/tickets/' + created.id)).conversation.length, before);
    report.checks.unsafe_reply_not_sent = true;
    await request(agent, 'POST', '/tickets/' + created.id + '/draft/send', { response: '您好，审批已通过。当前尚未执行退款，本项目仅支持模拟资金操作，不代表真实到账。' });
    const received = await request(customer, 'GET', '/customer/tickets/' + created.id);
    assert.equal(received.stage, 'answered'); assert.notEqual(received.stage, 'resolved');
    await request(customer, 'POST', '/customer/tickets/' + created.id + '/messages', { message: '谢谢，请继续核实，我稍后确认。' });
    report.checks.reply_does_not_auto_resolve_and_can_continue = true;
    const admin = await client('admin'); await admin.page.goto(ui + '/accounts');
    await admin.page.getByRole('heading', { name: '账号管理', exact: true }).waitFor();
    await admin.page.locator('.account-row').first().waitFor();
    await admin.page.waitForLoadState('networkidle');
    await admin.page.screenshot({ path: path.join(out, 'accounts.png'), fullPage: true });
    const metrics = await request(admin, 'GET', '/performance/metrics');
    assert(metrics.models.ledger_calls > 0 && metrics.workflow.durable_runs > 0);
    assert.equal(metrics.models.cost_is_invoice, false);
    fs.writeFileSync(path.join(out, 'durable-metrics.json'), JSON.stringify(metrics, null, 2));
    report.checks.durable_metrics_and_admin_page = true;
    assert.deepEqual(report.page_errors, []); report.status = 'passed';
  } catch (error) { report.status = 'failed'; report.error = error.message; throw error; }
  finally { for (const context of clients) await context.close(); await browser.close(); fs.writeFileSync(path.join(out, 'roles-acceptance.json'), JSON.stringify(report, null, 2)); console.log(JSON.stringify(report)); }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
