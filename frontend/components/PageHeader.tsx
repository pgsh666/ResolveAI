"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { Bell, CheckCircle2, Search, X } from "lucide-react";
import { api } from "@/lib/api";
import type { HealthData, TicketSummary } from "@/lib/types";


type OpenPanel = "search" | "notifications" | null;

export function PageHeader({ title, description }: { title: string; description: string }) {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [tickets, setTickets] = useState<TicketSummary[]>([]);
  const [openPanel, setOpenPanel] = useState<OpenPanel>(null);
  const [query, setQuery] = useState("");
  const [ticketsLoading, setTicketsLoading] = useState(true);
  const actionsRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => undefined);
    api.tickets()
      .then(setTickets)
      .catch(() => setTickets([]))
      .finally(() => setTicketsLoading(false));
  }, []);

  useEffect(() => {
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (actionsRef.current && !actionsRef.current.contains(event.target as Node)) {
        setOpenPanel(null);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpenPanel(null);
    };

    document.addEventListener("mousedown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, []);

  useEffect(() => {
    if (openPanel === "search") searchInputRef.current?.focus();
  }, [openPanel]);

  const provider = !health
    ? "待获取"
    : health.agent_mode === "deepseek"
      ? "DeepSeek"
      : health.agent_mode === "openai"
        ? "OpenAI"
        : "演示模式";

  const pendingTickets = useMemo(
    () => tickets.filter((ticket) => ticket.status === "pending_approval"),
    [tickets],
  );
  const searchResults = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    if (!keyword) return tickets.slice(0, 5);
    return tickets.filter((ticket) => (
      `${ticket.ticket_no} ${ticket.customer_name} ${ticket.subject} ${ticket.order_id ?? ""}`
        .toLowerCase()
        .includes(keyword)
    )).slice(0, 6);
  }, [query, tickets]);

  const togglePanel = (panel: Exclude<OpenPanel, null>) => {
    setOpenPanel((current) => current === panel ? null : panel);
  };

  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        <p className="page-description">{description}</p>
      </div>
      <div className="header-actions" ref={actionsRef}>
        <button
          type="button"
          className={`icon-button ${openPanel === "search" ? "active" : ""}`}
          aria-label="搜索工单"
          aria-expanded={openPanel === "search"}
          aria-controls="global-search-panel"
          onClick={() => togglePanel("search")}
        >
          <Search size={18} />
        </button>
        <button
          type="button"
          className={`icon-button notification ${openPanel === "notifications" ? "active" : ""}`}
          aria-label={`通知，${pendingTickets.length} 条待处理`}
          aria-expanded={openPanel === "notifications"}
          aria-controls="notification-panel"
          onClick={() => togglePanel("notifications")}
        >
          <Bell size={18} />
          {pendingTickets.length > 0 && <i />}
        </button>
        <div className="date-chip">
          <span>{!health ? "MCP状态待获取" : health.mcp_enabled ? "MCP已启用" : "MCP未启用"}</span>
          <strong>当前模型：{provider}</strong>
        </div>

        {openPanel === "search" && (
          <section className="header-popover search-popover" id="global-search-panel" aria-label="全局工单搜索">
            <div className="popover-title">
              <div><strong>搜索工单</strong><span>按工单号、客户、订单或问题查找</span></div>
              <button type="button" aria-label="关闭搜索" onClick={() => setOpenPanel(null)}><X size={15} /></button>
            </div>
            <label className="global-search-field">
              <Search size={17} />
              <input
                ref={searchInputRef}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="例如 TKT-1048 或 ORD-7642"
              />
              {query && <button type="button" aria-label="清空搜索" onClick={() => setQuery("")}><X size={14} /></button>}
            </label>
            <div className="popover-list">
              <span className="popover-section-label">{query ? `搜索结果 · ${searchResults.length}` : "最近工单"}</span>
              {ticketsLoading && <div className="popover-empty">正在加载工单…</div>}
              {!ticketsLoading && searchResults.map((ticket) => (
                <Link className="popover-ticket" href={`/tickets/${ticket.id}`} key={ticket.id} onClick={() => setOpenPanel(null)}>
                  <div><strong>{ticket.ticket_no}</strong><span>{ticket.subject}</span></div>
                  <small>{ticket.customer_name}</small>
                </Link>
              ))}
              {!ticketsLoading && searchResults.length === 0 && <div className="popover-empty">没有找到匹配的工单</div>}
            </div>
            <Link className="popover-footer-link" href="/tickets" onClick={() => setOpenPanel(null)}>进入工单中心</Link>
          </section>
        )}

        {openPanel === "notifications" && (
          <section className="header-popover notification-popover" id="notification-panel" aria-label="通知列表">
            <div className="popover-title">
              <div><strong>待办通知</strong><span>{pendingTickets.length > 0 ? `${pendingTickets.length} 个动作需要人工决策` : "当前没有待处理事项"}</span></div>
              <button type="button" aria-label="关闭通知" onClick={() => setOpenPanel(null)}><X size={15} /></button>
            </div>
            <div className="popover-list">
              {ticketsLoading && <div className="popover-empty">正在加载通知…</div>}
              {!ticketsLoading && pendingTickets.map((ticket) => (
                <Link className="notification-item" href={`/tickets/${ticket.id}`} key={ticket.id} onClick={() => setOpenPanel(null)}>
                  <span className="notification-item-icon"><Bell size={15} /></span>
                  <div><strong>{ticket.ticket_no} 等待审批</strong><span>{ticket.subject}</span><small>{ticket.customer_name}{ticket.amount ? ` · ¥${ticket.amount}` : ""}</small></div>
                </Link>
              ))}
              {!ticketsLoading && pendingTickets.length === 0 && (
                <div className="notification-clear"><CheckCircle2 size={25} /><strong>待办已清空</strong><span>新的审批请求会显示在这里</span></div>
              )}
            </div>
            <Link className="popover-footer-link" href="/tickets?status=pending_approval" onClick={() => setOpenPanel(null)}>查看全部待审批工单</Link>
          </section>
        )}
      </div>
    </header>
  );
}
