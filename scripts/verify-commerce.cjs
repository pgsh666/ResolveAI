/* Isolated browser acceptance: no business DB and no paid model calls. */
const {spawn} = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
process.env.PLAYWRIGHT_BROWSERS_PATH ??= path.resolve(__dirname, '../.build/playwright-browsers');
const {chromium} = require('../frontend/node_modules/playwright');

const root = path.resolve(__dirname, '..');
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'resolveai-shop-browser-'));
const apiUrl = 'http://127.0.0.1:8200/api/v1';
const webUrl = 'http://127.0.0.1:3200';
const password = 'Test-' + randomUUID();
const customerPassword = '1'; // Isolated test account only: prove no signup minimum/format rules.
const environment = {...process.env, APP_ENV:'test', AUTH_ENABLED:'true', AGENT_MODE:'demo', EMBEDDING_PROVIDER:'local',
  DATABASE_URL:'sqlite:///' + path.join(temp,'browser.db').replaceAll('\\','/'), CORS_ORIGINS:webUrl,
  MCP_ENABLED:'false', ASYNC_TASKS_ENABLED:'false', OTEL_ENABLED:'false', RATE_LIMIT_ENABLED:'false',
  AUTH_BOOTSTRAP_USERS:JSON.stringify([{username:'shop_admin',display_name:'商户测试管理员',role:'admin',password}])};
const children = [];
let browser;
let logs = '';
function start(executable, args, cwd, env) {
  const child = spawn(executable, args, {cwd, env, windowsHide:true, stdio:['ignore','pipe','pipe']});
  children.push(child);
  child.stdout.on('data', c => {logs += String(c).slice(-2000);});
  child.stderr.on('data', c => {logs += String(c).slice(-2000);});
  child.on('error', e => {logs += e.message;});
  child.unref();
  return child;
}
async function ready(url) {
  for (let i=0;i<120;i++) {try {if((await fetch(url)).ok) return;} catch {} await new Promise(r => setTimeout(r,500));}
  throw new Error('Isolated service not ready: ' + url + '\n' + logs.slice(-3000));
}
async function api(context, method, suffix, payload) {
  const me = await context.request.get(apiUrl + '/auth/me');
  const csrf = me.ok() ? (await me.json()).csrf_token : '';
  const r = await context.request.fetch(apiUrl+suffix,{method, data:payload, headers:{'X-CSRF-Token':csrf}});
  assert.ok(r.ok(), `${method} ${suffix}: ${r.status()} ${await r.text()}`);
  return r.json();
}
async function login(page, username, target='', loginPassword=password) {
  await page.goto(webUrl+'/login' + (target ? '?next='+encodeURIComponent(target) : ''));
  await page.getByLabel('用户名', {exact:true}).fill(username);
  await page.getByLabel('密码', {exact:true}).fill(loginPassword);
  await page.getByRole('button', {name:'登录',exact:true}).click();
  await page.waitForURL(url => url.pathname !== '/login');
}
async function visibleImagesReady(page) {
  await page.waitForFunction(() => {
    const images = [...document.querySelectorAll('.assistant-chat-card img')];
    return images.length > 0 && images.every(img => img.complete && img.naturalWidth > 0);
  });
}

(async () => {
  try {
    const python = process.env.RESOLVEAI_TEST_PYTHON || 'python';
    start(python,['-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8200'],path.join(root,'backend'),environment);
    const productionUI=process.env.RESOLVEAI_UI_PRODUCTION === '1';
    const nextEnvironment={...process.env, NEXT_PUBLIC_API_URL:apiUrl, NEXT_DIST_DIR:'.next-commerce-test'};
    const nextExecutable=path.join(root,'frontend/node_modules/next/dist/bin/next');
    if(productionUI) {
      const build=start(process.execPath,[nextExecutable,'build'],path.join(root,'frontend'),nextEnvironment);
      await new Promise((resolve,reject)=>{build.once('exit',code=>code === 0 ? resolve() : reject(new Error('Isolated production build failed: '+logs.slice(-2000)))); build.once('error',reject);});
    }
    start(process.execPath,[nextExecutable,productionUI ? 'start' : 'dev','--hostname','127.0.0.1','--port','3200'],path.join(root,'frontend'),nextEnvironment);
    await Promise.all([ready(apiUrl+'/health'),ready(webUrl+'/login')]);
    browser = await chromium.launch({headless:true, ...(fs.existsSync(chromium.executablePath()) ? {} : {channel:'msedge'})});
    const merchant = await browser.newContext({viewport:{width:1500,height:1000}});
    const customer = await browser.newContext({viewport:{width:1500,height:1000}});
    const adminPage = await merchant.newPage();
    const customerPage = await customer.newPage();
    const errors=[];
    for(const p of [adminPage,customerPage]) p.on('pageerror', e => errors.push(e.message));
    for (const role of ['admin']) {
      const staffContext = await browser.newContext();
      const staffPage = await staffContext.newPage();
      staffPage.on('pageerror', e => errors.push(e.message));
      await login(staffPage, 'shop_'+role, '/customer/shop');
      await staffPage.waitForURL(webUrl+'/');
      assert.equal(await staffPage.locator('.customer-header').count(),0);
      assert.equal(await staffPage.getByRole('link',{name:'客户端',exact:true}).count(),0);
      for (const target of ['/customer','/customer/shop','/customer/orders']) {
        await staffPage.goto(webUrl+target);
        await staffPage.waitForURL(webUrl+'/');
        assert.equal(await staffPage.locator('.customer-header').count(),0);
      }
      const me = await (await staffContext.request.get(apiUrl+'/auth/me')).json();
      assert.equal(me.user.role,role);
      for (const target of ['/customer/products','/customer/orders','/customer/tickets']) {
        assert.equal((await staffContext.request.get(apiUrl+target)).status(),403);
      }
      assert.equal((await staffContext.request.post(apiUrl+'/customer/tickets',{data:{message:'工作人员不能作为客户提问'},headers:{'X-CSRF-Token':me.csrf_token}})).status(),403);
      await staffContext.close();
    }
    await login(adminPage, 'shop_admin');
    // This catalog is created explicitly in this script's disposable SQLite DB.
    // Normal application startup does not create products or tickets.
    for(const [name,description,price,stock] of [
      ['轻便蓝牙耳机','入耳式蓝牙耳机，适合通勤，续航约 6 小时。',129,30],
      ['降噪蓝牙耳机','头戴式蓝牙耳机，支持主动降噪，续航约 20 小时。',299,20],
      ['无线鼠标','办公用无线鼠标，轻巧便携。',59,50],
      ['机械键盘','87 键机械键盘，适用于办公和游戏。',199,25],
      ['保温水杯','500ml 保温水杯，食品接触级内胆。',79,40],
    ]) await api(merchant,'POST','/products',{name,description,price,stock,category:'数码'});
    const nav = adminPage.getByRole('navigation',{name:'主导航',exact:true});
    assert.deepEqual(await nav.locator('.admin-primary-list > li > a, .admin-primary-list > li > button').allTextContents(), ['总览','管理','Agent']);
    assert.deepEqual(await nav.locator('#management-menu > a, #management-menu > button').allTextContents(), ['商品管理','客户管理','项目设置','提问与路由记录']);
    assert.deepEqual(await nav.locator('#customers-menu > a').allTextContents(), ['聊天管理','订单管理','售后处理']);
    assert.deepEqual(await nav.locator('#agent-menu > a').allTextContents(), ['Agent运行','知识库','效果分析']);
    assert.equal(await nav.getByRole('link',{name:'人工审批',exact:true}).count(),0);
    await adminPage.screenshot({path:path.join(temp,'merchant-sidebar-desktop.png'),fullPage:true});
    await adminPage.goto(webUrl+'/accounts');
    await adminPage.getByRole('heading',{name:'我的账号',exact:true}).waitFor();
    assert.equal(await adminPage.locator('select').count(),0);
    assert.equal(await adminPage.locator('.account-row').count(),0);
    assert.ok(await adminPage.getByLabel('登录名',{exact:true}).getAttribute('readonly') !== null);
    await adminPage.getByLabel('显示名称',{exact:true}).fill('验收管理员');
    await adminPage.getByRole('button',{name:'保存个人资料',exact:true}).click();
    await adminPage.getByText('个人资料已保存。',{exact:true}).waitFor();
    await adminPage.screenshot({path:path.join(temp,'own-account.png'),fullPage:true});
    for (const width of [700,390]) {
      await adminPage.setViewportSize({width,height:700});
      assert.ok(await adminPage.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),'Small merchant window must not overflow');
      await adminPage.getByRole('button',{name:'打开导航',exact:true}).click();
      await nav.getByRole('link',{name:'聊天管理',exact:true}).waitFor({state:'visible'});
      await nav.getByRole('link',{name:'效果分析',exact:true}).scrollIntoViewIfNeeded();
      await adminPage.screenshot({path:path.join(temp,`merchant-sidebar-${width}.png`),fullPage:true});
      await nav.getByRole('link',{name:'效果分析',exact:true}).click();
      await adminPage.waitForURL(webUrl+'/analytics');
      assert.equal(await adminPage.locator('#merchant-sidebar.is-open').count(),0);
      await adminPage.goto(webUrl+'/accounts');
    }
    await adminPage.setViewportSize({width:1500,height:1000});
    await adminPage.goto(webUrl+'/products');
    await adminPage.getByRole('button',{name:'上架新商品'}).click();
    await adminPage.getByLabel('商品名称',{exact:true}).fill('浏览器验收耳机');
    await adminPage.getByLabel('分类',{exact:true}).fill('数码');
    await adminPage.getByLabel('商品描述',{exact:true}).fill('轻便蓝牙耳机，适合通勤，续航约 6 小时。演示商品。');
    await adminPage.getByLabel('售价（元）',{exact:true}).fill('88.50');
    await adminPage.getByLabel('可售库存',{exact:true}).fill('5');
    await adminPage.getByLabel('商品图片（最多 2MB）').setInputFiles({name:'test.png', mimeType:'image/png', buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aW9sAAAAASUVORK5CYII=','base64')});
    await adminPage.getByRole('button',{name:'保存商品'}).click();
    await adminPage.locator('.product-card').filter({hasText:'浏览器验收耳机'}).waitFor();
    await customerPage.goto(webUrl+'/login');
    await customerPage.getByRole('button',{name:'用户注册',exact:true}).click();
    await customerPage.getByLabel('用户名',{exact:true}).fill('shop_customer');
    await customerPage.getByLabel('您的称呼',{exact:true}).fill('购物测试客户');
    assert.equal(await customerPage.getByLabel('密码',{exact:true}).getAttribute('minlength'),null);
    await customerPage.getByLabel('密码',{exact:true}).fill(customerPassword);
    await customerPage.getByRole('button',{name:'注册',exact:true}).click();
    await customerPage.getByText('注册成功，请使用刚才的账号和密码登录。',{exact:true}).waitFor();
    assert.equal((await customer.request.get(apiUrl+'/auth/me')).status(),401);
    await login(customerPage,'shop_customer','',customerPassword);
    await customerPage.waitForURL(webUrl+'/customer');
    assert.equal(await customerPage.getByRole('link',{name:'商户后台',exact:true}).count(),0);
    assert.ok((await customerPage.locator('.session-actions > span').innerText()).endsWith('· 客户'));
    const adminMe = await api(merchant,'GET','/auth/me');
    const customerMe = await api(customer,'GET','/auth/me');
    assert.deepEqual((await api(merchant,'GET','/auth/users')).map(a=>a.id),[adminMe.user.id]);
    assert.equal((await merchant.request.patch(apiUrl+'/auth/users/'+customerMe.user.id,{data:{display_name:'不应改动'},headers:{'X-CSRF-Token':adminMe.csrf_token}})).status(),403);
    assert.equal((await merchant.request.post(apiUrl+'/auth/users',{data:{username:'illegal_customer',password,role:'customer',display_name:'不允许创建'},headers:{'X-CSRF-Token':adminMe.csrf_token}})).status(),403);
    assert.equal((await api(customer,'GET','/auth/me')).user.display_name,'购物测试客户');
    await customerPage.goto(webUrl+'/orders');
    await customerPage.waitForURL(webUrl+'/customer');
    assert.equal(await customerPage.locator('.customer-form').count(),0);
    await customerPage.locator('.assistant-welcome').waitFor();
    assert.equal(await customerPage.getByRole('button',{name:'帮我找一款蓝牙耳机',exact:true}).count(),1);
    assert.equal(await customerPage.getByRole('button',{name:'比较商城商品价格',exact:true}).count(),1);
    assert.equal(await customerPage.getByRole('button',{name:'查看我的订单物流',exact:true}).count(),1);
    assert.equal(await customerPage.getByText('帮我规划学习 Python 的步骤',{exact:true}).count(),0);
    await visibleImagesReady(customerPage);
    await customerPage.screenshot({path:path.join(temp,'assistant-welcome.png'),fullPage:true});
    await customerPage.getByRole('button',{name:'帮我找一款蓝牙耳机',exact:true}).click();
    await customerPage.locator('.customer-message-row.support').waitFor();
    assert.ok((await customerPage.locator('.customer-message-row.support').innerText()).includes('浏览器验收耳机'));
    assert.equal(await customerPage.getByText('已转人工客服：',{exact:false}).count(),0);
    const ordinary = (await api(customer,'GET','/customer/tickets'))[0];
    await adminPage.goto(webUrl+'/chats');
    await adminPage.locator('.list-row').filter({hasText:ordinary.ticket_no}).waitFor();
    assert.ok((await adminPage.locator('.list-row').filter({hasText:ordinary.ticket_no}).innerText()).includes('普通咨询'));
    await adminPage.locator('.list-row').filter({hasText:ordinary.ticket_no}).click();
    await adminPage.waitForURL(webUrl+'/chats/'+ordinary.id);
    assert.equal(await adminPage.locator('.back-button').getAttribute('href'),'/chats');
    await adminPage.goto(webUrl+'/tickets');
    await adminPage.getByRole('heading',{name:'售后处理',exact:true}).waitFor();
    await adminPage.locator('.inline-loading').waitFor({state:'hidden'});
    assert.equal(await adminPage.locator('.list-row').filter({hasText:ordinary.ticket_no}).count(),0);
    await customerPage.getByRole('textbox',{name:'聊天消息'}).fill('商城有没有耳机？');
    await customerPage.getByRole('button',{name:'发送消息',exact:true}).click();
    await customerPage.locator('.customer-message-row.support').nth(1).waitFor();
    assert.ok((await customerPage.locator('.customer-message-row.support').nth(1).innerText()).includes('浏览器验收耳机'));
    await visibleImagesReady(customerPage);
    await customerPage.screenshot({path:path.join(temp,'assistant-chat.png'),fullPage:true});
    await customerPage.reload();
    await customerPage.locator('.customer-message-row.support').nth(1).waitFor();
    assert.equal(await customerPage.locator('.customer-message-row.user').count(),2);
    const history = customerPage.getByRole('complementary',{name:'对话记录'});
    await history.getByRole('button',{name:'新对话',exact:true}).click();
    await customerPage.locator('.assistant-welcome').waitFor();
    await history.getByRole('button').filter({hasText:'帮我找一款蓝牙耳机'}).click();
    await customerPage.locator('.customer-message-row.support').nth(1).waitFor();
    await customerPage.setViewportSize({width:390,height:844});
    assert.ok(await customerPage.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),'Mobile chat must not overflow horizontally');
    await customerPage.screenshot({path:path.join(temp,'assistant-mobile.png'),fullPage:true});
    await customerPage.setViewportSize({width:1500,height:1000});
    await history.getByRole('button',{name:'新对话',exact:true}).click();
    for (const [index, message] of ['比较商城商品价格','有没有更便宜的键盘','什么时候上架新的'].entries()) {
      await customerPage.getByRole('textbox',{name:'聊天消息'}).fill(message);
      await customerPage.getByRole('button',{name:'发送消息',exact:true}).click();
      await customerPage.locator('.customer-message-row.support').nth(index).waitFor();
    }
    assert.ok((await customerPage.locator('.customer-message-row.support').nth(2).innerText()).includes('没有记录键盘的上新或补货计划，暂时无法确认具体时间'));
    assert.equal(await customerPage.getByText('已转人工客服：',{exact:false}).count(),0);
    await customerPage.screenshot({path:path.join(temp,'catalog-followup.png'),fullPage:true});
    await customerPage.getByRole('textbox',{name:'聊天消息'}).fill('有优惠券吗');
    await customerPage.getByRole('button',{name:'发送消息',exact:true}).click();
    await customerPage.locator('.customer-message-row.support').nth(3).waitFor();
    const promotionReply = await customerPage.locator('.customer-message-row.support').nth(3).innerText();
    assert.ok(promotionReply.includes('不能确认是否有可用优惠'));
    assert.equal(promotionReply.includes('与购物无关'),false);
    assert.equal(await customerPage.getByText('已转人工客服：',{exact:false}).count(),0);
    const promotions = (await api(merchant,'GET','/questions/routes?q='+encodeURIComponent('有优惠券吗')))[0];
    assert.equal(promotions.route,'promotions');
    assert.equal(promotions.outcome,'replied');
    await customerPage.screenshot({path:path.join(temp,'promotion-inquiry.png'),fullPage:true});
    await adminPage.goto(webUrl+'/knowledge');
    await adminPage.getByRole('button',{name:'手动添加 FAQ'}).click();
    await adminPage.getByLabel('问题标题').fill('能给我优惠券吗');
    await adminPage.getByLabel('答案与处理规范').fill('收藏店家即可获得三元优惠券哦亲亲');
    await adminPage.getByRole('button',{name:'保存并启用'}).click();
    const couponFaq = adminPage.locator('.document-card').filter({hasText:'能给我优惠券吗'});
    await couponFaq.waitFor();
    async function askCoupon(message) {
      await history.getByRole('button',{name:'新对话',exact:true}).click();
      await customerPage.getByRole('textbox',{name:'聊天消息'}).fill(message);
      await customerPage.getByRole('button',{name:'发送消息',exact:true}).click();
      const answer = customerPage.locator('.customer-message-row.support').first();
      await answer.waitFor();
      return answer.innerText();
    }
    const configuredAnswer = await askCoupon('有优惠券吗');
    assert.ok(configuredAnswer.includes('收藏店家即可获得三元优惠券哦亲亲'));
    assert.equal(configuredAnswer.includes('不能确认是否有可用优惠'),false);
    assert.equal(await customerPage.getByText('已转人工客服：',{exact:false}).count(),0);
    const couponChat = (await api(customer,'GET','/customer/tickets'))[0];
    const couponDetail = await api(merchant,'GET','/tickets/'+couponChat.id);
    assert.equal(couponDetail.assessment.model_trace.find(t=>t.stage==='resolution').provider,'promotion_faq_guard');
    assert.equal(couponDetail.citations[0].title,'能给我优惠券吗');
    await customerPage.locator('.customer-message-row.support').first().scrollIntoViewIfNeeded();
    await customerPage.screenshot({path:path.join(temp,'promotion-faq-answer.png'),fullPage:true});
    await couponFaq.click();
    await adminPage.getByRole('button',{name:'停用文档'}).click();
    await adminPage.getByRole('button',{name:'启用文档'}).waitFor();
    const disabledAnswer = await askCoupon('有优惠券吗');
    assert.ok(disabledAnswer.includes('不能确认是否有可用优惠'));
    assert.equal(disabledAnswer.includes('收藏店家'),false);
    await adminPage.getByRole('button',{name:'启用文档'}).click();
    await adminPage.getByRole('button',{name:'停用文档'}).waitFor();
    assert.ok((await askCoupon('有券吗')).includes('收藏店家即可获得三元优惠券哦亲亲'));
    for (let index=1;index<10;index++) {
      await customerPage.getByRole('textbox',{name:'聊天消息'}).fill('有优惠券吗');
      await customerPage.getByRole('button',{name:'发送消息',exact:true}).click();
      const answer = customerPage.locator('.customer-message-row.support').nth(index);
      await answer.waitFor();
      assert.ok((await answer.innerText()).includes('收藏店家即可获得三元优惠券哦亲亲'));
      assert.equal(await customerPage.getByText('已转人工客服：',{exact:false}).count(),0);
    }
    assert.equal(await customerPage.locator('.customer-message-row.user').count(),10);
    await customerPage.locator('.customer-message-row.support').last().scrollIntoViewIfNeeded();
    await customerPage.screenshot({path:path.join(temp,'continuous-conversation.png'),fullPage:true});
    await customerPage.getByRole('textbox',{name:'聊天消息'}).fill('请转人工客服');
    await customerPage.getByRole('button',{name:'发送消息',exact:true}).click();
    await customerPage.getByText('已转人工客服：',{exact:false}).waitFor();
    await history.getByRole('button',{name:'新对话',exact:true}).click();
    await customerPage.getByRole('textbox',{name:'聊天消息'}).fill('帮我规划学习Python的步骤');
    await customerPage.getByRole('button',{name:'发送消息',exact:true}).click();
    await customerPage.getByText('我主要提供本商城的电商客服服务，暂不提供与购物无关的通用问答。您可以咨询商品选购、价格库存、订单支付、物流配送、退换货或账号问题；请告诉我您具体想了解哪方面。',{exact:true}).waitFor();
    await customerPage.goto(webUrl+'/customer/shop');
    await customerPage.getByRole('link',{name:'让ResolveAI帮您挑选',exact:true}).waitFor();
    await customerPage.waitForLoadState('networkidle');
    assert.equal(/演示|模拟|体验.*流程/.test(await customerPage.locator('.storefront-page').innerText()),false,'商城系统文案与默认商品标签已清理');
    const product = customerPage.locator('.product-card').filter({hasText:'浏览器验收耳机'});
    await product.getByRole('button',{name:'查看并下单'}).click();
    assert.equal(/演示|模拟|体验.*流程/.test(await customerPage.getByRole('dialog').innerText()),false,'下单弹窗不出现旧标签');
    await customerPage.getByLabel('收货地址').fill('杭州市西湖区文一路 88 号');
    await customerPage.getByRole('button',{name:'确认下单'}).click();
    await customerPage.getByText('下单成功：', {exact:false}).waitFor();
    await customerPage.getByRole('link',{name:'前往支付'}).click();
    await customerPage.getByRole('button',{name:'支付',exact:true}).click();
    await customerPage.locator('.order-card header b').filter({hasText:'已支付'}).waitFor();
    const orders = await api(customer,'GET','/customer/orders');
    const order = orders[0];
    await adminPage.goto(webUrl+'/orders');
    await adminPage.getByRole('button',{name:'发货',exact:true}).click();
    await adminPage.locator('.order-card header b').filter({hasText:'未送达'}).waitFor();
    await customerPage.goto(webUrl+'/customer?order='+encodeURIComponent(order.order_no));
    await customerPage.getByRole('textbox',{name:'聊天消息'}).fill('请帮我查询物流状态');
    await customerPage.getByRole('button',{name:'发送消息',exact:true}).click();
    await customerPage.locator('.customer-message-row.support').waitFor();
    assert.ok((await customerPage.locator('.customer-message-row.support').innerText()).includes('未送达'));
    await customerPage.getByRole('textbox',{name:'聊天消息'}).fill('请转人工客服');
    await customerPage.getByRole('button',{name:'发送消息',exact:true}).click();
    await customerPage.getByText('已转人工客服：',{exact:false}).waitFor();
    const humanChat = (await api(customer,'GET','/customer/tickets'))[0];
    assert.equal((await api(merchant,'GET','/tickets?case_type=after_sales')).some(t=>t.id===humanChat.id),false,'Ordinary logistics + explicit human request must remain a chat');
    await adminPage.goto(webUrl+'/chats?status=pending_approval');
    await adminPage.locator('.list-row').filter({hasText:humanChat.ticket_no}).waitFor();
    await adminPage.screenshot({path:path.join(temp,'chat-management.png'),fullPage:true});
    await adminPage.goto(webUrl+'/questions');
    await adminPage.locator('.route-card').filter({hasText:'请转人工客服'}).filter({
      has:adminPage.getByRole('link',{name:humanChat.ticket_no,exact:true}),
    }).waitFor();
    await adminPage.screenshot({path:path.join(temp,'question-routes.png'),fullPage:true});
    await api(merchant,'POST',`/orders/${order.id}/action`,{action:'deliver'});
    await customerPage.goto(webUrl+'/customer/orders');
    await customerPage.getByRole('button',{name:'确认签收'}).click();
    await customerPage.locator('.order-card header b').filter({hasText:'已签收'}).waitFor();
    assert.equal(/演示|模拟|体验.*流程/.test(await customerPage.locator('.orders-page').innerText()),false,'订单页面不出现旧标签');
    await customerPage.screenshot({path:path.join(temp,'customer-orders.png'),fullPage:true});
    await customerPage.goto(webUrl+'/customer?order='+encodeURIComponent(order.order_no));
    await customerPage.getByRole('textbox',{name:'聊天消息'}).fill('收到的耳机坏了，请帮我申请退款');
    await customerPage.getByRole('button',{name:'发送消息',exact:true}).click();
    await customerPage.locator('.customer-message-row.support').waitFor();
    const complaint = (await api(customer,'GET','/customer/tickets'))[0];
    await adminPage.goto(webUrl+'/tickets');
    await adminPage.locator('.list-row').filter({hasText:complaint.ticket_no}).waitFor();
    assert.ok((await adminPage.locator('.list-row').filter({hasText:complaint.ticket_no}).innerText()).includes('售后'));
    assert.equal(await adminPage.locator('.list-row').filter({hasText:ordinary.ticket_no}).count(),0);
    assert.equal(await adminPage.locator('.list-row').filter({hasText:humanChat.ticket_no}).count(),0);
    await adminPage.screenshot({path:path.join(temp,'after-sales-only.png'),fullPage:true});
    await adminPage.goto(webUrl+'/knowledge');
    await adminPage.getByRole('button',{name:'手动添加 FAQ'}).click();
    await adminPage.getByLabel('问题标题').fill('演示FAQ：如何开箱');
    await adminPage.getByLabel('答案与处理规范').fill('请检查外包装是否完整，按照商品说明进行开箱。');
    await adminPage.getByRole('button',{name:'保存并启用'}).click();
    await adminPage.locator('.document-card').filter({hasText:'演示FAQ：如何开箱'}).click();
    await adminPage.getByRole('button',{name:'停用文档'}).click();
    await adminPage.getByRole('button',{name:'启用文档'}).waitFor();
    // Visual refresh: exercise real filters, sorting and dialogs; all data is isolated.
    await adminPage.goto(webUrl+'/orders');
    await adminPage.locator('.order-card').waitFor();
    await adminPage.getByLabel('搜索订单',{exact:true}).fill('不存在的订单-ui-test');
    await adminPage.getByRole('heading',{name:'没有找到匹配的订单',exact:true}).waitFor();
    await adminPage.getByRole('button',{name:'重置筛选',exact:true}).click();
    await adminPage.getByLabel('订单状态',{exact:true}).selectOption('signed');
    assert.equal((await adminPage.locator('.commerce-tabs button.active').innerText()).replace(/\s/g,''),'已签收1');
    await adminPage.getByRole('button',{name:/全部订单/}).click();
    await adminPage.locator('.order-card summary').click();
    await adminPage.getByRole('heading',{name:'处理时间线',exact:true}).waitFor();
    assert.equal((await adminPage.locator('.order-events').innerText()).includes('模拟'),false,'系统订单时间线不显示旧用语');
    assert.equal((await adminPage.locator('.ledger-entry').innerText()).includes('模拟'),false,'流水显示标准支付/退款用语');
    const storedOrder=(await api(merchant,'GET','/orders')).find(o=>o.id===order.id);
    assert.ok(storedOrder.events.some(e=>e.description.includes('模拟')),'显示层修改不能改写数据库事件');
    assert.equal((await adminPage.locator('.order-progress li[aria-current="step"]').innerText()).replace(/\s/g,''),'5已签收');
    await adminPage.screenshot({path:path.join(temp,'ui-order-expanded.png'),fullPage:true});
    await adminPage.goto(webUrl+'/products');
    await adminPage.getByLabel('搜索商品管理',{exact:true}).fill('浏览器验收耳机');
    const filteredProduct = adminPage.locator('.product-card');
    await filteredProduct.waitFor();
    assert.equal(await filteredProduct.count(),1);
    await filteredProduct.getByRole('button',{name:'编辑商品',exact:true}).click();
    await adminPage.getByRole('dialog',{name:'编辑商品',exact:true}).waitFor();
    await adminPage.keyboard.press('Tab');
    assert.ok(await adminPage.getByRole('dialog').evaluate(el=>el.contains(document.activeElement)),'Dialog traps keyboard focus');
    await adminPage.keyboard.press('Escape');
    await adminPage.getByRole('dialog').waitFor({state:'hidden'});
    await customerPage.goto(webUrl+'/customer/shop');
    await customerPage.getByLabel('商品排序',{exact:true}).selectOption('price-up');
    await customerPage.waitForLoadState('networkidle');
    const prices=await customerPage.locator('.product-price > strong').allTextContents();
    const values=prices.map(p=>Number(p.replace(/[^\d.]/g,'')));
    assert.deepEqual(values,[...values].sort((a,b)=>a-b),'Price sorting uses numeric product prices');
    const visualRoutes=[['/','智能客服运营台'],['/products','商品管理'],['/orders','订单管理'],['/chats','聊天管理'],['/tickets','售后处理'],['/questions','提问与路由记录'],['/agents','Agent 运行'],['/knowledge','知识库'],['/analytics','效果分析'],['/settings','项目设置'],['/accounts','我的账号']];
    await adminPage.emulateMedia({reducedMotion:'reduce'});
    await customerPage.emulateMedia({reducedMotion:'reduce'});
    const uiWidths=[1440,1024,768,390];
    for(const [route,title] of visualRoutes) {
      await adminPage.goto(webUrl+route);
      await adminPage.locator('.page-header h1').waitFor();
      assert.ok((await adminPage.locator('.page-header h1').innerText()).replaceAll(' ','').includes(title.replaceAll(' ','')));
      await adminPage.waitForLoadState('networkidle');
      for(const width of uiWidths) {
        await adminPage.setViewportSize({width,height:950});
        const overflow=await adminPage.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
        await adminPage.screenshot({path:path.join(temp,`ui-merchant-${route.replaceAll('/','')||'overview'}-${width}.png`)});
        assert.ok(overflow.scroll<=overflow.width+1,`Merchant ${route} @ ${width}px must not overflow: ${JSON.stringify(overflow)}`);
      }
    }
    for(const route of ['/customer','/customer/shop','/customer/orders']) {
      await customerPage.goto(webUrl+route);
      await customerPage.locator('.customer-header').waitFor();
      await customerPage.waitForLoadState('networkidle');
      for(const width of uiWidths) {
        await customerPage.setViewportSize({width,height:950});
        assert.ok(await customerPage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`Customer ${route} @ ${width}px must not overflow`);
        await customerPage.screenshot({path:path.join(temp,`ui-${route.replaceAll('/','-')}-${width}.png`)});
      }
    }
    for(const [route,label] of [[`/chats/${ordinary.id}`,'chat-detail'],[`/tickets/${complaint.id}`,'after-sales-detail']]) {
      await adminPage.goto(webUrl+route);
      await adminPage.locator('.workbench-title h1').waitFor();
      await adminPage.waitForLoadState('networkidle');
      for(const width of uiWidths) {
        await adminPage.setViewportSize({width,height:950});
        await adminPage.screenshot({path:path.join(temp,`ui-${label}-${width}.png`)});
        assert.ok(await adminPage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`Workbench ${route} @ ${width}px must not overflow`);
      }
    }
    const loginPage=await browser.newPage();
    loginPage.on('pageerror',e=>errors.push(e.message));
    await loginPage.goto(webUrl+'/login');
    await loginPage.getByRole('button',{name:'登录',exact:true}).waitFor();
    for(const width of uiWidths) {
      await loginPage.setViewportSize({width,height:950});
      assert.ok(await loginPage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`Login @ ${width}px must not overflow`);
      await loginPage.screenshot({path:path.join(temp,`ui-login-${width}.png`)});
    }
    await loginPage.close();
    await adminPage.setViewportSize({width:1440,height:950});
    await adminPage.goto(webUrl+'/chats');
    await adminPage.getByLabel('搜索对话',{exact:true}).fill(complaint.ticket_no);
    await adminPage.locator('.list-row').waitFor();
    assert.equal(await adminPage.locator('.list-row').count(),1);
    await adminPage.getByLabel('对话优先级',{exact:true}).selectOption('urgent');
    await adminPage.getByText('没有符合条件的对话',{exact:true}).waitFor();
    await adminPage.getByLabel('对话优先级',{exact:true}).selectOption('all');
    await adminPage.getByLabel('对话排序',{exact:true}).selectOption('oldest');
    await adminPage.locator('.list-row').waitFor();
    const costReport=(await api(merchant,'GET','/performance/metrics')).models;
    assert.ok(costReport.complete_cost_calls>0,'成本验收需要完整台账样本');
    assert.equal(costReport.calls,costReport.ledger_calls,'历史摘要不能混入调用数');
    assert.equal(costReport.recorded_average_cost_per_call_usd,Number((costReport.known_estimated_cost_usd/costReport.complete_cost_calls).toFixed(8)));
    await adminPage.goto(webUrl+'/analytics');
    const costCard=adminPage.locator('.feature-metric').filter({hasText:'平均估算成本（非账单）'});
    await costCard.waitFor();
    assert.ok((await costCard.innerText()).includes(`基于 ${costReport.complete_cost_calls} 次完整记录`));
    assert.ok((await costCard.innerText()).includes(`$${costReport.recorded_average_cost_per_call_usd.toFixed(6)}`));
    assert.equal((await costCard.innerText()).includes('未知'),false);
    assert.equal((await costCard.innerText()).includes('旧版摘要'),false);
    await costCard.scrollIntoViewIfNeeded();
    await adminPage.screenshot({path:path.join(temp,'recorded-cost-average.png'),fullPage:true});
    await customerPage.goto(webUrl+'/customer');
    await customerPage.locator('.assistant-history').getByRole('button',{name:'新对话',exact:true}).click();
    const originalCustomerMessage='我想了解耳机库存，备注：模拟测试原文请保留';
    await customerPage.getByRole('textbox',{name:'聊天消息'}).fill(originalCustomerMessage);
    await customerPage.getByRole('button',{name:'发送消息',exact:true}).click();
    await customerPage.locator('.customer-message-row.support').waitFor();
    assert.equal(await customerPage.locator('.customer-message-row.user .customer-bubble p').innerText(),originalCustomerMessage,'客户原文不能被显示格式化器改写');
    assert.equal((await customerPage.locator('.customer-message-row.support .customer-bubble p').innerText()).includes('模拟'),false,'客服系统用语统一');
    assert.equal((await customerPage.locator('.customer-message-row.support .customer-bubble p').innerText()).includes('演示'),false,'客服回复不回退到演示字样');
    await customerPage.screenshot({path:path.join(temp,'standard-service-copy.png'),fullPage:true});
    const customProduct=await api(merchant,'POST','/products',{name:'星纹盒',description:'一件星纹盒',price:1299,stock:1,category:'其他'});
    await customerPage.setViewportSize({width:1440,height:950});
    await history.getByRole('button',{name:'新对话',exact:true}).click();
    await customerPage.getByRole('textbox',{name:'聊天消息'}).fill('商城里都有什么');
    await customerPage.getByRole('button',{name:'发送消息',exact:true}).click();
    await customerPage.locator('.customer-message-row.support').first().waitFor();
    for(const [index,message] of ['星纹盒是啥啊','这个的成分是什么'].entries()) {
      await customerPage.getByRole('textbox',{name:'聊天消息'}).fill(message);
      await customerPage.getByRole('button',{name:'发送消息',exact:true}).click();
      const reply=customerPage.locator('.customer-message-row.support').nth(index+1);
      await reply.waitFor();
      const content=await reply.innerText();
      assert.ok(content.includes('一件星纹盒') && content.includes('1299.00'));
      assert.ok(content.includes(index===0?'暂时没有更详细的介绍':'这款商品暂未提供成分说明'));
      for(const phrase of ['不会自行猜测','不能仅凭名称推断','资料中未写明','以上依据商户当前商品资料']) {
        assert.equal(content.includes(phrase),false,'客户回复不暴露内部防编造规则');
      }
      assert.equal(content.includes('牛奶'),false);
      assert.equal(content.includes('鸡蛋'),false);
      assert.equal(await customerPage.getByText('已转人工客服：',{exact:false}).count(),0);
    }
    const detailsChat=(await api(customer,'GET','/customer/tickets'))[0];
    const detailsContext=(await api(merchant,'GET','/tickets/'+detailsChat.id)).assessment.customer_context;
    assert.equal(detailsContext.catalog_intent,'details');
    assert.equal(detailsContext.catalog[0].id,customProduct.id);
    const detailsRoutes=(await api(merchant,'GET','/questions/routes')).filter(r=>r.ticket_id===detailsChat.id);
    assert.equal(detailsRoutes.length,3);
    assert.ok(detailsRoutes.every(r=>r.route==='catalog' && r.outcome==='replied'));
    await customerPage.locator('.customer-message-row.support').last().scrollIntoViewIfNeeded();
    await customerPage.screenshot({path:path.join(temp,'custom-product-details.png'),fullPage:true});
    // Merchant chat: short and long manual replies share the same right edge;
    // customer ratings/comments remain visible after follow-ups and refreshes.
    await history.getByRole('button',{name:'新对话',exact:true}).click();
    await customerPage.getByRole('textbox',{name:'聊天消息'}).fill('请转人工客服，我想了解商品');
    await customerPage.getByRole('button',{name:'发送消息',exact:true}).click();
    await customerPage.getByText('已转人工客服：',{exact:false}).waitFor();
    const manualChat=(await api(customer,'GET','/customer/tickets'))[0];
    await adminPage.setViewportSize({width:1440,height:950});
    await adminPage.goto(webUrl+'/chats/'+manualChat.id);
    const feedbackPanel=adminPage.getByRole('region',{name:'客户反馈',exact:true});
    await feedbackPanel.getByText('暂无客户反馈，客户提交评价后会在这里显示。',{exact:true}).waitFor();
    const manualReplies=['收到。','请问您想了解哪款商品？','您可以告诉我想了解的商品名称，以及更关注的功能或使用场景。'+'选购需求说明'.repeat(35)+'\n参考编号：'+'ABCD'.repeat(25)];
    async function manualReply(content) {
      await adminPage.getByRole('textbox',{name:'回复内容',exact:true}).fill(content);
      await adminPage.getByRole('button',{name:'发送回复',exact:true}).click();
      await adminPage.locator('.sent-message').getByText(content,{exact:true}).waitFor();
      await customerPage.locator('.customer-message-row.support').getByText(content,{exact:true}).waitFor();
    }
    for(const reply of manualReplies) await manualReply(reply);
    for(const width of [1440,1024,768,390]) {
      await adminPage.setViewportSize({width,height:950});
      await adminPage.evaluate(()=>document.fonts.ready);
      const geometry=await adminPage.locator('.message.sent-message').evaluateAll(rows=>rows.map(row=>{
        const avatar=row.querySelector('.message-avatar').getBoundingClientRect();
        const bubble=row.querySelector('.bubble').getBoundingClientRect();
        return {avatarRight:avatar.right,avatarLeft:avatar.left,bubbleRight:bubble.right,bubbleWidth:bubble.width,
          contentWidth:row.querySelector('.bubble').scrollWidth,clientWidth:row.querySelector('.bubble').clientWidth};
      }));
      assert.ok(geometry.length>=4);
      for(const item of geometry) {
        assert.ok(Math.abs(item.avatarRight-geometry[0].avatarRight)<1,'长短客服回复头像右边缘一致');
        assert.ok(Math.abs(item.bubbleRight-geometry[0].bubbleRight)<1,'长短客服回复气泡右边缘一致');
        assert.ok(item.bubbleRight<item.avatarLeft,'客服气泡位于头像左侧');
        assert.ok(item.contentWidth<=item.clientWidth+1,'长文本和连续字符必须换行');
      }
      assert.ok(await adminPage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      await adminPage.locator('.message.sent-message').last().scrollIntoViewIfNeeded();
      await adminPage.screenshot({path:path.join(temp,`manual-reply-alignment-${width}.png`),fullPage:true});
    }
    await adminPage.setViewportSize({width:1440,height:950});
    const unresolvedFeedback='回复还不够具体。\n请结合我的问题补充说明。';
    await customerPage.getByRole('button',{name:'评价本次对话',exact:true}).click();
    await customerPage.getByRole('button',{name:'2 星',exact:true}).click();
    await customerPage.getByPlaceholder('补充您的意见（选填）',{exact:true}).fill(unresolvedFeedback);
    await customerPage.getByRole('button',{name:'仍需帮助',exact:true}).click();
    await customerPage.locator('.assistant-feedback').waitFor({state:'hidden'});
    const unresolvedRow=feedbackPanel.locator('.workbench-feedback-item').filter({hasText:unresolvedFeedback});
    await unresolvedRow.waitFor();
    assert.equal(await unresolvedRow.locator('.feedback-comment').innerText(),unresolvedFeedback);
    assert.ok((await unresolvedRow.innerText()).includes('2 / 5 分'));
    assert.ok((await unresolvedRow.innerText()).includes('仍需帮助'));
    assert.ok(await unresolvedRow.locator('time').getAttribute('datetime'));
    await customerPage.getByRole('textbox',{name:'聊天消息'}).fill('还是想知道是否适合通勤');
    await customerPage.getByRole('button',{name:'发送消息',exact:true}).click();
    await adminPage.locator('.customer-message').getByText('还是想知道是否适合通勤',{exact:true}).waitFor();
    assert.equal(await feedbackPanel.locator('.workbench-feedback-item').count(),1);
    await manualReply('您可以补充具体的通勤场景和商品名称。');
    await customerPage.getByRole('button',{name:'评价本次对话',exact:true}).click();
    await customerPage.getByRole('button',{name:'5 星',exact:true}).click();
    await customerPage.getByPlaceholder('补充您的意见（选填）',{exact:true}).fill('回复清楚，谢谢。');
    await customerPage.getByRole('button',{name:'问题已解决',exact:true}).click();
    await customerPage.locator('.assistant-feedback').waitFor({state:'hidden'});
    await feedbackPanel.getByText('回复清楚，谢谢。',{exact:true}).waitFor();
    await adminPage.reload();
    await feedbackPanel.getByText('回复清楚，谢谢。',{exact:true}).waitFor();
    assert.equal(await feedbackPanel.locator('.workbench-feedback-item').count(),2);
    const latestFeedback=feedbackPanel.locator('.workbench-feedback-item').first();
    assert.ok((await latestFeedback.innerText()).includes('5 / 5 分'));
    assert.ok((await latestFeedback.innerText()).includes('问题已解决'));
    assert.equal(await feedbackPanel.locator('.feedback-comment').last().innerText(),unresolvedFeedback);
    for(const width of [1440,390]) {
      await adminPage.setViewportSize({width,height:950});
      await feedbackPanel.scrollIntoViewIfNeeded();
      assert.ok(await adminPage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
      await adminPage.screenshot({path:path.join(temp,`merchant-customer-feedback-${width}.png`),fullPage:true});
    }
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({status:'passed',checks:['后台仅管理员且客户端仅客户','三层左侧导航顺序和层级','小窗口390/700px可展开滚动导航且不溢出','管理员只能修改自己资料且不能操作客户','客户一位数字密码可注册并登录','用户注册后登录','登录直接聊天且无订单表单','电商咨询与连续聊天','聊天管理查看普通咨询并保持详情返回路径','普通咨询/查物流转人工不进入售后','真实破损/退款进入售后列表','优惠券咨询属于电商范围且未知优惠不编造','新增优惠FAQ后立即按配置回复并保留引用','停用优惠FAQ后回到未知且重新启用恢复','连续十轮仍自动回复且之后可主动转人工','上新追问继承键盘主题且未知时间不转人工','电商服务范围边界','刷新恢复与历史切换','移动端布局','商品及图片上架','客户端浏览/下单/支付','商户全部订单及发货','物流自动回复','转人工','提问记录','送达签收','手动FAQ及停用'],uiAcceptance:{mode:productionUI?'production':'development',pages:17,viewports:uiWidths,layoutChecks:68,interactions:['订单搜索/筛选同步/进度与流水展开','商品搜索/弹窗键盘聚焦/ESC关闭','商城价格数值排序','聊天搜索/优先级/更新时间排序']},screenshots:temp,pageErrors:errors},null,2));
  } finally {
    if(browser) await browser.close();
    for(const child of children) {
      if (child.exitCode !== null || !child.pid) continue;
      if (process.platform === 'win32') {
        await new Promise(resolve => {
          const stop=spawn('taskkill',['/PID',String(child.pid),'/T','/F'],{windowsHide:true,stdio:'ignore'});
          const timer=setTimeout(() => {stop.kill(); resolve();},5000);
          const finish=() => {clearTimeout(timer); resolve();}; stop.on('exit',finish); stop.on('error',finish);
        });
      } else child.kill();
      child.stdout.destroy();
      child.stderr.destroy();
    }
  }
})().catch(e => {console.error(e.message); process.exitCode=1;});
