export const NON_REAL_FUNDS_NOTICE = "本项目不处理真实资金，不产生真实扣款或资金到账。";

/** Hide old catalog boilerplate, not product facts such as analog signal support. */
export function productDisplayDescription(value: string): string {
  return value.replace(/(?:演示用商品|演示商品|模拟商品|仅供演示使用|仅供演示)[。！!]?\s*/g, "").trim();
}

/** Presentation only: never use this to rewrite stored records, customer input or knowledge sources. */
export function serviceDisplayText(value?: string | null, discloseFunds = false): string {
  if (!value) return "";
  const wasDemonstration = /模拟|演示/.test(value);
  let text = productDisplayDescription(value)
    .replaceAll("本项目资金工具为模拟执行", "本项目资金工具只记录操作结果")
    .replaceAll("本项目资金工具仅在演示环境执行", "本项目资金工具只记录操作结果")
    .replaceAll("项目模拟执行", "站内执行")
    .replaceAll("项目模拟", "站内")
    .replaceAll("演示环境执行", "站内执行")
    .replaceAll("模拟执行", "站内执行")
    .replaceAll("演示执行", "站内执行")
    .replaceAll("已模拟退款", "已退款")
    .replaceAll("已模拟发货", "已发货")
    .replaceAll("已模拟送达", "已送达")
    .replaceAll("模拟支付", "支付")
    .replaceAll("模拟退款", "退款")
    .replaceAll("模拟下单", "下单")
    .replaceAll("模拟发货", "发货")
    .replaceAll("模拟送达", "送达")
    .replaceAll("模拟运输", "运输")
    .replaceAll("模拟订单", "订单")
    .replaceAll("模拟流水", "交易流水")
    .replaceAll("体验完整购物流程", "查看订单与配送进度")
    .replaceAll("体验流程", "使用说明")
    .replaceAll("演示模式", "离线规则")
    .replaceAll("演示环境", "站内")
    .replaceAll("演示用", "")
    .replaceAll("模拟", "")
    .replaceAll("演示", "");
  // Assistant financial replies must remain truthful even if their only old disclaimer was a prefix.
  if (discloseFunds && wasDemonstration && /支付|退款|扣款|到账|资金|发券/.test(value) && !text.includes(NON_REAL_FUNDS_NOTICE)) {
    text += `\n${NON_REAL_FUNDS_NOTICE}`;
  }
  return text;
}
