"use client";

import { useEffect, useState, type FormEvent } from "react";
import { AlertTriangle, ArrowUpRight, Boxes, CheckCircle2, ImagePlus, Package, Pencil, Plus, Search, X } from "lucide-react";
import { api } from "@/lib/api";
import type { Product, ProductInput } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { useAuth } from "@/components/AuthProvider";
import { productDisplayDescription } from "@/lib/presentation";
import { CommerceDialog, CommerceEmpty, CommerceStats, ProductVisual, money } from "@/components/CommerceUI";

const blank: ProductInput = { name: "", description: "", image: "", price: 1, stock: 0, category: "其他", active: true };
export function ProductManager() {
  const { user } = useAuth();
  const [products, setProducts] = useState<Product[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<ProductInput>(blank);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const canEdit = user?.role === "admin";
  useEffect(() => { api.products().then(setProducts).catch(e => setError(e.message)).finally(() => setLoading(false)); }, []);
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { await api.saveProduct(form, editing ?? undefined); setProducts(await api.products()); setOpen(false); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function upload(file?: File) {
    if (!file) return;
    if (file.size > 2 * 1024 * 1024 || !["image/png", "image/jpeg", "image/webp"].includes(file.type)) { setError("请选择不超过 2MB 的 PNG、JPEG 或 WebP 图片"); return; }
    try {
      const data = await new Promise<string>((resolve, reject) => { const r = new FileReader(); r.onload = () => resolve(String(r.result)); r.onerror = () => reject(new Error("图片读取失败，请重新选择")); r.readAsDataURL(file); });
      setForm(f => ({ ...f, image: data })); setError("");
    } catch (e) { setError((e as Error).message); }
  }
  function createProduct() { setEditing(null); setForm(blank); setOpen(true); setError(""); }
  const keyword = query.trim().toLowerCase();
  const matches = (p: Product) => filter === "all" || (filter === "active" ? p.active : filter === "inactive" ? !p.active : p.active && p.stock === 0);
  const visible = products.filter(p => matches(p) && `${p.name} ${p.category} ${p.description}`.toLowerCase().includes(keyword));
  return <div className="page-wrap feature-page products-page">
    <PageHeader title="商品管理" description="让商品信息更完整，让每一笔交易有据可查。" />
    <CommerceStats loading={loading} items={[
      { label: "全部商品", value: products.length, note: "已录入商城的商品", icon: Package },
      { label: "已上架", value: products.filter(p => p.active).length, note: "客户可在商城浏览", icon: CheckCircle2, tone: "teal" },
      { label: "可售库存", value: products.filter(p => p.active).reduce((n, p) => n + p.stock, 0), note: "当前上架商品库存合计", icon: Boxes, tone: "blue" },
      { label: "上架缺货", value: products.filter(p => p.active && p.stock === 0).length, note: "库存为零，暂不可下单", icon: AlertTriangle, tone: "orange" },
    ]} />
    <section className="panel commerce-control-panel">
      <div className="commerce-control-top"><div><h2>商品目录 <span>{loading ? "—" : products.length}</span></h2><p>下架商品不会影响已有订单</p></div>{canEdit && <button className="commerce-button" onClick={createProduct}><Plus size={17} />上架新商品</button>}</div>
      <div className="commerce-filters"><label className="commerce-search-field"><Search size={18} /><input aria-label="搜索商品管理" placeholder="搜索商品名称、分类或描述" value={query} onChange={e => setQuery(e.target.value)} />{query && <button aria-label="清空商品搜索" onClick={() => setQuery("")}><X size={15} /></button>}</label><select aria-label="商品上架状态" value={filter} onChange={e => setFilter(e.target.value)}><option value="all">全部商品</option><option value="active">已上架</option><option value="inactive">已下架</option><option value="empty">上架缺货</option></select></div>
      <div className="commerce-result-caption">共找到 {loading ? "—" : visible.length} 个商品<span>价格、库存与商城同步</span></div>
    </section>
    {error && !open && <p className="customer-error" role="alert">{error}</p>}
    {loading ? <CommerceEmpty title="正在加载商品" loading /> : <div className="product-grid">{visible.map(p => <article className="product-card panel" key={p.id}>
      <div className="product-media"><ProductVisual image={p.image} name={p.name} category={p.category} /><span className={`commerce-status ${p.active ? p.stock ? "signed" : "overdue" : "cancelled"}`}><i />{p.active ? p.stock ? "已上架" : "上架缺货" : "已下架"}</span></div>
      <div className="product-copy"><small className="product-category">{p.category}</small><h2>{p.name}</h2><p title={productDisplayDescription(p.description)}>{productDisplayDescription(p.description)}</p><div className="product-price"><strong><small>¥</small> {money(p.price)}</strong><span className={!p.stock ? "stock-empty" : ""}>可售库存 <b>{p.stock}</b></span></div>{canEdit && <button className="product-edit-button" onClick={() => { setEditing(p.id); setForm({ name: p.name, description: p.description, image: p.image, price: p.price, stock: p.stock, category: p.category, active: p.active }); setOpen(true); setError(""); }}><Pencil size={15} />编辑商品<ArrowUpRight size={15} /></button>}</div>
    </article>)}</div>}
    {!loading && !visible.length && <CommerceEmpty title={products.length ? "没有找到匹配的商品" : "开始搭建您的商品目录"} description={products.length ? "调整关键词或上架状态，再试一次。" : "录入商品信息、图片和库存，客户即可在商城浏览和下单。"} action={products.length ? <button className="commerce-button" onClick={() => { setQuery(""); setFilter("all"); }}>重置筛选</button> : canEdit ? <button className="commerce-button" onClick={createProduct}><Plus size={17} />上架新商品</button> : undefined} />}
    {open && <CommerceDialog title={editing ? "编辑商品" : "上架商品"} busy={busy} onClose={() => setOpen(false)} onSubmit={save}>
      <p className="commerce-dialog-intro">完善商品信息，保存后同步至商城。</p>
      <div className="commerce-form-columns"><label>商品名称<input required maxLength={120} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></label><label>分类<input required maxLength={50} value={form.category} onChange={e => setForm({ ...form, category: e.target.value })} /></label></div>
      <label>商品描述<textarea required maxLength={4000} rows={3} value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} /></label>
      <div className="commerce-form-columns"><label>售价（元）<input type="number" required min={0.01} max={1000000} step="0.01" value={form.price} onChange={e => setForm({ ...form, price: Number(e.target.value) })} /></label><label>可售库存<input type="number" required min={0} max={1000000} step={1} value={form.stock} onChange={e => setForm({ ...form, stock: Number(e.target.value) })} /></label></div>
      <div className="product-upload"><div>{form.image ? <img className="product-preview" src={form.image} alt="商品图片预览" /> : <ImagePlus size={30} strokeWidth={1.4} />}</div><label>商品图片（最多 2MB）<input type="file" accept="image/png,image/jpeg,image/webp" onChange={e => void upload(e.target.files?.[0])} /><small>支持 PNG、JPEG、WebP</small></label></div>
      <label className="commerce-check"><input type="checkbox" checked={form.active} onChange={e => setForm({ ...form, active: e.target.checked })} />上架销售</label>
      {error && <p className="customer-error" role="alert">{error}</p>}<div className="commerce-dialog-actions"><button type="button" className="commerce-secondary" disabled={busy} onClick={() => setOpen(false)}>取消</button><button type="submit" className="commerce-button" disabled={busy}>{busy ? "保存中…" : "保存商品"}</button></div>
    </CommerceDialog>}
  </div>;
}
