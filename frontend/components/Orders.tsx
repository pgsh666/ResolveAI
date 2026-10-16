"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Check, CheckCircle2, Clock3, MapPin, MessageCircle, Package, Search, ShieldCheck, ShoppingBag, Truck, UserRound, Wallet, X } from "lucide-react";
import { api } from "@/lib/api";
import type { StoreOrder } from "@/lib/types";
import { CustomerHeader } from "@/components/CustomerHeader";
import { PageHeader } from "@/components/PageHeader";
import { useAuth } from "@/components/AuthProvider";
import { CommerceEmpty, CommerceStats, ProductVisual, money } from "@/components/CommerceUI";
import { formatBusinessTime } from "@/lib/time";
import { serviceDisplayText } from "@/lib/presentation";

const statuses: Record<string, string> = { all: "全部订单", pending_payment: "待支付", paid: "已支付", in_transit: "未送达", delivered: "已送达", signed: "已签收", cancelled: "已取消" };
const stages = ["pending_payment", "paid", "in_transit", "delivered", "signed"];
const stageLabels = ["已下单", "已支付", "运输中", "已送达", "已签收"];
const hints: Record<string, string> = { pending_payment: "等待客户完成支付", paid: "已支付，等待商户发货", in_transit: "商品运输中，可查看运输进度", delivered: "商品已送达，等待客户确认签收", signed: "客户已签收，购物流程已完成", cancelled: "订单已取消，不再推进后续流程" };

export function Orders({ customer = false }: { customer?: boolean }) {
  const { user } = useAuth();
  const [orders, setOrders] = useState<StoreOrder[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    const reload = () => api.orders(customer).then(data => { if (active) { setOrders(data); setError(""); } }).catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setLoading(false); });
    void reload();
    const timer = setInterval(reload, 5000);
    return () => { active = false; clearInterval(timer); };
  }, [customer]);
  async function act(order: StoreOrder, action: string) {
    setBusy(order.id); setError("");
    try { await api.orderAction(order.id, action, customer, action === "advance" ? { transit_days: order.transit_days + 1 } : {}); setOrders(await api.orders(customer)); }
    catch (e) { setError((e as Error).message); } finally { setBusy(""); }
  }
  const keyword = query.trim().toLowerCase();
  const visible = orders.filter(o => (filter === "all" || o.status === filter) && `${o.order_no} ${o.product_name} ${o.customer_name}`.toLowerCase().includes(keyword));
  const canManage = !customer && user?.role === "admin";
  const count = (status: string) => orders.filter(o => o.status === status).length;
  const stats = [
    { label: "全部订单", value: orders.length, note: customer ? "您的购物记录" : "商城累计订单", icon: ShoppingBag },
    { label: customer ? "待支付" : "待发货", value: count(customer ? "pending_payment" : "paid"), note: customer ? "完成支付后安排发货" : "已支付，等待安排发货", icon: customer ? Wallet : Package, tone: "orange" },
    { label: "运输中", value: count("in_transit"), note: `其中 ${orders.filter(o => o.overdue).length} 单物流超期`, icon: Truck, tone: "blue" },
    { label: "已签收", value: count("signed"), note: "客户已确认收货", icon: CheckCircle2, tone: "teal" },
  ];
  const content = <div className={`${customer ? "commerce-page" : "page-wrap feature-page"} orders-page`}>
    {customer ? <div className="commerce-heading"><div><h1>我的订单</h1><p>每一笔购物，都有清晰的进度。</p></div><Link href="/customer/shop" className="commerce-button"><ShoppingBag size={17} />继续购物</Link></div> : <PageHeader title="订单管理" description="从下单到签收，集中跟进每一笔商城订单。" />}
    <CommerceStats items={stats} loading={loading} />
    <section className="panel commerce-control-panel" aria-label="订单筛选">
      <div className="commerce-control-top"><div><h2>订单列表 <span>{loading ? "—" : visible.length}</span></h2><p>{customer ? "查看支付、物流和收货状态" : "所有客户的商城订单，不仅限于售后订单"}</p></div><span className="live-caption"><i />每 5 秒同步</span></div>
      <div className="commerce-filters"><label className="commerce-search-field"><Search size={18} /><input aria-label="搜索订单" value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索订单编号、商品或客户" />{query && <button aria-label="清空订单搜索" onClick={() => setQuery("")}><X size={15} /></button>}</label><select aria-label="订单状态" value={filter} onChange={e => setFilter(e.target.value)}>{Object.entries(statuses).map(([v, t]) => <option value={v} key={v}>{t}</option>)}</select></div>
      <div className="commerce-tabs" aria-label="快捷筛选订单">{Object.entries(statuses).map(([v, t]) => <button key={v} aria-pressed={filter === v} className={filter === v ? "active" : ""} onClick={() => setFilter(v)}>{t}<span>{loading ? "—" : v === "all" ? orders.length : count(v)}</span></button>)}</div>
    </section>
    {error && <p role="alert" className="customer-error">{error}</p>}
    {loading && <CommerceEmpty title="正在加载订单" description="正在同步订单和物流记录…" loading />}
    <div className="order-list">{!loading && visible.map(o => {
      const current = stages.indexOf(o.status);
      return <article className={`panel order-card status-${o.status}`} key={o.id} aria-busy={busy === o.id}>
        <header><div className="order-identity"><span className="order-id-icon"><ShoppingBag size={17} /></span><strong>{o.order_no}</strong><span className="order-date">{formatBusinessTime(o.created_at)}</span></div><b className={`commerce-status ${o.overdue ? "overdue" : o.status}`}><i />{o.status_label}{o.overdue && " · 物流超期"}</b></header>
        <div className="order-summary"><div className="order-product"><ProductVisual name={o.product_name} compact /><div><h2>{o.product_name}</h2><span className="order-unit">单价 ¥ {money(o.unit_price)}<em>× {o.quantity} 件</em></span><div className="order-customer"><span><UserRound size={14} />{o.customer_name}</span><span><MapPin size={14} />{o.address}</span></div></div></div><div className="order-amount"><span>订单金额</span><strong><small>¥</small> {money(o.total)}</strong>{o.refunded > 0 && <span className="refund-caption">已退款 ¥ {money(o.refunded)}</span>}</div></div>
        {o.status !== "cancelled" && <ol className="order-progress" aria-label="订单进度">{stages.map((stage, i) => <li key={stage} className={i < current ? "done" : i === current ? "current" : ""} aria-current={i === current ? "step" : undefined}><span>{i < current ? <Check size={12} /> : i + 1}</span><strong>{stageLabels[i]}</strong></li>)}</ol>}
        <div className={`order-fulfillment ${o.overdue ? "overdue" : ""}`}><div><Truck size={17} /><span>{o.status === "cancelled" ? "订单已取消" : ["pending_payment", "paid"].includes(o.status) ? `预计运输 ${o.expected_delivery_days} 天 · 尚未发货` : `预计运输 ${o.expected_delivery_days} 天 · 已运输 ${o.transit_days} 天`}</span></div><span>{o.overdue ? "运输时间已超过预计天数，请跟进" : hints[o.status]}</span></div>
        <div className="order-bottom"><details><summary>订单进度与交易流水</summary><div className="order-detail-columns"><section><h3><Clock3 size={16} />处理时间线</h3><ol className="order-events">{o.events.map(e => <li key={e.id}><span>{formatBusinessTime(e.created_at)}</span><p>{serviceDisplayText(e.description)}</p></li>)}</ol></section><section><h3><Wallet size={16} />交易流水</h3>{!o.ledger.length && <p className="ledger-empty">尚未支付，无资金流水。</p>}{o.ledger.map(l => <div className="ledger-entry" key={l.id}><div><strong>{l.kind === "payment" ? "支付" : "退款"}</strong><b>¥ {money(l.amount)}</b></div><span>{formatBusinessTime(l.created_at)}</span><small className="ledger-id">流水号：{l.id}</small></div>)}</section></div></details>
          <div className="commerce-actions">{customer && o.status === "pending_payment" && <><button disabled={Boolean(busy)} onClick={() => void act(o, "cancel")}>取消订单</button><button className="commerce-button" disabled={Boolean(busy)} onClick={() => void act(o, "pay")}>支付<ArrowRight size={15} /></button></>}{customer && o.status === "delivered" && <button className="commerce-button" disabled={Boolean(busy)} onClick={() => void act(o, "sign")}>确认签收<Check size={16} /></button>}{customer && <Link className="order-consult" href={`/customer?order=${encodeURIComponent(o.order_no)}`}><MessageCircle size={15} />咨询此订单</Link>}{canManage && o.status === "paid" && <button className="commerce-button" disabled={Boolean(busy)} onClick={() => void act(o, "ship")}><Truck size={16} />发货</button>}{canManage && o.status === "in_transit" && <><button disabled={Boolean(busy)} onClick={() => void act(o, "advance")}>运输时间 +1 天</button><button className="commerce-button" disabled={Boolean(busy)} onClick={() => void act(o, "deliver")}><CheckCircle2 size={16} />确认送达</button></>}</div>
        </div>
      </article>;
    })}</div>
    {!loading && !visible.length && <CommerceEmpty title={orders.length ? "没有找到匹配的订单" : "还没有订单"} description={orders.length ? "试试其他关键词，或切换订单状态。" : customer ? "前往商城挑选商品，下单后可在这里查看支付和配送进度。" : "客户在商城下单后，订单会自动出现在这里。"} action={orders.length ? <button className="commerce-button" onClick={() => { setFilter("all"); setQuery(""); }}>重置筛选</button> : customer ? <Link className="commerce-button" href="/customer/shop">前往商城下单<ArrowRight size={16} /></Link> : undefined} />}
    <p className="commerce-footnote commerce-disclaimer"><ShieldCheck size={16} />支付和退款不涉及真实资金；商户推进物流状态，客户确认签收。</p>
  </div>;
  return customer ? <div className="customer-portal"><CustomerHeader />{content}</div> : content;
}
