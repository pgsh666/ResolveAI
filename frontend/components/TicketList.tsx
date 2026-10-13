"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Filter, LoaderCircle, Search, SlidersHorizontal } from "lucide-react";
import { api } from "@/lib/api";
import type { TicketSummary } from "@/lib/types";
import { PageHeader } from "@/components/PageHeader";
import { categoryLabel, priorityLabel, statusLabel } from "@/components/Dashboard";


const filters = [
  { value: "all", label: "全部" },
  { value: "new", label: "待处理" },
  { value: "processing", label: "处理中" },
  { value: "resolved", label: "已解决" },
];

export function TicketList() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [tickets, setTickets] = useState<TicketSummary[]>([]);
  const [query, setQuery] = useState("");
  const [loadedFilter, setLoadedFilter] = useState<string | null>(null);
  const requestedStatus = searchParams.get("status") ?? "all";
  const validStatuses = [...filters.map((filter) => filter.value), "pending_approval"];
  const active = validStatuses.includes(requestedStatus) ? requestedStatus : "all";

  useEffect(() => {
    let cancelled = false;
    api.tickets(active).then((ticketList) => {
      if (cancelled) return;
      setTickets(ticketList);
      setLoadedFilter(active);
    });
    return () => { cancelled = true; };
  }, [active]);

  const loading = loadedFilter !== active;
  const filtered = useMemo(() => tickets.filter((ticket) => `${ticket.ticket_no}${ticket.customer_name}${ticket.subject}`.toLowerCase().includes(query.toLowerCase())), [tickets, query]);

  return <div className="page-wrap">
    <PageHeader title="工单中心" description="由 Agent 完成理解、检索、判断与建议，您只处理需要决策的节点。" />
    <section className="ticket-toolbar panel">
      <div className="segmented-control">{filters.map((filter) => <button className={active === filter.value ? "active" : ""} key={filter.value} onClick={() => router.push(filter.value === "all" ? "/tickets" : `/tickets?status=${filter.value}`)}>{filter.label}</button>)}</div>
      <div className="toolbar-right"><label className="search-field"><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索工单、客户或问题" /></label><button className="outline-button"><Filter size={16} />筛选</button><button className="icon-button"><SlidersHorizontal size={17} /></button></div>
    </section>

    <section className="ticket-list panel">
      <div className="list-header"><span>工单信息</span><span>客户 / 渠道</span><span>Agent 识别</span><span>优先级</span><span>状态</span><span>更新时间</span><span /></div>
      {loading ? <div className="inline-loading"><LoaderCircle className="spin" />加载工单…</div> : filtered.map((ticket) => <Link href={`/tickets/${ticket.id}`} className="list-row" key={ticket.id}>
        <div className="ticket-main"><span>{ticket.ticket_no}</span><strong>{ticket.subject}</strong><small>{ticket.order_id ?? "无关联订单"}</small></div>
        <div className="customer-cell"><div className="mini-avatar">{ticket.customer_name.slice(0, 1)}</div><div><strong>{ticket.customer_name}</strong><span>{ticket.customer_tier} · {ticket.channel}</span></div></div>
        <div><strong>{ticket.category ? categoryLabel(ticket.category) : "待分析"}</strong><span className="confidence">{ticket.confidence ? `${Math.round(ticket.confidence * 100)}% 置信度` : "—"}</span></div>
        <span><i className={`priority-dot ${ticket.priority}`} />{priorityLabel(ticket.priority)}</span>
        <span className={`status-badge ${ticket.status}`}>{statusLabel(ticket.status)}</span>
        <span className="muted">{formatRelative(ticket.updated_at)}</span>
        <ArrowRight size={17} className="row-arrow" />
      </Link>)}
      {!loading && filtered.length === 0 && <div className="empty-list">没有符合条件的工单</div>}
    </section>
  </div>;
}

function formatRelative(value: string) {
  const minutes = Math.max(1, Math.round((Date.now() - new Date(value).getTime()) / 60000));
  return minutes < 60 ? `${minutes} 分钟前` : `${Math.round(minutes / 60)} 小时前`;
}
