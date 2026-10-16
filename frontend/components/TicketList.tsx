"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ArrowRight, CheckCircle2, Clock3, Headset, LoaderCircle, MessageCircle, Search } from "lucide-react";
import { api } from "@/lib/api";
import type { TicketSummary } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { categoryLabel, priorityLabel, statusLabel } from "@/components/Dashboard";
import { parseUtc } from "@/lib/time";
import { CommerceStats } from "@/components/CommerceUI";


const filters = [
  { value: "all", label: "全部" },
  { value: "new", label: "待处理" },
  { value: "processing", label: "处理中" },
  { value: "resolved", label: "已解决" },
];

export function TicketList({ mode = "after_sales" }: { mode?: "after_sales" | "chats" }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [tickets, setTickets] = useState<TicketSummary[]>([]);
  const [query, setQuery] = useState("");
  const [priority, setPriority] = useState("all");
  const [sort, setSort] = useState("latest");
  const [loadedFilter, setLoadedFilter] = useState<string | null>(null);
  const [error, setError] = useState("");
  const requestedStatus = searchParams.get("status") ?? "all";
  const validStatuses = [...filters.map((filter) => filter.value), "pending_approval"];
  const active = validStatuses.includes(requestedStatus) ? requestedStatus : "all";
  const basePath = mode === "chats" ? "/chats" : "/tickets";

  useEffect(() => {
    let cancelled = false;
    api.tickets(active, mode === "chats" ? "all" : "after_sales").then((ticketList) => {
      if (cancelled) return;
      setTickets(ticketList);
      setError("");
      setLoadedFilter(active);
    }).catch((failure: Error) => {
      if (cancelled) return;
      setTickets([]);
      setError(failure.message || "加载对话失败，请刷新重试");
      setLoadedFilter(active);
    });
    return () => { cancelled = true; };
  }, [active, mode]);

  const loading = loadedFilter !== active;
  const filtered = useMemo(() => tickets.filter((ticket) => (priority === "all" || ticket.priority === priority) && `${ticket.ticket_no}${ticket.legacy_ticket_no ?? ""}${ticket.customer_name}${ticket.subject}${ticket.order_id ?? ""}`.toLowerCase().includes(query.trim().toLowerCase())).sort((a,b) => (sort === "latest" ? -1 : 1) * (parseUtc(a.updated_at).getTime() - parseUtc(b.updated_at).getTime())), [tickets, query, priority, sort]);

  return <div className="page-wrap">
    <PageHeader title={mode === "chats" ? "聊天管理" : "售后处理"} description={mode === "chats" ? "查看客户全部对话，跟进商品咨询与普通转人工问题；售后对话会明确标记。" : "仅处理退款、退换货、商品损坏和订单异常等售后诉求，普通商品咨询不进入此列表。"} />
    <CommerceStats loading={loading} items={[
      {label:"当前列表",value:tickets.length,note:"当前状态下的对话数量",icon:MessageCircle},
      {label:"待处理",value:tickets.filter(t=>t.status === "new").length,note:"当前列表中尚未处理",icon:Clock3,tone:"blue"},
      {label:"待人工处理",value:tickets.filter(t=>t.status === "pending_approval").length,note:"当前列表中待人工接管",icon:Headset,tone:"orange"},
      {label:"已解决",value:tickets.filter(t=>t.status === "resolved").length,note:"已获客户确认解决",icon:CheckCircle2,tone:"teal"},
    ]} />
    <section className="ticket-toolbar panel">
      <div className="segmented-control">{[...filters, {value:"pending_approval",label:"待人工处理"}].map((filter) => <button className={active === filter.value ? "active" : ""} key={filter.value} onClick={() => router.push(filter.value === "all" ? basePath : `${basePath}?status=${filter.value}`)}>{filter.label}</button>)}</div>
      <div className="toolbar-right"><label className="search-field"><Search size={17} /><input aria-label="搜索对话" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索工单、客户或问题" /></label><select aria-label="对话优先级" value={priority} onChange={e=>setPriority(e.target.value)}><option value="all">全部优先级</option>{["urgent","high","medium","low"].map(p=><option value={p} key={p}>{priorityLabel(p)}</option>)}</select><select aria-label="对话排序" value={sort} onChange={e=>setSort(e.target.value)}><option value="latest">最近更新</option><option value="oldest">最早更新</option></select></div>
    </section>

    <section className="ticket-list panel">
      <div className="list-header"><span>对话信息</span><span>客户 / 渠道</span><span>Agent 识别</span><span>优先级</span><span>状态</span><span>更新时间</span><span /></div>
      {loading ? <div className="inline-loading"><LoaderCircle className="spin" />加载对话…</div> : filtered.map((ticket) => <Link href={`${mode === "chats" ? "/chats" : "/tickets"}/${ticket.id}`} className="list-row" key={ticket.id}>
        <div className="ticket-main"><span>{ticket.ticket_no} · {ticket.case_type === "after_sales" ? "售后" : "普通咨询"}</span><strong>{ticket.subject}</strong><small>{ticket.order_id ?? "无关联订单"}</small></div>
        <div className="customer-cell"><div className="mini-avatar">{ticket.customer_name.slice(0, 1)}</div><div><strong>{ticket.customer_name}</strong><span>{ticket.customer_tier} · {ticket.channel}</span></div></div>
        <div><strong>{ticket.category ? categoryLabel(ticket.category) : "待分析"}</strong><span className="confidence">{ticket.confidence ? `${Math.round(ticket.confidence * 100)}% 置信度` : "—"}</span></div>
        <span><i className={`priority-dot ${ticket.priority}`} />{priorityLabel(ticket.priority)}</span>
        <span className={`status-badge ${ticket.status}`}>{statusLabel(ticket.status)}</span>
        <span className="muted">{formatRelative(ticket.updated_at)}</span>
        <ArrowRight size={17} className="row-arrow" />
      </Link>)}
      {!loading && error && <div className="customer-error" role="alert">{error}</div>}
      {!loading && !error && filtered.length === 0 && <div className="empty-list">{mode === "chats" ? "没有符合条件的对话" : "暂无符合条件的售后问题"}</div>}
    </section>
  </div>;
}

function formatRelative(value: string) {
  const minutes = Math.max(1, Math.round((Date.now() - parseUtc(value).getTime()) / 60000));
  return minutes < 60 ? `${minutes} 分钟前` : `${Math.round(minutes / 60)} 小时前`;
}
