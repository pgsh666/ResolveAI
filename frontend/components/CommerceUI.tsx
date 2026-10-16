"use client";

import { useEffect, useId, useRef, useState, type FormEventHandler, type ReactNode } from "react";
import { Box, Coffee, Headphones, Keyboard, LoaderCircle, Package, SearchX, Shirt, Smartphone, X, type LucideIcon } from "lucide-react";

export const money = (value: number) => value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function CommerceStats({ items, loading = false }: { items: { label: string; value: number | string; note: string; icon: LucideIcon; tone?: string }[]; loading?: boolean }) {
  return <section className="commerce-stats" aria-label="数据概览">{items.map(({ label, value, note, icon: Icon, tone = "purple" }) => <div className={`commerce-stat ${tone}`} key={label}>
    <div className="commerce-stat-copy"><span>{label}</span><strong>{loading ? "—" : value}</strong><small>{note}</small></div><div className="commerce-stat-icon"><Icon size={22} strokeWidth={1.7} aria-hidden="true" /></div>
  </div>)}</section>;
}

/** Product images are merchant-uploaded data URLs, not remote optimized assets. */
export function ProductVisual({ image, name, category = "", compact = false }: { image?: string; name: string; category?: string; compact?: boolean }) {
  const [failedImage, setFailedImage] = useState("");
  const label = `${name} ${category}`;
  const Icon = /耳机|音箱|音频/.test(label) ? Headphones : /键盘|鼠标|办公/.test(label) ? Keyboard : /杯|饮水/.test(label) ? Coffee : /服|衣|鞋|包/.test(label) ? Shirt : /手机|数码/.test(label) ? Smartphone : Package;
  return <div className={`product-visual${compact ? " compact" : ""}`}>
    {image && failedImage !== image ? <img src={image} alt={name} loading="lazy" onError={() => setFailedImage(image)} /> : <div className="product-visual-placeholder"><Icon size={compact ? 30 : 60} strokeWidth={1.25} aria-hidden="true" />{!compact && <span>暂无商品图片</span>}</div>}
  </div>;
}

export function CommerceEmpty({ title, description, action, loading = false }: { title: string; description?: string; action?: ReactNode; loading?: boolean }) {
  return <div className="panel commerce-empty" role={loading ? "status" : undefined}><div className="commerce-empty-icon">{loading ? <LoaderCircle size={27} className="spin" /> : action ? <Box size={27} /> : <SearchX size={27} />}</div><h2>{title}</h2>{description && <p>{description}</p>}{action}</div>;
}

export function CommerceDialog({ title, busy, onClose, onSubmit, children }: { title: string; busy: boolean; onClose: () => void; onSubmit: FormEventHandler<HTMLFormElement>; children: ReactNode }) {
  const titleId = useId();
  const formRef = useRef<HTMLFormElement>(null);
  const closeRef = useRef(onClose);
  const busyRef = useRef(busy);
  useEffect(() => { closeRef.current = onClose; busyRef.current = busy; });
  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusable = () => Array.from(formRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href]') ?? []).filter(node => !node.hidden && node.getClientRects().length);
    const frame = requestAnimationFrame(() => formRef.current?.focus());
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busyRef.current) closeRef.current();
      if (event.key !== "Tab") return;
      const elements = focusable();
      const first = elements[0], last = elements[elements.length - 1];
      if (!first) { event.preventDefault(); return; }
      if (event.shiftKey && (document.activeElement === first || document.activeElement === formRef.current)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === formRef.current)) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { cancelAnimationFrame(frame); document.removeEventListener("keydown", onKey); document.body.style.overflow = previousOverflow; previousFocus?.focus(); };
  }, []);
  return <div className="document-modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !busy) onClose(); }}><form ref={formRef} tabIndex={-1} className="commerce-dialog panel" role="dialog" aria-modal="true" aria-labelledby={titleId} aria-busy={busy} onSubmit={onSubmit}>
    <div className="commerce-dialog-head"><h2 id={titleId}>{title}</h2><button type="button" className="dialog-close" aria-label="关闭弹窗" disabled={busy} onClick={onClose}><X size={19} /></button></div>{children}
  </form></div>;
}
