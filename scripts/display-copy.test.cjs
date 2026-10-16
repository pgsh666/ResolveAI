const test = require('node:test');
const assert = require('node:assert/strict');
const {serviceDisplayText, productDisplayDescription, NON_REAL_FUNDS_NOTICE} = require('../frontend/lib/presentation.ts');

test('旧操作文案显示为标准交易用语', () => {
  const cases = [
    ['模拟支付', '支付'], ['模拟退款', '退款'], ['已模拟退款 88.50 元', '已退款 88.50 元'],
    ['商户已模拟发货，包裹运输中', '商户已发货，包裹运输中'],
    ['包裹已模拟送达，等待客户签收', '包裹已送达，等待客户签收'],
    ['等待模拟支付', '等待支付'], ['模拟运输已进行 3 天', '运输已进行 3 天'],
    ['在商城查看后模拟下单', '在商城查看后下单'], ['订单进度与模拟流水', '订单进度与交易流水'],
  ];
  for (const [source, expected] of cases) assert.equal(serviceDisplayText(source), expected);
});

test('保留金额、时间和未发生真实扣款的事实', () => {
  const source = '模拟支付成功，未发生真实扣款；¥ 88.50 · ORD-26-10-15-1';
  assert.equal(serviceDisplayText(source), '支付成功，未发生真实扣款；¥ 88.50 · ORD-26-10-15-1');
  assert.equal(source.includes('模拟'), true, '输入原文不变');
});

test('客服资金回复保留不处理真实资金的明确说明且不用旧标签', () => {
  for (const source of ['本项目资金工具为模拟执行，不代表真实到账。', '已完成模拟退款 88.50 元。', '模拟支付成功']) {
    const display = serviceDisplayText(source, true);
    assert.equal(display.includes('模拟'), false);
    assert.equal(display.includes('演示'), false);
    assert.ok(display.includes(NON_REAL_FUNDS_NOTICE));
    assert.match(display, /不代表真实到账|不产生真实扣款或资金到账/);
  }
});

test('旧商品默认标签只在显示层去除，不改动真实商品能力描述', () => {
  const source='支持模拟信号输出，续航约 6 小时。演示商品。';
  assert.equal(productDisplayDescription(source),'支持模拟信号输出，续航约 6 小时。');
  assert.equal(source.endsWith('演示商品。'),true);
  assert.equal(productDisplayDescription('轻便蓝牙耳机。仅供演示使用。'),'轻便蓝牙耳机。');
});

test('服务提示不再输出演示用或体验流程标签', () => {
  for(const source of ['演示用订单', '项目模拟执行', '当前为演示环境，不产生真实扣款。', '体验完整购物流程', '演示商品。']) {
    assert.equal(/模拟|演示|体验.*流程/.test(serviceDisplayText(source,true)),false);
  }
});

test('不改变协议模式、订单编号、工具名与普通回复', () => {
  for (const source of ['simulated', 'execute_refund', '26-10-15-1', '您好，耳机库存 25 件。']) {
    assert.equal(serviceDisplayText(source, true), source);
  }
});

test('非资金服务消息不追加资金提示', () => {
  assert.equal(serviceDisplayText('模拟运输已进行 2 天', true), '运输已进行 2 天');
});

test('空值安全且反复格式化保持幂等', () => {
  assert.equal(serviceDisplayText(null), '');
  assert.equal(serviceDisplayText(undefined), '');
  const once = serviceDisplayText('已完成模拟退款 88.50 元', true);
  assert.equal(serviceDisplayText(once, true), once);
});
