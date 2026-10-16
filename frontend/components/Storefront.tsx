"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { ArrowRight, CheckCircle2, MessageCircle, Search, ShieldCheck, ShoppingBag, SlidersHorizontal, X } from "lucide-react";
import { api } from "@/lib/api";
import type { Product } from "@/lib/types";
import { CustomerHeader } from "@/components/CustomerHeader";
import { productDisplayDescription } from "@/lib/presentation";
import { CommerceDialog, CommerceEmpty, ProductVisual, money } from "@/components/CommerceUI";

export function Storefront() {
  const [products, setProducts] = useState<Product[]>([]);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [sort, setSort] = useState("default");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Product | null>(null);
  const [quantity, setQuantity] = useState(1);
  const [address, setAddress] = useState("");
  const [requestKey, setRequestKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => { api.products(true).then(setProducts).catch(e => setError(e.message)).finally(() => setLoading(false)); }, []);
  async function buy(event: FormEvent) {
    event.preventDefault(); if (!selected) return;
    setBusy(true); setError("");
    try { const order = await api.purchase(selected.id, quantity, address, requestKey); setNotice(`下单成功：${order.order_no}，请到“我的订单”进行支付。`); setSelected(null); setProducts(await api.products(true)); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const categories = Array.from(new Set(products.map(p => p.category)));
  const keyword = query.trim().toLowerCase();
  const visible = products.filter(p => (!category || p.category === category) && `${p.name} ${p.description} ${p.category}`.toLowerCase().includes(keyword)).sort((a, b) => sort === "price-up" ? a.price - b.price : sort === "price-down" ? b.price - a.price : 0);
  return <div className="customer-portal"><CustomerHeader /><div className="commerce-page storefront-page">
    <section className="shop-hero"><div className="shop-hero-copy"><span className="shop-hero-label"><span />选购好物，从这里开始</span><h1>发现心仪好物<span>购物进度，随时掌握。</span></h1><p>浏览商品、轻松下单，随时跟进订单与配送进度。</p><div><Link href="/customer" className="commerce-button"><MessageCircle size={17} />让ResolveAI帮您挑选<ArrowRight size={17} /></Link><Link className="shop-orders-link" href="/customer/orders">我的订单<ArrowRight size={15} /></Link></div></div><div className="shop-hero-art" aria-hidden="true"><div className="shop-art-orbit" /><div className="shop-art-bag"><ShoppingBag size={62} strokeWidth={1.3} /></div><span className="shop-art-label"><CheckCircle2 size={16} />随时查看订单进度</span><span className="shop-art-dot" /></div></section>
    {error && !selected && <p role="alert" className="customer-error">{error}</p>}{notice && <div className="commerce-notice" role="status"><CheckCircle2 size={20} /><span>{notice}</span><Link href="/customer/orders">前往支付<ArrowRight size={15} /></Link></div>}
    <section className="panel commerce-control-panel">
      <div className="commerce-control-top"><div><h2>商城 <span>{loading ? "—" : products.length}</span></h2><p>查看商户上架商品，按您的需求挑选</p></div><span className="shop-simulation-label"><ShieldCheck size={15} />不产生真实扣款</span></div>
      <div className="commerce-filters"><label className="commerce-search-field"><Search size={18} /><input aria-label="搜索商品" placeholder="搜索商品名称、分类或描述" value={query} onChange={e => setQuery(e.target.value)} />{query && <button aria-label="清空商品搜索" onClick={() => setQuery("")}><X size={15} /></button>}</label><label className="shop-sort"><SlidersHorizontal size={16} /><select aria-label="商品排序" value={sort} onChange={e => setSort(e.target.value)}><option value="default">默认排序</option><option value="price-up">价格从低到高</option><option value="price-down">价格从高到低</option></select></label></div>
      <div className="commerce-tabs" aria-label="商品分类"><button className={!category ? "active" : ""} aria-pressed={!category} onClick={() => setCategory("")}>全部商品<span>{loading ? "—" : products.length}</span></button>{categories.map(c => <button key={c} className={category === c ? "active" : ""} aria-pressed={category === c} onClick={() => setCategory(c)}>{c}</button>)}</div>
    </section>
    <div className="catalog-result-line"><h2>{category || "全部商品"}</h2><span>共 {loading ? "—" : visible.length} 件商品</span></div>
    {loading ? <CommerceEmpty title="正在加载商城" description="好物马上呈现…" loading /> : <div className="product-grid">{visible.map(p => <article className="product-card panel" key={p.id}><div className="product-media"><ProductVisual image={p.image} name={p.name} category={p.category} />{!p.stock && <span className="commerce-status cancelled">暂时缺货</span>}</div><div className="product-copy"><small className="product-category">{p.category}</small><h2>{p.name}</h2><p title={productDisplayDescription(p.description)}>{productDisplayDescription(p.description)}</p><div className="product-price"><strong><small>¥</small> {money(p.price)}</strong><span className={!p.stock ? "stock-empty" : ""}>库存 <b>{p.stock}</b></span></div><button className="commerce-button" disabled={!p.stock} onClick={() => { setSelected(p); setQuantity(1); setRequestKey(crypto.randomUUID()); setError(""); }}><ShoppingBag size={16} />{p.stock ? "查看并下单" : "暂时缺货"}{Boolean(p.stock) && <ArrowRight size={16} />}</button></div></article>)}</div>}
    {!loading && !visible.length && <CommerceEmpty title={products.length ? "没有找到心仪的商品？" : "商品正在准备中"} description={products.length ? "换个关键词，或者让智能客服帮您查找。" : "商户上架商品后，您就能在这里浏览和下单。"} action={products.length ? <button className="commerce-button" onClick={() => { setCategory(""); setQuery(""); }}>查看全部商品</button> : <Link href="/customer" className="commerce-button">咨询智能客服</Link>} />}
    <p className="commerce-footnote commerce-disclaimer"><ShieldCheck size={16} />本商城不产生真实交易、扣款或物流配送。</p>
    {selected && <CommerceDialog title="确认商品与收货信息" busy={busy} onClose={() => setSelected(null)} onSubmit={buy}>
      <div className="purchase-preview"><ProductVisual image={selected.image} name={selected.name} category={selected.category} compact /><div><small>{selected.category}</small><h3>{selected.name}</h3><strong>¥ {money(selected.price)}</strong><span>库存 {selected.stock} 件</span></div></div>
      <p className="purchase-description">{productDisplayDescription(selected.description)}</p>
      <label>购买数量<input type="number" min={1} max={Math.min(100, selected.stock)} required value={quantity} onChange={e => setQuantity(Number(e.target.value))} /></label>
      <label>收货地址<input required minLength={2} maxLength={500} value={address} onChange={e => setAddress(e.target.value)} placeholder="请输入收货地址" /></label>
      <div className="purchase-total"><span>订单合计</span><strong>¥ {money(selected.price * quantity)}</strong></div>
      <p className="purchase-note"><ShieldCheck size={15} />本次操作不会产生真实扣款。</p>
      {error && <p className="customer-error" role="alert">{error}</p>}
      <div className="commerce-dialog-actions"><button type="button" className="commerce-secondary" disabled={busy} onClick={() => setSelected(null)}>取消</button><button className="commerce-button" disabled={busy || quantity > selected.stock || quantity < 1 || !Number.isInteger(quantity)} type="submit">{busy ? "下单中…" : "确认下单"}<ArrowRight size={16} /></button></div>
    </CommerceDialog>}
  </div></div>;
}
